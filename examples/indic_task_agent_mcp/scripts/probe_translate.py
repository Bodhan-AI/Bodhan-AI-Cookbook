"""One-off probe: which request shape does the live Indic-Translate endpoint accept? Costs a fraction of a paisa.
Prints status and the start of each response; never prints the key."""

import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
key = os.environ.get("BODHAN_TRANSLATE_KEY") or os.environ.get("BODHAN_API_KEY")
assert key and not key.startswith("<"), "BODHAN_TRANSLATE_KEY is not set in .env"
URL = "https://api.bodhan.ai/v1/chat/completions"
msg = [{"role": "user", "content": "Water boils at 100 degrees Celsius."}]
variants = {
    "A android style": {"model": "indic-translate", "messages": msg, "source_language_code": "en", "target_language_code": "hi"},
    "B target_language": {"model": "indic-translate", "messages": msg, "target_language": "hi", "script": "native"},
    "C target code only": {"model": "indic-translate", "messages": msg, "target_language_code": "hi"},
}
for name, body in variants.items():
    r = httpx.post(URL, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=60)
    print(f"--- {name}: HTTP {r.status_code}")
    try:
        data = r.json()
        print(json.dumps(data, ensure_ascii=False)[:700])
    except ValueError:
        print(r.text[:300])
