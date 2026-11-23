"""Perform non-generative connectivity checks without printing credentials."""

import os

import httpx
from dotenv import load_dotenv


def main():
    load_dotenv()
    base = os.getenv("BODHAN_BASE_URL", "https://api.bodhan.ai/v1").rstrip("/")
    for name in ("BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY", "BODHAN_TRANSCRIBE_KEY"):
        key = os.getenv(name)
        if not key:
            print(f"{name}: missing")
            continue
        try:
            response = httpx.get(base + "/models", headers={"Authorization": "Bearer " + key}, timeout=10)
            print(f"{name}: HTTP {response.status_code} (model listing; not a model invocation)")
        except httpx.HTTPError as exc:
            print(f"{name}: connection failed ({type(exc).__name__})")


if __name__ == "__main__":
    main()
