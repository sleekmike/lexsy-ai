---

# Lexsy SAFE — AI-Assisted SAFE Filler

Minimal, fast web app that:

* accepts a **.docx** SAFE template,
* detects and classifies **placeholders** (e.g., `[Company Name]`, `$[________]`),
* drives a **conversational** fill flow (GPT-4o optional),
* and returns a **filled .docx** preserving formatting (split-run safe).

Deployed:

* **API** (FastAPI): `https://lexsy-safe-api.fly.dev`
* **Web** (Next.js 14): your Fly app (e.g., `https://lexsy-ai.fly.dev`)

> Sample template: *Postmoney SAFE – Valuation Cap Only* (YC form).

---

## Stack

* **Frontend**: Next.js 14 + React + Tailwind (deployed as a Node runtime)
* **Backend**: FastAPI (Python 3.12)
* **LLM (optional)**: OpenAI GPT-4o (header shows `X-Ask-Source: openai`)
* **DB**: MongoDB (sessions + mapping), with local filesystem data directory
* **.docx**: XML-level replacements across `document.xml`, **headers/footers**, footnotes/endnotes; **split-run safe**
* **Deploy**: Fly.io (frontend and API separate), internal DNS Mongo (`<mongo-app>.internal`)

---

## Project structure

```text
/  (repo root)
├─ app/                 # FastAPI backend (Python)
│  ├─ main.py           # API endpoints (/upload, /fill, /ask, /preview, /download)
│  ├─ storage.py        # Session store (FileStore + MongoStore with fallback)
│  ├─ docx_utils.py     # Split-run-safe DOCX placeholder replacer
│  ├─ llm.py            # Optional GPT-4o integration for /ask
│  ├─ requirements.txt  # Backend dependencies
│  ├─ Dockerfile        # Backend container definition
│  ├─ fly.toml          # Fly app config (API)
│  └─ data/sessions/    # Local session data (gitignored)
├─ frontend/            # Next.js 14 + Tailwind web app
│  ├─ app/              # App Router pages
│  │  └─ page.tsx       # Main UI (upload → ask → fill → download)
│  ├─ components/Dropzone.tsx
│  ├─ Dockerfile        # Frontend container definition
│  ├─ fly.toml          # Fly app config (Web)
│  ├─ package.json
│  └─ tailwind.config.ts
├─ mongo/               # Optional Mongo single-instance for Fly
│  ├─ Dockerfile
│  └─ fly.toml
├─ demo-videos/
├─ README.md
└─ sample.docx, lexsy.docx, etc.
```

Key entry points:

- API: `app/main.py`
- Web: `frontend/app/page.tsx`

---

## Features

* **Placeholder detection**: `[ … ]` and `$[________]` (underscores) are parsed and **classified** (name, date, currency, jurisdiction, string) with **occurrence counts**.
* **Split-run replacer**: Replaces placeholders even when Word splits them across runs/tags — works in main doc, **headers**, **footers**, and notes.
* **Conversational fill**: `/ask` returns “next missing field” with a tailored question, examples, and an optional suggestion. If GPT-4o is enabled, you’ll see header `X-Ask-Source: openai`.
* **Currency/date normalization**: Accept inputs like `10m`, `250k`, `USD 5,500,000`, `2025-09-15`, `9/15/2025` → output as **`$10,000,000`** and **`Month DD, YYYY`**.
* **Preview & Download**: Fast **text preview** (for eyeballing) + **streaming .docx download** (preserves formatting).

---

## API (FastAPI)

### Endpoints

* `GET /health` → `"ok"`
* `POST /upload` (`multipart/form-data`) → `{ session_id, placeholders[] }`
* `POST /fill` (JSON) → apply one value → returns updated placeholders
* `POST /ask` (JSON) → returns `{ next, remaining, missing_keys[] }`

  * Header includes `X-Ask-Source: openai` or `deterministic`
* `GET /preview?session_id=…` → HTML text preview (fast)
* `GET /download?session_id=…` → streamed `.docx` with `Content-Disposition: attachment`
* `GET /diag/llm` → `{ openai_enabled, model, ask_use_openai }`

### Run locally

```bash
# from repo root
cd app
# (venv recommended)
pip install -r requirements.txt

export DATA_DIR=./data
export MONGO_URL="mongodb://127.0.0.1:27017"      # or leave unset for file-only
export MONGO_DB="lexsy_safe"
export MONGO_COLLECTION="sessions"
export RETENTION_DAYS=3

# Optional LLM (set these to enable GPT-4o)
export ASK_USE_OPENAI=1
export OPENAI_MODEL="gpt-4o"
export OPENAI_API_KEY="<YOUR_OPENAI_KEY>"

uvicorn main:app --reload --port 8000
```

> **Never commit real keys.** Use environment variables or Fly secrets.

### Quick smoke tests

```bash
# Health
curl -sSf http://127.0.0.1:8000/health

# Upload and capture session_id (Python one-liner, works on macOS)
SID=$(curl -s -F "file=@/path/to/lexsy.docx" http://127.0.0.1:8000/upload \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["session_id"])')
echo "$SID"

# Ask (show headers to confirm GPT-4o is used when enabled)
curl -i -s -X POST http://127.0.0.1:8000/ask \
  -H "Content-Type: application/json" \
  -d "{\"session_id\":\"$SID\"}" | tr -d '\r' | sed -n '1,30p'
# Look for: X-Ask-Source: openai

# Fill examples (currency normalization)
for v in '$10,000,000' '10m' '1.25m' '250000' '250k' 'USD 5,500,000'; do
  curl -s -X POST http://127.0.0.1:8000/fill \
    -H "Content-Type: application/json" \
    -d "{\"session_id\":\"$SID\",\"key\":\"post_money_valuation_cap\",\"value\":\"$v\"}" \
  | python3 -m json.tool | sed -n '1,18p'
done

# Preview
open "http://127.0.0.1:8000/preview?session_id=$SID"

# Download
curl -L -o filled.docx "http://127.0.0.1:8000/download?session_id=$SID"
```

---

## Web (Next.js 14)

* Uses `NEXT_PUBLIC_API_BASE` at **build time** (baked into the client bundle).
* Shows **“All fields are filled 🎉”** only **after** there’s a session with placeholders and none missing.
* Auto-downloads once when everything is filled (plus a big “Download filled .docx” button).

### Dev

```bash
# from repo root
cd frontend
echo 'NEXT_PUBLIC_API_BASE="http://localhost:8000"' > .env.local
npm install
npm run dev
# http://localhost:3000
```

---

## Deploy on Fly.io

### 1) MongoDB (single-volume app)

```bash
# from repo root
cd mongo
fly launch --name lexsy-mongo --region phx --no-deploy

fly volumes create lexsy_mongo --region phx --size 1

fly secrets set -a lexsy-mongo \
  MONGO_INITDB_ROOT_USERNAME="admin" \
  MONGO_INITDB_ROOT_PASSWORD="supersecret123"

fly deploy -a lexsy-mongo
# Mongo will be reachable from other Fly apps via: mongodb://admin:<pass>@lexsy-mongo.internal:27017/?authSource=admin
```

### 2) API

```bash
# from repo root
cd app
fly launch --name lexsy-safe-api --dockerfile Dockerfile --no-deploy

API_APP="lexsy-safe-api"
MONGO_APP="lexsy-mongo"

fly secrets set -a "$API_APP" \
  MONGO_URL="mongodb://admin:supersecret123@${MONGO_APP}.internal:27017/?authSource=admin" \
  MONGO_DB="lexsy_safe" \
  MONGO_COLLECTION="sessions" \
  RETENTION_DAYS="3" \
  ALLOW_MONGO_FALLBACK="1" \
  ASK_USE_OPENAI="1" \
  OPENAI_MODEL="gpt-4o" \
  OPENAI_API_KEY="<YOUR_OPENAI_KEY>"

fly deploy -a "$API_APP"
# Health
curl -sSf https://lexsy-safe-api.fly.dev/health
```

> If you restrict CORS on the API, allow your web origin (e.g., `CORS_ALLOW_ORIGINS="https://lexsy-ai.fly.dev"`).

### 3) Frontend (Next.js, Node runtime)

Dockerfile starts Next binding `-H 0.0.0.0 -p 3000` so Fly health checks pass.

```bash
# First time only
# from repo root
cd frontend
fly launch --name lexsy-ai --copy-config --no-deploy

# Build with API base baked in
fly deploy -a lexsy-ai \
  --build-arg NEXT_PUBLIC_API_BASE="https://lexsy-safe-api.fly.dev"

# Verify
fly status -a lexsy-ai
fly logs -a lexsy-ai | sed -n '1,120p'
```

---

## Endpoint examples (production)

```bash
# Upload + capture SID
SID=$(curl -s -F "file=@/path/to/lexsy.docx" \
  https://lexsy-safe-api.fly.dev/upload | python3 -c 'import sys,json; print(json.load(sys.stdin)["session_id"])')
echo "$SID"

# Ask (shows LLM header)
curl -i -s -X POST https://lexsy-safe-api.fly.dev/ask \
  -H "Content-Type: application/json" \
  -d "{\"session_id\":\"$SID\"}" | tr -d '\r' | sed -n '1,20p'

# Fill
curl -s -X POST https://lexsy-safe-api.fly.dev/fill \
  -H "Content-Type: application/json" \
  -d "{\"session_id\":\"$SID\",\"key\":\"company_name\",\"value\":\"AlphaSoft Technologies LTD\"}"

# Download
curl -L -o filled.docx "https://lexsy-safe-api.fly.dev/download?session_id=$SID"
```

---

## How it works (high-level)

1. **/upload** extracts all text from `word/document.xml`, finds `[…]` and `$[____]` placeholders, de-dupes, classifies, counts occurrences, and initializes a session (`Mongo` if configured).
2. **/fill** stores values in the session mapping and normalizes **currency** and **date**.
3. **/ask** chooses the next missing slot (priority-aware), generates a question/examples/suggestion. If OpenAI is enabled, we refine the prompt with GPT-4o and add `X-Ask-Source: openai`.
4. **/download** runs the **split-run replacer** across **document + headers/footers + notes**, then streams a valid `.docx`.

---

## Troubleshooting

* **Frontend deploy stuck on health check**
  Ensure Next binds to the Fly internal port: use a `CMD` like
  `node node_modules/next/dist/bin/next start -p 3000 -H 0.0.0.0`
  and set `internal_port = 3000` in `fly.toml`.

* **LLM not used**
  Check headers on `/ask` for `X-Ask-Source`. If missing, verify:

  * `ASK_USE_OPENAI=1`, `OPENAI_API_KEY` set
  * API logs don’t show auth errors
  * `/diag/llm` returns `openai_enabled: true`

* **CORS**
  If your browser calls are blocked, set API env:
  `CORS_ALLOW_ORIGINS="https://<your-frontend>.fly.dev"`

* **Mongo can’t connect**
  Use internal DNS: `mongodb://admin:<pass>@lexsy-mongo.internal:27017/?authSource=admin`
  Ensure the Mongo app is **running** and has a volume mounted.
  `fly status -a lexsy-mongo`, `fly logs -a lexsy-mongo`.

---

## Security & hygiene

* **Never commit secrets** (API keys, Mongo creds). Use Fly secrets or local env vars.
* **Data retention** defaults to `RETENTION_DAYS=3`. Adjust as needed.
* Keep CORS tight in production.

---

## Submission checklist

* ✅ Public URL to the web app
* ✅ Public API health endpoint (`/health`)
* ✅ GitHub repo(s) with README and Dockerfiles
* ✅ Short Loom (≤2 min) showing upload → ask → fill → download
* ✅ Optional: attach a filled `.docx` for reviewers

---
