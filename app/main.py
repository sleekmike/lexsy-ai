# main.py (Mongo-enabled + split-run replacer + /ask)
import os, json, zipfile, re, html, uuid
from typing import List, Optional, Dict, Any
from datetime import datetime
from fastapi import FastAPI, UploadFile, File, HTTPException, Body, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, HTMLResponse, FileResponse
from pydantic import BaseModel
import xml.etree.ElementTree as ET

from fastapi.responses import StreamingResponse
from storage import get_store
from docx_utils import replace_placeholders_in_docx
from llm import suggest_question, is_enabled
from decimal import Decimal, InvalidOperation

DATA_DIR = os.environ.get("DATA_DIR", "./data")
DOCX_MAIN = "word/document.xml"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

app = FastAPI(title="Lexsy SAFE Filler API", version="0.3.1")

# CORS (open for MVP; lock down in production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ALLOW_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------
# Models
# -----------------------
class Placeholder(BaseModel):
    key: str
    label: str
    type: str = "string"
    occurrences: int = 1
    value: Optional[str] = None

class UploadResponse(BaseModel):
    session_id: str
    placeholders: List[Placeholder]

class FillRequest(BaseModel):
    session_id: str
    key: str
    value: str

class FillResponse(BaseModel):
    ok: bool
    remaining: int
    placeholders: List[Placeholder]

# --- Normalizers -------------------------------------------------------------

_CURRENCY_WORDS = {
    "k": Decimal("1e3"),
    "thousand": Decimal("1e3"),
    "m": Decimal("1e6"),
    "mn": Decimal("1e6"),
    "million": Decimal("1e6"),
    "b": Decimal("1e9"),
    "bn": Decimal("1e9"),
    "billion": Decimal("1e9"),
    "t": Decimal("1e12"),
    "trillion": Decimal("1e12"),
}

def _fmt_usd(amount: Decimal) -> str:
    q = amount.quantize(Decimal("0.01"))
    if q == q.to_integral():
        return f"${int(q):,}"
    return f"${q:,.2f}"

def normalize_currencyx(value: str) -> str:
    """
    Accepts: $10,000,000, 10m, 10 M, USD 10m, 1.25m, 250k, 250,000
    Returns: pretty USD string (e.g., $10,000,000 or $1,250,000.00)
    If parsing fails, returns original value unchanged.
    """
    if not value:
        return value
    s = value.strip().lower()
    # strip common currency prefixes/symbols and commas
    s = re.sub(r"^\s*(usd|us\$|\$)\s*", "", s, flags=re.I)
    s = s.replace(",", " ")

    # Extract number + optional word/suffix
    m = re.match(r"\s*([0-9]+(?:\.[0-9]+)?)\s*([a-z]+)?\s*$", s)
    if not m:
        # try words like "10 million"
        m = re.match(r"\s*([0-9]+(?:\.[0-9]+)?)\s*(million|billion|thousand|mn|bn|m|b|k|t|trillion)\s*$", s)
    if not m:
        return value  # keep raw if unknown

    num_str = m.group(1)
    word = (m.group(2) or "").lower()

    try:
        base = Decimal(num_str)
    except InvalidOperation:
        return value

    mult = _CURRENCY_WORDS.get(word, Decimal(1))
    amount = base * mult
    return _fmt_usd(amount)


def normalize_currency(value: str) -> str:
    """
    Accepts: $10,000,000, 10m, USD 10m, 1.25m, 250k, 250000, 10 million
    Returns: pretty USD string (e.g., $10,000,000 or $1,250,000.00)
    If parsing fails, returns original value unchanged.
    """
    if not value:
        return value
    s = value.strip().lower()

    # strip common prefixes and separators
    s = re.sub(r"^\s*(usd|us\$|\$)\s*", "", s, flags=re.I)
    s = s.replace(",", "")              # <-- keep digits contiguous
    s = re.sub(r"\s+", " ", s).strip()  # collapse stray spaces

    # number + optional suffix (k,m,mn,b,bn,t)
    m = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*([a-z]+)?$", s)
    if not m:
        # try “10 million”, “10 billion”, etc.
        m = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*(million|billion|thousand|mn|bn|m|b|k|t|trillion)$", s)
    if not m:
        return value

    num_str = m.group(1)
    word = (m.group(2) or "").lower()

    try:
        base = Decimal(num_str)
    except InvalidOperation:
        return value

    mult = _CURRENCY_WORDS.get(word, Decimal(1))
    amount = base * mult
    return _fmt_usd(amount)


# Date normalization -> Month DD, YYYY
_ORDINAL_RE = re.compile(r"(\d+)(st|nd|rd|th)\b", re.I)

def _strip_ordinals(s: str) -> str:
    return _ORDINAL_RE.sub(lambda m: m.group(1), s)

def normalize_date(value: str) -> str:
    """
    Accepts many common formats: 2025-09-15, 9/15/2025, 15 Sep 2025, September 15 2025, etc.
    Returns Month DD, YYYY if parseable; else original string.
    """
    if not value:
        return value
    from datetime import datetime
    s = _strip_ordinals(value.strip())
    TRIES = [
        "%B %d, %Y", "%b %d, %Y",
        "%B %d %Y", "%b %d %Y",
        "%m/%d/%Y", "%Y-%m-%d", "%d-%m-%Y",
        "%d %B %Y", "%d %b %Y",
        "%m/%d/%y", "%d/%m/%Y", "%d/%m/%y",
    ]
    for fmt in TRIES:
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%B %d, %Y")
        except Exception:
            continue
    # last resort: try parsing month name + day + comma optional + year loosely
    m = re.match(r"^\s*([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})\s*$", s)
    if m:
        try:
            dt = datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y")
            return dt.strftime("%B %d, %Y")
        except Exception:
            pass
    return value

# -----------------------
# Regex & helpers
# -----------------------
BRACKET_RE = re.compile(r"\[[^\[\]]+\]")
UNDERSCORE_RE = re.compile(r"\$?\[\s*[_\u2014\-]{3,}\s*\]")

def canonical_key(label: str) -> str:
    s = label.strip("[]$ ").strip()
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", "_", s).lower()
    return s or "value"

def infer_type(label: str) -> str:
    s = label.strip("[]$ ").lower()
    if "date" in s:
        return "date"
    if "cap" in s or "amount" in s or "price" in s or "valuation" in s or re.search(r"[_]{3,}", label):
        return "currency"
    if "state of incorporation" in s or "jurisdiction" in s or "state" in s:
        return "jurisdiction"
    if "email" in s:
        return "email"
    if "address" in s:
        return "address"
    if "name" in s or "investor" in s or "company" in s:
        return "name"
    return "string"

def extract_plaintext_and_placeholders(docx_path: str) -> Dict[str, Any]:
    with zipfile.ZipFile(docx_path) as z:
        xml_data = z.read(DOCX_MAIN)

    ns = {'w': W_NS}
    root = ET.fromstring(xml_data)
    paragraphs = []
    texts_for_detection = []

    # For detection, gather all w:t text
    for t in root.iterfind('.//w:t', namespaces=ns):
        if t.text:
            texts_for_detection.append(t.text)

    # For preview, build paragraphs
    for p in root.iterfind('.//w:p', namespaces=ns):
        parts = []
        for t in p.iterfind('.//w:t', namespaces=ns):
            if t.text:
                parts.append(t.text)
        paragraphs.append("".join(parts))

    joined_text = "\n".join(paragraphs)

    # Detect placeholders
    concat = "".join(texts_for_detection)
    bracket_ph = BRACKET_RE.findall(concat)
    underscore_ph = UNDERSCORE_RE.findall(concat)

    # dedupe while preserving order
    seen = set()
    ordered = []
    for ph in bracket_ph + underscore_ph:
        if ph not in seen:
            ordered.append(ph)
            seen.add(ph)

    return {"text": joined_text, "placeholders": ordered}

def apply_mapping_to_text(text: str, mapping: Dict[str, str]) -> str:
    result = text
    for label, value in mapping.items():
        result = result.replace(label, value)
    return result


# --- Currency helpers ---------------------------------------------------------

def _classify_currency_label_by_shape(label: str) -> str | None:
    """
    Heuristic: in the SAFE, the $-prefixed blank is the Purchase Amount,
    the plain bracket blank is the Valuation Cap.
    Returns 'purchase_amount' | 'post_money_valuation_cap' | None
    """
    s = label.strip()
    if s.startswith("$["):
        return "purchase_amount"
    if s.startswith("[") and not s.startswith("$["):
        return "post_money_valuation_cap"
    return None

def _guess_currency_label_key(doc_text: str, label: str) -> str:
    """
    Improved guess:
      1) Use shape heuristic first (very reliable on SAFE templates)
      2) Otherwise, search ALL occurrences and look for nearby phrases
    """
    # 1) Heuristic by label shape
    shape = _classify_currency_label_by_shape(label)
    if shape:
        return shape

    # 2) Look around each occurrence in the text
    hits = [m.start() for m in re.finditer(re.escape(label), doc_text)]
    for idx in hits:
        win = doc_text[max(0, idx - 200): idx + 200].lower()
        if "post-money valuation cap" in win or "post money valuation cap" in win:
            return "post_money_valuation_cap"
        if "purchase amount" in win:
            return "purchase_amount"
    return "currency"

def _select_currency_placeholder_for_alias(sess: Dict[str, Any], doc_text: str, alias: str) -> Optional[Dict[str, Any]]:
    """
    Deterministically pick the placeholder that corresponds to a semantic alias.
    Prefer unfilled; if both unfilled, choose by shape; fallback to doc-text search.
    """
    curr = [p for p in sess["placeholders"] if p.get("type") == "currency"]

    # Prefer unfilled first
    unfilled = [p for p in curr if not p.get("value")] or curr

    # Try by shape first
    for p in unfilled:
        if _classify_currency_label_by_shape(p["label"]) == alias:
            return p

    # Try by doc-text context
    for p in unfilled:
        if _guess_currency_label_key(doc_text, p["label"]) == alias:
            return p

    # Nothing definitive
    return unfilled[0] if unfilled else None

def _load_doc_text(path: str) -> str:
    with zipfile.ZipFile(path) as z:
        xml_data = z.read(DOCX_MAIN)
    ns = {'w': W_NS}
    root = ET.fromstring(xml_data)
    parts = []
    for p in root.iterfind('.//w:p', namespaces=ns):
        tparts = []
        for t in p.iterfind('.//w:t', namespaces=ns):
            if t.text:
                tparts.append(t.text)
        parts.append("".join(tparts))
    return "\n".join(parts)

def _guess_currency_label_keyx(doc_text: str, label: str) -> str:
    idx = doc_text.find(label)
    if idx == -1:
        return "currency"
    window = doc_text[max(0, idx-160): idx+160].lower()
    if "purchase amount" in window:
        return "purchase_amount"
    if "post-money valuation cap" in window or "post money valuation cap" in window:
        return "post_money_valuation_cap"
    return "currency"

# -----------------------
# API
# -----------------------

@app.get("/health", response_class=PlainTextResponse)
def health() -> str:
    return "ok"

@app.post("/upload", response_model=UploadResponse)
async def upload_doc(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="Only .docx files are supported")

    store = await get_store()
    sid = await store.create_session()

    content = await file.read()
    orig_path = await store.save_file(sid, "original.docx", content)

    # parse & detect
    extracted = extract_plaintext_and_placeholders(orig_path)
    labels = extracted["placeholders"]

    # build placeholder objects with counts
    text_for_counts = extracted["text"]
    placeholders = []
    seen = set()
    for label in labels:
        if label in seen:
            continue
        seen.add(label)
        key = canonical_key(label)
        ptype = infer_type(label)
        count = text_for_counts.count(label) if label in text_for_counts else 1
        placeholders.append({"key": key, "label": label, "type": ptype, "occurrences": count, "value": None})

    # persist session metadata
    sess = await store.load_session(sid)
    sess["placeholders"] = placeholders
    sess["files"]["original_path"] = orig_path
    sess["mapping"] = {}
    await store.save_session(sid, sess)

    return UploadResponse(session_id=sid, placeholders=[Placeholder(**p) for p in placeholders])


@app.post("/fill", response_model=FillResponse)
async def fill_slot(req: FillRequest):
    """Accepts exact placeholder keys as well as semantic aliases for currency fields.

    - Exact match: uses the placeholder with p['key'] == req.key.
    - Currency aliases: if req.key in {'purchase_amount','post_money_valuation_cap'}, we scan the
      document text around each *unfilled* currency label to choose the right one.
      If context is ambiguous but only one currency field remains, we use it as fallback.
    - Special: when filling company_name we also auto-fill [COMPANY] uppercase.
    """
    store = await get_store()
    sess = await store.load_session(req.session_id)

    # 1) Try exact key match first
    ph = None
    for p in sess["placeholders"]:
        if p["key"] == req.key:
            ph = p
            break

    # 2) Try semantic currency alias mapping
    if ph is None and req.key in {"purchase_amount", "post_money_valuation_cap"}:
        try:
            doc_text = _load_doc_text(sess["files"]["original_path"])
        except Exception:
            doc_text = ""
        candidates = [p for p in sess["placeholders"] if not p.get("value") and p.get("type") == "currency"]
        selected = None
        for p in candidates:
            guessed = _guess_currency_label_key(doc_text, p["label"])
            if guessed == req.key:
                selected = p
                break
        if selected is None and len(candidates) == 1:
            selected = candidates[0]
        if selected is not None:
            ph = selected

    if ph is None:
        raise HTTPException(status_code=404, detail=f"Placeholder key '{req.key}' not found")

    # Normalize based on type / semantic key
    raw_val = req.value
    val = raw_val

    # Currency normalization (by type or key alias)
    if ph.get("type") == "currency" or req.key in {"purchase_amount", "post_money_valuation_cap"}:
        val = normalize_currency(raw_val)

    # Date normalization (by type or explicit key)
    if ph.get("type") == "date" or req.key == "date_of_safe":
        val = normalize_date(raw_val)

    # Special: auto-fill [COMPANY] uppercase when filling company_name
    if req.key == "company_name":
        for p in sess["placeholders"]:
            if p["key"] == "company" and not p.get("value"):
                p["value"] = val.upper()
                sess.setdefault("mapping", {})[p["label"]] = p["value"]

    # Persist by label (what docx replacement uses)
    ph["value"] = val
    mapping = sess.get("mapping", {})
    mapping[ph["label"]] = val
    sess["mapping"] = mapping
    await store.save_session(req.session_id, sess)

    remaining = sum(1 for p in sess["placeholders"] if not p.get("value"))
    return FillResponse(ok=True, remaining=remaining, placeholders=[Placeholder(**p) for p in sess["placeholders"]])

'''

@app.post("/fill", response_model=FillResponse)
async def fill_slot(req: FillRequest):
    """
    Accepts exact placeholder keys as well as semantic aliases for currency fields.

    - Exact match: uses the placeholder with p['key'] == req.key.
    - Currency aliases: if req.key in {'purchase_amount','post_money_valuation_cap'},
      scan the doc text around each currency label to pick the right one.
      Prefer unfilled currency placeholders; if ambiguous but only one candidate remains, use it.
    - Special: when filling company_name also auto-fill [COMPANY] (uppercase).
    - Normalizes currency and date inputs into pretty, consistent formats.
    """
    store = await get_store()
    try:
        sess = await store.load_session(req.session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")

    # 1) Try exact key match first.
    ph = next((p for p in sess["placeholders"] if p["key"] == req.key), None)

    # 2) Semantic alias mapping for currency fields (purchase_amount / post_money_valuation_cap).
    currency_aliases = {"purchase_amount", "post_money_valuation_cap"}
    if ph is None and req.key in currency_aliases:
        try:
            doc_text = _load_doc_text(sess["files"]["original_path"])
        except Exception:
            doc_text = ""

        # Consider all currency placeholders; prefer those not yet filled.
        all_currency = [p for p in sess["placeholders"] if p.get("type") == "currency"]
        candidates = [p for p in all_currency if not p.get("value")] or all_currency

        # Choose the one whose surrounding text matches the alias.
        selected = None
        for p in candidates:
            guessed = _guess_currency_label_key(doc_text, p["label"])
            if guessed == req.key:
                selected = p
                break

        # If still ambiguous but only one candidate, pick it.
        if selected is None and len(candidates) == 1:
            selected = candidates[0]

        ph = selected

    if ph is None:
        raise HTTPException(status_code=404, detail=f"Placeholder key '{req.key}' not found")

    # 3) Normalize value according to type / semantic key.
    raw_val = req.value
    val = raw_val

    # Currency normalization (by type OR alias key).
    if ph.get("type") == "currency" or req.key in currency_aliases:
        val = normalize_currency(raw_val)

    # Date normalization (by type OR specific key).
    if ph.get("type") == "date" or req.key == "date_of_safe":
        val = normalize_date(raw_val)

    # 4) Special: when setting company_name, auto-fill [COMPANY] as uppercase if not already set.
    if req.key == "company_name":
        for p in sess["placeholders"]:
            if p["key"] == "company" and not p.get("value"):
                p["value"] = (val or "").upper()
                sess.setdefault("mapping", {})[p["label"]] = p["value"]

    # 5) Persist mapping by *label* (docx replacement uses labels).
    ph["value"] = val
    mapping = sess.get("mapping", {})
    mapping[ph["label"]] = val
    sess["mapping"] = mapping
    await store.save_session(req.session_id, sess)

    remaining = sum(1 for p in sess["placeholders"] if not p.get("value"))
    return FillResponse(ok=True, remaining=remaining, placeholders=[Placeholder(**p) for p in sess["placeholders"]])
'''
'''
@app.post("/fill", response_model=FillResponse)
async def fill_slot(req: FillRequest):
    """
    - Exact key match if you pass a real placeholder key.
    - If you pass a semantic alias ('purchase_amount' or 'post_money_valuation_cap'),
      map it deterministically to the right currency placeholder.
    - Normalizes currency/date.
    - Auto-fills [COMPANY] when company_name is set.
    """
    store = await get_store()
    try:
        sess = await store.load_session(req.session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")

    ph = next((p for p in sess["placeholders"] if p["key"] == req.key), None)

    currency_aliases = {"purchase_amount", "post_money_valuation_cap"}
    if ph is None and req.key in currency_aliases:
        try:
            doc_text = _load_doc_text(sess["files"]["original_path"])
        except Exception:
            doc_text = ""
        ph = _select_currency_placeholder_for_alias(sess, doc_text, req.key)

    if ph is None:
        raise HTTPException(status_code=404, detail=f"Placeholder key '{req.key}' not found")

    raw_val = req.value
    val = raw_val

    # Normalize currency/date
    if ph.get("type") == "currency" or req.key in currency_aliases:
        val = normalize_currency(raw_val)
    if ph.get("type") == "date" or req.key == "date_of_safe":
        val = normalize_date(raw_val)

    # Auto-fill [COMPANY] when company_name is set
    if req.key == "company_name":
        for p in sess["placeholders"]:
            if p["key"] == "company" and not p.get("value"):
                p["value"] = (val or "").upper()
                sess.setdefault("mapping", {})[p["label"]] = p["value"]

    # Persist (docx replacer keys by label)
    ph["value"] = val
    mapping = sess.get("mapping", {})
    mapping[ph["label"]] = val
    sess["mapping"] = mapping
    await store.save_session(req.session_id, sess)

    remaining = sum(1 for p in sess["placeholders"] if not p.get("value"))
    return FillResponse(ok=True, remaining=remaining, placeholders=[Placeholder(**p) for p in sess["placeholders"]])

'''
@app.get("/preview", response_class=HTMLResponse)
async def preview(session_id: str = Query(...)):
    store = await get_store()
    sess = await store.load_session(session_id)
    orig = sess["files"]["original_path"]

    extracted = extract_plaintext_and_placeholders(orig)
    text = extracted["text"]
    html_text = html.escape(text)
    if sess.get("mapping"):
        filled = apply_mapping_to_text(extracted["text"], sess["mapping"])
        html_text = html.escape(filled)
    html_text = html_text.replace("\n", "<br/>")

    doc = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Preview - Session {session_id}</title>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<style>
  body {{ font-family: ui-sans-serif, system-ui, -apple-system, 'SF Pro Text', Inter, Arial; margin: 24px; color: #0A0A0A; }}
  .container {{ max-width: 820px; margin: 0 auto; line-height: 1.5; }}
  .hint {{ color: #666; font-size: 12px; margin-bottom: 12px; }}
  .doc {{ background: #fff; border: 1px solid #e5e5ea; border-radius: 12px; padding: 24px; }}
</style>
</head>
<body>
<div class="container">
  <div class="hint">Preview is a simplified text rendering for speed. The downloadable .docx retains original formatting.</div>
  <div class="doc">{html_text}</div>
</div>
</body>
</html>"""
    return HTMLResponse(content=doc)
 

@app.get("/download")
async def download(session_id: str = Query(...)):
    store = await get_store()
    sess = await store.load_session(session_id)
    orig = sess["files"]["original_path"]
    filled_path = os.path.join(store.session_dir(session_id), "filled.docx")
    mapping = sess.get("mapping", {})

    path = orig
    name = "original.docx"
    if mapping:
        replace_placeholders_in_docx(orig, filled_path, mapping)
        path = filled_path
        name = "filled.docx"

    f = open(path, "rb")
    headers = {
        "Cache-Control": "no-store",
        "Content-Disposition": f'attachment; filename="{name}"'
    }
    return StreamingResponse(f, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", headers=headers)

# -----------------------
# ASK: conversational slot-filling (deterministic baseline + optional LLM refinement)
# -----------------------

class AskRequest(BaseModel):
    session_id: str

class AskResponse(BaseModel):
    next: Optional[Dict[str, Any]] = None
    remaining: int = 0
    missing_keys: List[str] = []

PRIORITY_ORDER = [
    "company_name",
    "investor_name",
    "purchase_amount",
    "date_of_safe",
    "state_of_incorporation",
    "post_money_valuation_cap",
    "governing_law_jurisdiction",
    "company",  # [COMPANY] uppercase alias
    "name",
    "title",
    "company_address",
    "company_email",
    "investor_address",
    "investor_email",
]
"""
def _question_for(ph: Dict[str, Any], sess: Dict[str, Any], doc_text: str) -> Dict[str, Any]:
    key = ph["key"]
    label = ph["label"]
    ptype = ph.get("type", "string")

    if ptype == "currency" and ("___" in label or label.startswith("$[")):
        guessed = _guess_currency_label_key(doc_text, label)
        if guessed != "currency":
            key = guessed
"""
def _question_for(ph: Dict[str, Any], sess: Dict[str, Any], doc_text: str) -> Dict[str, Any]:
    key = ph["key"]
    label = ph["label"]
    ptype = ph.get("type", "string")

    if ptype == "currency":
        # Use deterministic classification
        guessed = _guess_currency_label_key(doc_text, label)
        if guessed != "currency":
            key = guessed
    if key == "company":
        company_name = None
        for p in sess["placeholders"]:
            if p["key"] == "company_name" and p.get("value"):
                company_name = p["value"]
                break
        suggestion = company_name.upper() if company_name else None
    else:
        suggestion = None

    q = "Please provide a value."
    examples = []
    if key == "company_name":
        q = "What is the company’s full legal name (as in formation documents)?"
        examples = ["AlphaSoft Technologies LTD"]
    elif key == "investor_name":
        q = "What is the investor’s legal name (entity or individual)?"
        examples = ["Sample Capital LLC"]
    elif key == "purchase_amount":
        q = "What is the Purchase Amount (in USD)?"
        examples = ["$250,000", "$100,000"]
    elif key == "date_of_safe":
        q = "What is the Date of Safe? (e.g., September 14, 2025)"
        examples = ["September 14, 2025"]
    elif key == "state_of_incorporation":
        q = "What is the company’s state of incorporation?"
        examples = ["California", "Delaware"]
    elif key == "post_money_valuation_cap":
        q = "What is the Post‑Money Valuation Cap (in USD)?"
        examples = ["$8,000,000", "$5,000,000"]
    elif key == "governing_law_jurisdiction":
        q = "What is the governing law jurisdiction?"
        examples = ["California", "Delaware"]
    elif key == "company":
        q = "Confirm the uppercase company label for the signature block."
        examples = ["ALPHASOFT TECHNOLOGIES LTD"]
    elif key == "name":
        q = "Who is signing on behalf of the company (full name)?"
        examples = ["Michael Ohanu"]
    elif key == "title":
        q = "What is the signatory’s title?"
        examples = ["Founder & CEO"]
    elif "email" in key:
        q = "Provide the email address."
        examples = ["legal@alphasofttechnologies.ai"]
    elif "address" in key:
        q = "Provide the full mailing address."
        examples = ["123 Market St, San Francisco, CA 94103"]
    elif ph.get("type") == "currency":
        q = "Provide the dollar amount."
        examples = ["$1,000,000"]
    else:
        q = f"Provide a value for {label}."

    return {
        "key": key,
        "label": label,
        "type": ptype,
        "question": q,
        "examples": examples,
        "suggestion": suggestion,
    }

@app.post("/ask", response_model=AskResponse)
async def ask_next(req: AskRequest, response: Response):
    store = await get_store()
    #sess = await store.load_session(req.session_id)
    try:
        sess = await store.load_session(req.session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    
    doc_text = _load_doc_text(sess["files"]["original_path"])

    missing = [p for p in sess["placeholders"] if not p.get("value")]
    def rank(p):
        k = p["key"]
        try:
            return PRIORITY_ORDER.index(k)
        except ValueError:
            return 10_000
    missing_sorted = sorted(missing, key=rank)

    if not missing_sorted:
        return AskResponse(next=None, remaining=0, missing_keys=[])

    nxt = _question_for(missing_sorted[0], sess, doc_text)
    # Optional LLM refinement
    try:
        refined = suggest_question(doc_text, nxt, [p['key'] for p in missing_sorted], sess.get('mapping', {}))
        if refined and isinstance(refined, dict):
            # Keep the same key unless model chose a valid alternative still missing
            if refined.get('key') in [p['key'] for p in missing_sorted]:
                nxt['key'] = refined['key']
            nxt['question'] = refined.get('question', nxt['question'])
            nxt['examples'] = refined.get('examples', nxt.get('examples', []))
            nxt['suggestion'] = refined.get('suggestion', nxt.get('suggestion'))
            response.headers['X-Ask-Source'] = 'openai'
    except Exception:
        pass
    if 'X-Ask-Source' not in response.headers:
        response.headers['X-Ask-Source'] = 'deterministic'
    return AskResponse(
        next=nxt,
        remaining=len(missing_sorted),
        missing_keys=[p["key"] for p in missing_sorted],
    )


@app.get("/diag/llm")
def diag_llm():
    return {
        "openai_enabled": is_enabled(),
        "model": os.environ.get("OPENAI_MODEL", "gpt-4o"),
        "ask_use_openai": os.environ.get("ASK_USE_OPENAI", "1"),
    }