# storage.py
"""
Storage layer for Lexsy SAFE Filler API (robust Mongo fallback).

- Default: Filesystem JSON sessions under DATA_DIR/sessions/<sid>/session.json
- If MONGO_URL is set and reachable: session metadata in MongoDB (Motor), files remain on local disk.
- If Mongo is set but unreachable:
    * If ALLOW_MONGO_FALLBACK=1 -> auto-fallback to FileStore with a console warning.
    * Else -> raise HTTP 503 on first DB operation.

Env:
  DATA_DIR=./data
  MONGO_URL=mongodb://user:pass@host:27017/?authSource=admin
  MONGO_DB=lexsy_safe
  MONGO_COLLECTION=sessions
  RETENTION_DAYS=3
  ALLOW_MONGO_FALLBACK=1
"""
import os, json, uuid, asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

DATA_DIR = os.environ.get("DATA_DIR", "./data")
SESS_DIR = os.path.join(DATA_DIR, "sessions")
os.makedirs(SESS_DIR, exist_ok=True)

def _log(msg: str):
    print(f"[storage] {msg}")

# ---------- Base Interface ----------
class SessionStore:
    async def create_session(self) -> str: ...
    async def load_session(self, sid: str) -> Dict[str, Any]: ...
    async def save_session(self, sid: str, data: Dict[str, Any]) -> None: ...
    def session_dir(self, sid: str) -> str: ...
    async def save_file(self, sid: str, filename: str, content: bytes) -> str: ...

# ---------- Filesystem Store ----------
class FileStore(SessionStore):
    def session_dir(self, sid: str) -> str:
        d = os.path.join(SESS_DIR, sid)
        os.makedirs(d, exist_ok=True)
        return d

    async def create_session(self) -> str:
        sid = str(uuid.uuid4())
        d = self.session_dir(sid)
        data = {
            "id": sid,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "placeholders": [],
            "files": {"original_path": None, "filled_path": None, "preview_html": None},
            "mapping": {},
        }
        with open(os.path.join(d, "session.json"), "w") as f:
            json.dump(data, f, indent=2)
        _log(f"Created FS session {sid}")
        return sid

    async def load_session(self, sid: str) -> Dict[str, Any]:
        p = os.path.join(self.session_dir(sid), "session.json")
        if not os.path.exists(p):
            raise FileNotFoundError("Session not found")
        with open(p, "r") as f:
            return json.load(f)

    async def save_session(self, sid: str, data: Dict[str, Any]) -> None:
        p = os.path.join(self.session_dir(sid), "session.json")
        with open(p, "w") as f:
            json.dump(data, f, indent=2)

    async def save_file(self, sid: str, filename: str, content: bytes) -> str:
        d = self.session_dir(sid)
        path = os.path.join(d, filename)
        with open(path, "wb") as out:
            out.write(content)
        return path

# ---------- MongoDB Store (metadata only) ----------
class MongoStore(SessionStore):
    def __init__(self, client, db_name: str, coll_name: str, allow_fallback: bool = True):
        self.client = client
        self.db = client[db_name]
        self.coll = self.db[coll_name]
        self.allow_fallback = allow_fallback

    async def init(self):
        # actively verify connectivity
        try:
            await self.db.command("ping")
            _log("Mongo ping OK")
        except Exception as e:
            _log(f"Mongo ping failed: {e!r}")
            if self.allow_fallback:
                raise ConnectionError("Mongo unreachable")
            else:
                # Will raise later on first operation
                pass

        # TTL index on expireAt if present
        try:
            await self.coll.create_index("expireAt", expireAfterSeconds=0)
            await self.coll.create_index("created_at")
        except Exception as e:
            _log(f"Index creation error (non-fatal): {e!r}")

    def session_dir(self, sid: str) -> str:
        d = os.path.join(SESS_DIR, sid)
        os.makedirs(d, exist_ok=True)
        return d

    async def create_session(self) -> str:
        sid = str(uuid.uuid4())
        doc = {
            "_id": sid,
            "created_at": datetime.utcnow(),
            "placeholders": [],
            "files": {"original_path": None, "filled_path": None, "preview_html": None},
            "mapping": {},
        }
        retention = os.environ.get("RETENTION_DAYS")
        if retention:
            try:
                days = int(retention)
                doc["expireAt"] = datetime.utcnow() + timedelta(days=days)
            except Exception:
                pass
        try:
            await self.coll.insert_one(doc)
            _log(f"Created Mongo session {sid}")
        except Exception as e:
            _log(f"Mongo insert failed: {e!r}")
            if self.allow_fallback:
                raise ConnectionError("Mongo unreachable")
            raise
        return sid

    async def load_session(self, sid: str) -> Dict[str, Any]:
        try:
            doc = await self.coll.find_one({"_id": sid})
        except Exception as e:
            _log(f"Mongo find_one failed: {e!r}")
            if self.allow_fallback:
                raise ConnectionError("Mongo unreachable")
            raise
        if not doc:
            raise FileNotFoundError("Session not found")
        created = doc.get("created_at")
        if hasattr(created, "isoformat"):
            created = created.isoformat() + "Z"
        return {
            "id": doc["_id"],
            "created_at": created,
            "placeholders": doc.get("placeholders", []),
            "files": doc.get("files", {}),
            "mapping": doc.get("mapping", {}),
        }

    async def save_session(self, sid: str, data: Dict[str, Any]) -> None:
        update_doc = {
            "placeholders": data.get("placeholders", []),
            "files": data.get("files", {}),
            "mapping": data.get("mapping", {}),
        }
        try:
            await self.coll.update_one({"_id": sid}, {"$set": update_doc}, upsert=True)
        except Exception as e:
            _log(f"Mongo update failed: {e!r}")
            if self.allow_fallback:
                raise ConnectionError("Mongo unreachable")
            raise

    async def save_file(self, sid: str, filename: str, content: bytes) -> str:
        d = self.session_dir(sid)
        path = os.path.join(d, filename)
        with open(path, "wb") as out:
            out.write(content)
        return path

# ---------- Factory with proactive fallback ----------
_store_singleton: Optional[SessionStore] = None

async def get_store() -> SessionStore:
    global _store_singleton
    if _store_singleton is not None:
        return _store_singleton

    mongo_url = os.environ.get("MONGO_URL")
    allow_fb = os.environ.get("ALLOW_MONGO_FALLBACK", "1") == "1"

    if mongo_url:
        try:
            from motor.motor_asyncio import AsyncIOMotorClient
            db_name = os.environ.get("MONGO_DB", "lexsy_safe")
            coll_name = os.environ.get("MONGO_COLLECTION", "sessions")
            client = AsyncIOMotorClient(mongo_url, uuidRepresentation="standard")
            mongo_store = MongoStore(client, db_name, coll_name, allow_fallback=allow_fb)
            await mongo_store.init()
            _store_singleton = mongo_store
            _log("Using MongoStore")
            return _store_singleton
        except ConnectionError:
            if allow_fb:
                _log("Falling back to FileStore due to Mongo connectivity.")
                _store_singleton = FileStore()
                return _store_singleton
            else:
                raise
        except Exception as e:
            _log(f"Mongo init error: {e!r}; falling back to FileStore")
            _store_singleton = FileStore()
            return _store_singleton
    else:
        _store_singleton = FileStore()
        _log("Using FileStore (no MONGO_URL set)")
        return _store_singleton
