"""Throwaway probe: try translation through the chat-completions transport."""

import json
import os

import httpx
from dotenv import load_dotenv

load_dotenv()
base = os.getenv("BODHAN_BASE_URL", "https://api.bodhan.ai").strip().rstrip("/")
key = os.getenv("BODHAN_TRANSLATE_KEY", "").strip()
url = base + "/v1/chat/completions"
headers = {"Authorization": f"Bearer {key}"}
text = "The meeting has been postponed."

variants = {
    "A: target_language field": {
        "model": "indic-translate",
        "messages": [{"role": "user", "content": text}],
        "target_language": "hi",
    },
    "B: language in the prompt": {
        "model": "indic-translate",
        "messages": [{"role": "user", "content": f"Translate to Hindi: {text}"}],
    },
    "C: bare, no target": {
        "model": "indic-translate",
        "messages": [{"role": "user", "content": text}],
    },
}

for name, body in variants.items():
    r = httpx.post(url, headers=headers, json=body, timeout=60)
    print(f"\n{name} -> {r.status_code}")
    print(r.text[:400])