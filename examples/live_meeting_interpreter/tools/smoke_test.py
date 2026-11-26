"""End-to-end check of your three Bodhan keys, no audio devices needed.

    python -m tools.smoke_test

Speaks a sentence with Indic-Speak, transcribes that audio with Indic-Transcribe,
then translates the transcript with Indic-Translate. Prints timings.
"""
import asyncio
import time
from pathlib import Path

from app.bodhan.client import BodhanClient
from app.config import load_settings

SAMPLES = {
    "hi": "नमस्ते, आज की मीटिंग में हम अगले हफ्ते के प्लान पर बात करेंगे।",
    "en": "Hello, in today's meeting we will discuss next week's plan.",
}


async def main() -> None:
    s = load_settings()
    lang = s.their_language
    text = SAMPLES.get(lang, SAMPLES["en"])
    client = BodhanClient(s)
    try:
        t = time.monotonic()
        wav = await client.speak(text, lang, s.tts_voice, s.tts_style)
        Path("out").mkdir(exist_ok=True)
        Path("out/smoke_tts.wav").write_bytes(wav)
        print(f"TTS        {time.monotonic() - t:5.2f}s  {len(wav)} bytes -> out/smoke_tts.wav")

        t = time.monotonic()
        transcript = await client.transcribe(wav, lang)
        print(f"ASR        {time.monotonic() - t:5.2f}s  {transcript}")

        t = time.monotonic()
        translated = await client.translate(transcript, s.my_language, s.subtitle_script)
        print(f"Translate  {time.monotonic() - t:5.2f}s  {translated}")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
