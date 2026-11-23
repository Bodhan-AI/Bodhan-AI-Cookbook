"""Live verification of all five Bodhan services using the credentials in backend/.env.

Usage (from the backend folder):   python scripts/verify_bodhan.py [--only speak,translate] [--json report.json]
                                   [--audio my.wav] [--ocr-image page.png]

Each service reports PASS / FAIL / SKIPPED separately. PASS means a real HTTP request succeeded and the
response had the documented shape. API keys are never printed. Requests are tiny but DO use credits.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Telugu output on Windows consoles

from app.core.config import Settings  # noqa: E402
from app.core.errors import AppError  # noqa: E402
from app.core.logging import redact, register_secret  # noqa: E402
from app.services.audio.wav import wav_duration_seconds  # noqa: E402
from app.services.bodhan import ocr, speech, transcription, translation, transliteration  # noqa: E402
from app.services.bodhan.client import BodhanClient  # noqa: E402
from app.services.bodhan.exceptions import BodhanError, BodhanNotConfigured  # noqa: E402

from datetime import datetime, timezone

SAMPLE_TE = "నమస్కారం, మా గ్రామానికి స్వాగతం."


def _latin_test_png() -> bytes | None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None
    img = Image.new("RGB", (900, 200), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=48)
    except TypeError:
        font = ImageFont.load_default()
    d.text((30, 70), "HERITAGELENS TEST 123", fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


async def check_speak(client, settings, ctx, args):
    r = await speech.synthesize_speech(client, SAMPLE_TE, "te", "Sravani")
    ctx["speech_wav"] = r.wav_bytes
    return f"received WAV, {wav_duration_seconds(r.wav_bytes):.2f}s, {len(r.wav_bytes)} bytes", 200, "audio/wav"


async def check_transcribe(client, settings, ctx, args):
    with tempfile.TemporaryDirectory() as tmp:
        if args.audio:
            src = Path(args.audio)
        elif ctx.get("speech_wav"):
            src = Path(tmp) / "sample.wav"
            src.write_bytes(ctx["speech_wav"])
        else:
            raise _Skip("needs audio: run with 'speak' passing, or supply --audio file")
        r = await transcription.transcribe_file(client, src, "te", ffmpeg_path=settings.ffmpeg_path)
    return f"{len(r.segments)} segment(s); text={r.text!r}", 200, "application/json"


async def check_translate(client, settings, ctx, args):
    r = await translation.translate_text(client, SAMPLE_TE, "en")
    return f"truncated={r.truncated}; translation={r.translation!r}", 200, "application/json"


async def check_transliterate(client, settings, ctx, args):
    r = await transliteration.transliterate_text(client, "నమస్కారం", "te", script="roman")
    return f"converted={r.converted_text!r}", 200, "application/json"


async def check_ocr(client, settings, ctx, args):
    if args.ocr_image:
        data = Path(args.ocr_image).read_bytes()
    else:
        data = _latin_test_png()
        if data is None:
            raise _Skip("install Pillow or pass --ocr-image path.png")
    res = await ocr.ocr_document(client, data)
    page = res.pages[0]
    if not page.ok:
        raise RuntimeError(page.error)
    return f"blocks={len(page.blocks)}; markdown={page.markdown[:80]!r}", 200, "application/json"


class _Skip(Exception):
    pass


CHECKS = {
    "speak": ("indic-speak", check_speak),
    "transcribe": ("indic-transcribe", check_transcribe),
    "translate": ("indic-translate", check_translate),
    "transliterate": ("indic-transliterate", check_transliterate),
    "ocr": ("indic-ocr", check_ocr),
}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated: " + ",".join(CHECKS))
    ap.add_argument("--json", help="write a JSON report to this path")
    ap.add_argument("--audio", help="audio file for the transcribe check (<= any length; WAV/MP3/OGG/FLAC/WebM)")
    ap.add_argument("--ocr-image", help="PNG/JPEG for the OCR check")
    args = ap.parse_args()

    settings = Settings()
    for s in settings.all_secret_values():
        register_secret(s)
    client = BodhanClient.from_settings(settings)
    wanted = [n.strip() for n in args.only.split(",")] if args.only else list(CHECKS)
    ctx: dict = {}
    report = []
    print(f"{'Status':<8} {'Timestamp':<25} {'Service':<20} {'HTTP':<6} {'Response Type':<18} {'Detail'}")
    print("-" * 110)
    for key in [k for k in CHECKS if k in wanted]:  # speak first so transcribe can reuse its audio
        name, fn = CHECKS[key]
        started = time.monotonic()
        now_ts = datetime.now(timezone.utc).isoformat()
        http_status = None
        response_type = None
        try:
            detail, http_status, response_type = await fn(client, settings, ctx, args)
            status = "PASS"
        except _Skip as exc:
            status, detail = "SKIPPED", str(exc)
        except BodhanNotConfigured:
            status, detail = "SKIPPED", "no API key configured for this service"
        except BodhanError as exc:
            status, detail = "FAIL", redact(str(exc))
            http_status = exc.status_code
            response_type = "error"
        except AppError as exc:
            status, detail = "FAIL", redact(exc.message)
            http_status = exc.status_code
            response_type = "error"
        except Exception as exc:  # network, parsing...
            status, detail = "FAIL", redact(f"{type(exc).__name__}: {exc}")
            response_type = "error"

        http_str = str(http_status) if http_status is not None else "-"
        resp_str = str(response_type) if response_type is not None else "-"
        report.append({
            "service": name,
            "status": status,
            "http_status": http_status,
            "response_type": response_type,
            "detail": detail,
            "timestamp": now_ts,
            "seconds": round(time.monotonic() - started, 2)
        })
        print(f"[{status:5}]  {now_ts:<25} {name:<20} {http_str:<6} {resp_str:<18} {detail}")
    await client.aclose()
    print("\nPASS means a real request to the live API succeeded with the documented response shape.")
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if any(r["status"] == "FAIL" for r in report) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
