"""Throwaway probe: find out which translate URL the gateway accepts."""

import os

import httpx
from dotenv import load_dotenv

load_dotenv()
base = os.getenv("BODHAN_BASE_URL", "https://api.bodhan.ai")
print("BASE =", repr(base))  # repr() reveals hidden spaces or quotes

key = os.getenv("BODHAN_TRANSLATE_KEY", "").strip()
print("translate key looks set:", bool(key) and not key.startswith("<"), "| length:", len(key))

body = {"text": "Hello", "target_language": "hi"}
for path in ["/translate", "/v1/translate"]:
    url = base.strip().rstrip("/") + path
    r = httpx.post(url, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=60)
    print(f"{url} -> {r.status_code}  {r.text[:200]}")

# Control test: a different route, to check that the host and the speak key work at all.
skey = os.getenv("BODHAN_SPEAK_KEY", "").strip()
r = httpx.post(
    base.strip().rstrip("/") + "/v1/audio/speech",
    headers={"Authorization": f"Bearer {skey}"},
    json={"model": "indic-speak", "input": "नमस्ते", "voice": "Kavya",
          "instructions": '{"lang": "hi"}'},
    timeout=120,
)
print("speech ->", r.status_code, r.headers.get("content-type"), len(r.content), "bytes")