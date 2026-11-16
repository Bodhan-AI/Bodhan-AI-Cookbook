"""Phase 0 probe: one tiny real call per Bodhan model, print the raw response shape.

    python scripts/probe.py            # runs every probe not already saved
    python scripts/probe.py --force    # re-run all (costs money again)

Reads keys from .env (BODHAN_TRANSCRIBE_KEY, BODHAN_TRANSLATE_KEY, BODHAN_SPEAK_KEY,
BODHAN_OCR_KEY). Each probe's result is saved to outputs/probe/<name>.json and is
skipped on re-run, so the same input is never paid for twice.
Estimated total spend for a full run: ~Rs 0.30 (OCR image Rs 0.20 is most of it).
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "probe"
SAMPLE_WAV = ROOT.parents[1] / "sample_data" / "sample_hindi.wav"  # cookbook's shared 6 s Hindi clip
BASE = "https://api.bodhan.ai"
KEEP_HEADERS = ("content-type", "x-request-id", "x-ratelimit-limit-requests",
                "x-ratelimit-limit-parallel-requests", "x-ratelimit-remaining-requests", "retry-after")


def load_env() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def key(name: str) -> str:
    return os.environ.get(f"BODHAN_{name}_KEY", "")


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def call(name: str, method: str, url: str, api_key: str, **kw) -> dict:
    """Make one request, save a JSON record of it, return the record."""
    t0 = time.perf_counter()
    try:
        r = httpx.request(method, url, headers={"Authorization": f"Bearer {api_key}"}, timeout=60, **kw)
    except httpx.HTTPError as e:
        rec = {"probe": name, "url": url, "error": repr(e), "latency_ms": int((time.perf_counter() - t0) * 1000)}
        (OUT / f"{name}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2))
        return rec
    rec = {
        "probe": name,
        "url": url,
        "status": r.status_code,
        "latency_ms": int((time.perf_counter() - t0) * 1000),
        "headers": {k: v for k, v in r.headers.items() if k.lower() in KEEP_HEADERS or k.lower().startswith("x-ratelimit")},
    }
    if r.headers.get("content-type", "").startswith("audio/"):
        wav = OUT / f"{name}.{'mp3' if 'mpeg' in r.headers['content-type'] else 'wav'}"
        wav.write_bytes(r.content)
        rec["body"] = {"bytes": len(r.content), "saved_to": str(wav.relative_to(ROOT)), "duration_s": duration(wav)}
    else:
        try:
            rec["body"] = r.json()
        except ValueError:
            rec["body_text"] = r.text[:2000]
    (OUT / f"{name}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2))
    return rec


def saved(name: str) -> dict | None:
    p = OUT / f"{name}.json"
    return json.loads(p.read_text()) if p.exists() else None


def run(name: str, force: bool, fn) -> dict:
    if not force and (rec := saved(name)):
        print(f"-- {name}: cached (not re-sent)")
        return rec
    print(f"-> {name}")
    return fn()


def make_ocr_image(path: Path) -> None:
    """Small synthetic page: two lines of text and a 3x3 table."""
    img = Image.new("RGB", (640, 360), "white")
    d = ImageDraw.Draw(img)
    d.text((20, 20), "Worksheet 3: Fractions", fill="black")
    d.text((20, 45), "Fill in the table below. Total marks: 15", fill="black")
    rows = [["Q", "Fraction", "Decimal"], ["1", "1/2", "0.5"], ["2", "3/4", "0.75"]]
    x0, y0, cw, ch = 20, 90, 180, 40
    for i, row in enumerate(rows):
        for j, cell in enumerate(row):
            d.rectangle([x0 + j * cw, y0 + i * ch, x0 + (j + 1) * cw, y0 + (i + 1) * ch], outline="black")
            d.text((x0 + j * cw + 10, y0 + i * ch + 12), cell, fill="black")
    img.save(path)


def transcribe(name: str, wav: Path, language: str | None) -> dict:
    data = {"model": "indic-transcribe"} | ({"language": language} if language else {})
    with wav.open("rb") as f:
        return call(name, "POST", f"{BASE}/v1/audio/transcriptions", key("TRANSCRIBE"),
                    data=data, files={"file": (wav.name, f, "audio/wav")})


def translate(name: str, text: str, target: str, api_key: str | None = None) -> dict:
    # Docs list POST /translate, but it returns 404 (Nov 2026); the gateway only serves
    # /v1/chat/completions with model indic-translate: one user turn, target as an extra field.
    body = {"model": "indic-translate", "messages": [{"role": "user", "content": text}], "target_language": target}
    return call(name, "POST", f"{BASE}/v1/chat/completions", api_key if api_key is not None else key("TRANSLATE"),
                json=body)


def speak(name: str, text: str, voice: str, lang: str) -> dict:
    return call(name, "POST", f"{BASE}/v1/audio/speech", key("SPEAK"),
                json={"model": "indic-speak", "input": text, "voice": voice, "instructions": json.dumps({"lang": lang})})


def ocr(name: str, png: Path, table_format: str) -> dict:
    uri = "data:image/png;base64," + base64.b64encode(png.read_bytes()).decode()
    body = {"model": "indic-ocr", "table_format": table_format,
            "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": uri}}]}]}
    return call(name, "POST", f"{BASE}/v1/chat/completions", key("OCR"), json=body)


def main() -> int:
    force = "--force" in sys.argv
    load_env()
    missing = [k for k in ("TRANSCRIBE", "TRANSLATE", "SPEAK", "OCR") if not key(k)]
    if missing:
        print(f"missing keys: {missing} (set BODHAN_<NAME>_KEY in .env)")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)

    # Speech-to-text: known language, auto-detect, and the 30 s limit.
    run("stt_hi", force, lambda: transcribe("stt_hi", SAMPLE_WAV, "hi"))
    run("stt_autodetect", force, lambda: transcribe("stt_autodetect", SAMPLE_WAV, None))
    long_wav = OUT / "silence_35s.wav"
    if not long_wav.exists():
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
                        "-t", "35", str(long_wav)], check=True)
    run("stt_over_30s", force, lambda: transcribe("stt_over_30s", long_wav, "hi"))

    # Translation: en->hi, Indic->en (source auto-detected), unsupported target, bad key.
    src = "The meeting has been moved to Monday at 10 am."
    hi = run("translate_en_hi", force, lambda: translate("translate_en_hi", src, "hi"))
    choices = (hi.get("body") or {}).get("choices") or [{}]
    hi_text = choices[0].get("message", {}).get("content", "")
    if hi_text:
        run("translate_hi_en", force, lambda: translate("translate_hi_en", hi_text, "en"))
    run("translate_fr_unsupported", force, lambda: translate("translate_fr_unsupported", src, "fr"))
    run("translate_bad_key", force, lambda: translate("translate_bad_key", src, "hi", api_key="invalid"))

    # Speech synthesis: speak the model's own Hindi output, then back-transcribe it; bad voice.
    if hi_text:
        tts = run("tts_hi", force, lambda: speak("tts_hi", hi_text, "Kavya", "hi"))
        tts_wav = ROOT / (tts.get("body") or {}).get("saved_to", "missing.mp3")
        if tts_wav.exists():
            run("stt_back_of_tts", force, lambda: transcribe("stt_back_of_tts", tts_wav, "hi"))
    run("tts_bad_voice", force, lambda: speak("tts_bad_voice", "test", "NoSuchVoice", "hi"))

    # OCR: synthetic worksheet page with a table.
    png = OUT / "worksheet.png"
    if not png.exists():
        make_ocr_image(png)
    page = run("ocr_markdown", force, lambda: ocr("ocr_markdown", png, "markdown"))

    # Does translation keep a Markdown table intact? Feed it the OCR output.
    page_md = ((page.get("body") or {}).get("choices") or [{}])[0].get("message", {}).get("content", "")
    if page_md:
        run("translate_table_mr", force, lambda: translate("translate_table_mr", page_md, "mr"))

    # Summary.
    print(f"\n{'probe':28} {'status':>6} {'ms':>6}  body")
    for p in sorted(OUT.glob("*.json")):
        rec = json.loads(p.read_text())
        body = json.dumps(rec.get("body", rec.get("body_text", rec.get("error"))), ensure_ascii=False)
        print(f"{rec['probe']:28} {rec.get('status', '-'):>6} {rec['latency_ms']:>6}  {body[:300]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
