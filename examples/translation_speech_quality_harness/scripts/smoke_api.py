"""One tiny call per Bodhan model, to verify keys and response formats.

Cost: well under ₹0.10 (hard-capped at ₹0.50). Outputs are cached, so rerunning is free.
Never prints keys.

    python scripts/smoke_api.py [--lang hi] [--voice NAME]   (default voice: per language)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from bodhan_eval.cache import Cache
from bodhan_eval.checks.audio import load_audio
from bodhan_eval.clients import APIError, BodhanClient
from bodhan_eval.cost import CostMeter
from bodhan_eval.languages import load_languages

TEXT = "Water boils at 100 degrees Celsius at sea level."


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", default="hi")
    ap.add_argument("--voice", default=None, help="default: the language's voice")
    args = ap.parse_args()

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    for k in ("BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY", "BODHAN_TRANSCRIBE_KEY"):
        print(
            f"{k}: {'set' if os.environ.get(k) or os.environ.get('BODHAN_API_KEY') else 'MISSING'}"
        )

    cache = Cache(Path(".bodhan_cache") / "cache.sqlite")
    cost = CostMeter(max_inr=0.5)
    client = BodhanClient(cache, cost, concurrency=2)
    ok = True
    try:
        print("\n[1] Indic-Translate  en ->", args.lang)
        tr = await client.translate(TEXT, args.lang)
        meta = (
            cache.get(
                "translate",
                {
                    "model": "indic-translate",
                    "text": TEXT,
                    "target_language": args.lang,
                    "script": "native",
                },
            )
            or (b"", {})
        )[1]
        print("  translation:", tr.text)
        print("  truncated:", tr.truncated, "| cached:", tr.cached, "| usage:", meta.get("usage"))

        print("\n[2] Indic-Translate back-translation ->", "en")
        bt = await client.translate(tr.text, "en")
        print("  back-translation:", bt.text)

        voice = args.voice or load_languages()[args.lang].voice
        print(f"\n[3] Indic-Speak  voice={voice}")
        sp = await client.speak(tr.text, args.lang, voice)
        x, sr = load_audio(sp.wav)
        print(f"  audio: {len(sp.wav)} bytes, {sr} Hz, {len(x) / sr:.2f} s | cached: {sp.cached}")
        out = Path("outputs") / f"smoke.{args.lang}.wav"
        out.parent.mkdir(exist_ok=True)
        out.write_bytes(sp.wav)
        print("  saved to", out, "(git-ignored)")

        print("\n[4] Indic-Transcribe")
        asr = await client.transcribe(sp.wav, args.lang)
        print("  transcript:", asr.text, "| cached:", asr.cached)
    except APIError as e:
        ok = False
        print(
            f"  FAILED: status={e.status} code={e.code} message={e.message} "
            f"request_id={e.request_id}"
        )
    finally:
        await client.aclose()
        print("\ncost:", cost.summary())
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
