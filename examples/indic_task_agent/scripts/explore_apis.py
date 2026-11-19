"""Throwaway exploration script: call each Bodhan model once and print what comes back.

Run from examples/indic_task_agent:
    python scripts/explore_apis.py [optional_page_image.png]
"""

import base64
import json
import os
import sys
import wave
from pathlib import Path

import httpx
from dotenv import load_dotenv
sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()
BASE = os.getenv("BODHAN_BASE_URL", "https://api.bodhan.ai")
OUT = Path("sample_data")
OUT.mkdir(exist_ok=True)


def key(name: str) -> str:
    """Per-model key first, then the shared BODHAN_API_KEY. Ignore unfilled <placeholders>."""
    for k in (name, "BODHAN_API_KEY"):
        v = os.getenv(k, "")
        if v and not v.startswith("<"):
            return v
    sys.exit(f"Missing key: set {name} or BODHAN_API_KEY in .env")


def auth(name: str) -> dict:
    return {"Authorization": f"Bearer {key(name)}"}


def show(r: httpx.Response) -> None:
    print(
        f"  status={r.status_code}  request-id={r.headers.get('x-request-id')}  "
        f"rate-limit={r.headers.get('x-ratelimit-limit-requests')}  "
        f"parallel={r.headers.get('x-ratelimit-limit-parallel-requests')}"
    )
    if r.status_code != 200:
        print("  ERROR BODY:", r.text[:500])


# 1) TRANSLATE: English -> Hindi
print("\n[1] Translate")
r = httpx.post(
    f"{BASE}/v1/chat/completions",
    headers=auth("BODHAN_TRANSLATE_KEY"),
    json={
        "model": "indic-translate",
        "messages": [{"role": "user", "content": "The meeting has been postponed to tomorrow."}],
        "target_language": "hi",
    },
    timeout=60,
)
show(r)
hindi = ""
if r.status_code == 200:
    hindi = r.json()["choices"][0]["message"]["content"]
    print("  translation:", hindi)

# 2) SPEAK: Hindi text -> WAV
print("\n[2] Speak")
wav_path = OUT / "hello_hi.wav"
if hindi:
    r = httpx.post(
        f"{BASE}/v1/audio/speech",
        headers=auth("BODHAN_SPEAK_KEY"),
        json={
            "model": "indic-speak",
            "input": hindi,
            "voice": "Kavya",
            "instructions": json.dumps({"lang": "hi"}),  # instructions is a JSON *string*
        },
        timeout=120,
    )
    show(r)
    if r.status_code == 200:
        wav_path.write_bytes(r.content)
        with wave.open(str(wav_path)) as w:
            print(f"  saved {wav_path}: {w.getnframes() / w.getframerate():.1f}s, "
                  f"{w.getframerate()} Hz, {w.getnchannels()} ch")

# 3) TRANSCRIBE: the WAV we just made -> text (round trip)
print("\n[3] Transcribe")
if wav_path.exists():
    with open(wav_path, "rb") as f:
        r = httpx.post(
            f"{BASE}/v1/audio/transcriptions",
            headers=auth("BODHAN_TRANSCRIBE_KEY"),
            files={"file": (wav_path.name, f, "audio/wav")},
            data={"model": "indic-transcribe", "language": "hi"},
            timeout=120,
        )
    show(r)
    if r.status_code == 200:
        print("  original :", hindi)
        print("  heard    :", r.json()["text"])

# 4) OCR: only if an image path is given
print("\n[4] OCR")
if len(sys.argv) > 1:
    img = Path(sys.argv[1])
    mime = "image/png" if img.suffix.lower() == ".png" else "image/jpeg"
    b64 = base64.b64encode(img.read_bytes()).decode()
    body = {
        "model": "indic-ocr",
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
        ]}],
    }
    r = httpx.post(f"{BASE}/v1/chat/completions", headers=auth("BODHAN_OCR_KEY"), json=body, timeout=180)
    show(r)
    if r.status_code == 200:
        d = r.json()
        print("  text:", d["choices"][0]["message"]["content"][:400])
        print("  blocks:", [(b["order"], b["label"]) for b in d["blocks"]])
        print("  usage:", d["usage"])
else:
    print("  skipped (pass a PNG/JPEG page image to test)")