"""Probe script to verify Bodhan API endpoints, response formats, and headers.

Makes exactly ONE minimal call to each of the 4 Bodhan models using files in samples/.
Never prints secrets or keys.
"""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
import dotenv
import requests

# Load environment variables from .env
BASE_DIR = Path(__file__).resolve().parent
dotenv.load_dotenv(BASE_DIR / ".env")

BASE_URL = "https://api.bodhan.ai"
SAMPLES_DIR = BASE_DIR / "samples"
OUT_CACHE_DIR = BASE_DIR / ".cache"
OUT_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def probe_translate() -> None:
    print("\n--- 1. Probing indic-translate (POST /v1/chat/completions) ---")
    key = os.environ.get("BODHAN_TRANSLATE_KEY")
    if not key:
        print("[SKIP] BODHAN_TRANSLATE_KEY not set in .env")
        return

    payload = {
        "model": "indic-translate",
        "messages": [{"role": "user", "content": "The school library opens at nine in the morning."}],
        "target_language_code": "hi",
        "target_script": "native",
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    t0 = time.perf_counter()
    resp = requests.post(f"{BASE_URL}/v1/chat/completions", headers=headers, json=payload, timeout=30)
    latency = time.perf_counter() - t0

    print(f"Status: {resp.status_code} ({resp.reason}) | Latency: {latency:.2f}s")
    print("Relevant Headers:")
    for h in ("content-type", "x-ratelimit-limit-requests", "x-ratelimit-remaining-requests", "content-length"):
        if h in resp.headers:
            print(f"  {h}: {resp.headers[h]}")

    if resp.status_code == 200:
        data = resp.json()
        print("Response Keys:", list(data.keys()))
        if "choices" in data and len(data["choices"]) > 0:
            msg = data["choices"][0].get("message", {})
            print("Content:", msg.get("content"))
        if "usage" in data:
            print("Usage info:", data["usage"])
    else:
        print("Error body:", resp.text)


def probe_transcribe() -> None:
    print("\n--- 2. Probing indic-transcribe (POST /v1/audio/transcriptions) ---")
    key = os.environ.get("BODHAN_TRANSCRIBE_KEY")
    audio_path = SAMPLES_DIR / "test_audio.wav"
    if not key:
        print("[SKIP] BODHAN_TRANSCRIBE_KEY not set in .env")
        return
    if not audio_path.exists():
        print(f"[SKIP] {audio_path} not found")
        return

    headers = {"Authorization": f"Bearer {key}"}
    data = {"model": "indic-transcribe", "language": "hi", "vad": "true"}

    t0 = time.perf_counter()
    with open(audio_path, "rb") as f:
        files = {"file": (audio_path.name, f, "audio/wav")}
        resp = requests.post(f"{BASE_URL}/v1/audio/transcriptions", headers=headers, data=data, files=files, timeout=45)
    latency = time.perf_counter() - t0

    print(f"Status: {resp.status_code} ({resp.reason}) | Latency: {latency:.2f}s")
    print("Relevant Headers:")
    for h in ("content-type", "x-ratelimit-limit-requests", "x-ratelimit-remaining-requests", "content-length"):
        if h in resp.headers:
            print(f"  {h}: {resp.headers[h]}")

    if resp.status_code == 200:
        res_json = resp.json()
        print("Response Keys:", list(res_json.keys()))
        print("Transcript preview:", res_json.get("text", "")[:100])
    else:
        print("Error body:", resp.text)


def probe_ocr() -> None:
    print("\n--- 3. Probing indic-ocr (POST /v1/chat/completions) ---")
    key = os.environ.get("BODHAN_OCR_KEY")
    img_path = SAMPLES_DIR / "test_image.png"
    if not key:
        print("[SKIP] BODHAN_OCR_KEY not set in .env")
        return
    if not img_path.exists():
        print(f"[SKIP] {img_path} not found")
        return

    b64_img = base64.b64encode(img_path.read_bytes()).decode("utf-8")
    payload = {
        "model": "indic-ocr",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_img}"}}
                ],
            }
        ],
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    t0 = time.perf_counter()
    resp = requests.post(f"{BASE_URL}/v1/chat/completions", headers=headers, json=payload, timeout=45)
    latency = time.perf_counter() - t0

    print(f"Status: {resp.status_code} ({resp.reason}) | Latency: {latency:.2f}s")
    print("Relevant Headers:")
    for h in ("content-type", "x-ratelimit-limit-requests", "x-ratelimit-remaining-requests", "content-length"):
        if h in resp.headers:
            print(f"  {h}: {resp.headers[h]}")

    if resp.status_code == 200:
        data = resp.json()
        print("Response Keys:", list(data.keys()))
        if "choices" in data and len(data["choices"]) > 0:
            msg = data["choices"][0].get("message", {})
            print("OCR Text preview:", (msg.get("content") or "")[:120])
    else:
        print("Error body:", resp.text)


def probe_speak() -> None:
    print("\n--- 4. Probing indic-speak (POST /v1/audio/speech) ---")
    key = os.environ.get("BODHAN_SPEAK_KEY")
    if not key:
        print("[SKIP] BODHAN_SPEAK_KEY not set in .env")
        return

    # Keep text to a very short 2-word sentence to minimize credit usage
    payload = {
        "model": "indic-speak",
        "input": "नमस्ते भारत।",
        "voice": "Amit",
        "instructions": json.dumps({"lang": "hi"}),
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    t0 = time.perf_counter()
    resp = requests.post(f"{BASE_URL}/v1/audio/speech", headers=headers, json=payload, timeout=30)
    latency = time.perf_counter() - t0

    print(f"Status: {resp.status_code} ({resp.reason}) | Latency: {latency:.2f}s")
    print("Relevant Headers:")
    for h in ("content-type", "x-ratelimit-limit-requests", "x-ratelimit-remaining-requests", "content-length"):
        if h in resp.headers:
            print(f"  {h}: {resp.headers[h]}")

    if resp.status_code == 200:
        out_wav = OUT_CACHE_DIR / "probe_speak_test.wav"
        out_wav.write_bytes(resp.content)
        print(f"Audio received: {len(resp.content)} bytes, saved to {out_wav}")
    else:
        print("Error body:", resp.text)


if __name__ == "__main__":
    print("========================================")
    print("Bodhan API Minimal Probe (1 call / model)")
    print("========================================")
    probe_translate()
    probe_transcribe()
    probe_ocr()
    probe_speak()
    print("\nProbe completed.")
