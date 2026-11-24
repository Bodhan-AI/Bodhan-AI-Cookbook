"""Probe the Bodhan API before building on it.

    python eval/probe.py

Makes a few small, real calls and saves what came back (status, response headers,
raw body, latency) to outputs/probe.json. It answers three questions:

  a/b. Which route serves indic-transliterate on this key: the documented
       POST /transliterate, or POST /v1/chat/completions (the fallback PR #39 uses)?
  c.   Is the key itself fine? A known-good route (indic-speak) separates
       "bad key" (401/403) from "missing route" (404).
  d.   Does the working route handle a few more Hindi, Gujarati and Bengali sentences?

The key is read from .env and never printed or saved.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

RECIPE = Path(__file__).resolve().parent.parent
load_dotenv(RECIPE / ".env")
API_KEY = os.environ.get("BODHAN_API_KEY", "")
BASE_URL = os.environ.get("BODHAN_BASE_URL", "https://api.bodhan.ai")
OUT = RECIPE / "outputs" / "probe.json"


def redact(text: str) -> str:
    """Belt and braces: never let the key reach the output file, even if a server echoes it."""
    return text.replace(API_KEY, "<redacted>") if API_KEY else text


def call(label: str, path: str, body: dict) -> dict:
    """POST one JSON request and record everything except the request's auth header."""
    started = time.perf_counter()
    try:
        resp = requests.post(
            f"{BASE_URL}{path}",
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json=body,
            timeout=60,
        )
    except requests.RequestException as exc:
        return {"label": label, "path": path, "request_body": body, "error": redact(f"{type(exc).__name__}: {exc}")}
    latency_ms = round((time.perf_counter() - started) * 1000, 1)

    content_type = resp.headers.get("content-type", "")
    if content_type.startswith(("application/json", "text/")):
        raw_body = redact(resp.text)
    else:
        # Audio or other binary: record its size and first bytes, not the payload.
        raw_body = f"<{len(resp.content)} bytes of {content_type or 'unknown type'}; starts {resp.content[:12]!r}>"

    record = {
        "label": label,
        "path": path,
        "request_body": body,
        "status": resp.status_code,
        "latency_ms": latency_ms,
        "headers": {k: redact(v) for k, v in resp.headers.items()},
        "rate_limit_headers": {k: v for k, v in resp.headers.items() if k.lower().startswith("x-ratelimit")},
        "body": raw_body,
    }
    print(f"{label:<34} {resp.status_code}  {latency_ms:>7.1f} ms  {raw_body[:110]}")
    return record


def transliterate_body(route: str, text: str, language: str) -> tuple[str, dict]:
    """The same request on either route. The chat layout copies PR #39's fallback."""
    if route == "plain":
        return "/transliterate", {"text": text, "language": language, "script": "native"}
    return "/v1/chat/completions", {
        "model": "indic-transliterate",
        "messages": [{"role": "user", "content": text}],
        "language": language,
        "script": "native",
    }


def main() -> int:
    if not API_KEY:
        print(f"BODHAN_API_KEY is not set. Put it in {RECIPE / '.env'}")
        return 1

    results = []
    results.append(call("a. /transliterate", *transliterate_body("plain", "kal meeting hai", "hi")))
    results.append(call("b. /v1/chat/completions", *transliterate_body("chat", "kal meeting hai", "hi")))
    results.append(call("c. control: /v1/audio/speech", "/v1/audio/speech", {
        "model": "indic-speak",
        "input": "नमस्ते",
        "voice": "Kavya",
        "instructions": json.dumps({"lang": "hi"}),
    }))

    working = "plain" if results[0].get("status") == 200 else "chat" if results[1].get("status") == 200 else None
    if working:
        for language, text in [("hi", "aap kaise hain"), ("gu", "tame kem cho"), ("bn", "ami bhalo achi")]:
            results.append(call(f"d. {working} {language}: {text}", *transliterate_body(working, text, language)))
    else:
        print("d. skipped: neither transliteration route returned 200")

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({"base_url": BASE_URL, "working_route": working, "calls": results},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved {OUT.relative_to(RECIPE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
