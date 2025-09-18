# llm.py
"""
Optional OpenAI GPT-4o helper for refining /ask questions.

Env:
  OPENAI_API_KEY        # required to enable LLM mode
  ASK_USE_OPENAI=1      # set to "0" to disable (defaults to "1")
  OPENAI_MODEL=gpt-4o   # override to another compliant model if desired
"""
import os, json
from typing import Optional, Dict, Any, List

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
ASK_USE_OPENAI = os.getenv("ASK_USE_OPENAI", "1") == "1"

try:
    from openai import OpenAI
    _OPENAI_SDK_AVAILABLE = True
except Exception:
    OpenAI = None  # type: ignore
    _OPENAI_SDK_AVAILABLE = False

def is_enabled() -> bool:
    """Return True if we should attempt OpenAI calls."""
    api_key_present = bool(os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY_PATH"))
    return ASK_USE_OPENAI and _OPENAI_SDK_AVAILABLE and api_key_present

def _client():
    return OpenAI()

SYSTEM_PROMPT = """You are a precise legal-document assistant.
Rewrite ONE question to fill a missing field in a YC SAFE (post-money) template.
Keep it concise, formal but friendly, and unambiguous. Provide 1–3 concrete examples.
Return ONLY strict JSON with double quotes; no code fences, no extra commentary."""

USER_TEMPLATE = """Context:
- Surrounding document text (excerpted): {doc_excerpt}

Current mapping (already filled values):
{mapping_json}

Next placeholder to ask for:
{placeholder_json}

Full list of remaining missing keys (ordered by priority):
{missing_keys_json}

JSON schema to return (no other text):
{{
  "key": "<same key you are asking about>",
  "question": "<one clear question>",
  "examples": ["<ex1>", "<ex2>"],
  "suggestion": "<optional default value or UPPERCASE transform if applicable, else null>"
}}"""

def _excerpt(text: str, max_chars: int = 2800) -> str:
    if len(text) <= max_chars:
        return text
    head = text[: max_chars // 2]
    tail = text[-max_chars // 2 :]
    return head + "\n...\n" + tail

def suggest_question(
    doc_text: str,
    next_placeholder: Dict[str, Any],
    missing_keys: List[str],
    current_mapping: Dict[str, str],
) -> Optional[Dict[str, Any]]:
    """Ask GPT-4o to refine the next question; return dict or None if disabled/error."""
    if not is_enabled():
        return None
    try:
        client = _client()
        user = USER_TEMPLATE.format(
            doc_excerpt=_excerpt(doc_text),
            mapping_json=json.dumps(current_mapping, indent=2, ensure_ascii=False),
            placeholder_json=json.dumps(next_placeholder, indent=2, ensure_ascii=False),
            missing_keys_json=json.dumps(missing_keys, indent=2, ensure_ascii=False),
        )
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=0.2,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
        )
        content = resp.choices[0].message.content.strip()
        # Strip accidental code fences
        if content.startswith("```"):
            content = content.strip("`")
            if "\n" in content:
                content = content.split("\n", 1)[1]
        data = json.loads(content)
        # Minimal schema check
        for k in ("key", "question", "examples"):
            if k not in data:
                return None
        if "suggestion" not in data:
            data["suggestion"] = None
        return data
    except Exception:
        return None
