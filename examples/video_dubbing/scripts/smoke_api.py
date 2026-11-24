"""One cheap live call to each API, to confirm keys and response shapes (costs well under ₹0.10).

    python scripts/smoke_api.py

Round trip: Indic-Speak reads an English sentence -> Indic-Transcribe hears it back ->
Indic-Translate puts it into Hindi -> the LLM returns a tiny JSON verdict.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from bodhan_dub import logs
from bodhan_dub.bodhan import BodhanClient, wav_duration
from bodhan_dub.config import Settings
from bodhan_dub.llm import LLM

SENTENCE = "The sum of the angles of a triangle is 180 degrees."
OUT = Path(__file__).resolve().parent.parent / "outputs"


def main() -> int:
    load_dotenv()
    logs.configure("INFO")
    settings = Settings.from_env()
    missing = settings.missing()
    if bodhan_missing := [m for m in missing if m.startswith("BODHAN_")]:
        print(f"missing: {', '.join(bodhan_missing)} — fill in .env first", file=sys.stderr)
        return 2

    client = BodhanClient(settings)
    wav = client.speak(SENTENCE, voice="Amit", lang="en")
    OUT.mkdir(exist_ok=True)
    (OUT / "smoke_en.wav").write_bytes(wav)
    print(f"indic-speak      -> {wav_duration(wav):.2f}s of audio, saved outputs/smoke_en.wav")

    heard = client.transcribe(wav, language="en")
    print(f"indic-transcribe -> {heard!r}")

    hindi = client.translate(heard, "hi")
    print(f"indic-translate  -> {hindi.text!r} (truncated={hindi.truncated}, tokens={hindi.output_tokens})")

    if any(m.startswith("LLM_") for m in missing):
        print("llm              -> skipped (LLM_BASE_URL / LLM_MODEL / LLM_API_KEY not set)")
    else:
        verdict = LLM(settings).complete_json(
            "You compare two English sentences. Reply as JSON: {\"same_meaning\": true|false}.",
            f"A: {SENTENCE}\nB: {heard}",
        )
        print(f"llm              -> {verdict}")
    print("usage:", json.dumps(client.usage.snapshot(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
