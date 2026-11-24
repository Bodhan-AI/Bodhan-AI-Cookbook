"""Call every API the recipe depends on once, and report which work.

    python smoke_test.py
"""

from __future__ import annotations

import io
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

import bodhan  # noqa: E402

OUT = HERE / "outputs"
OUT.mkdir(exist_ok=True)


def make_test_page() -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1000, 360), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 34)
    except OSError:
        font = ImageFont.load_default()
    lines = [
        "Photosynthesis",
        "Green plants make their own food using sunlight,",
        "water and carbon dioxide. Oxygen is released.",
    ]
    for i, line in enumerate(lines):
        draw.text((40, 40 + i * 90), line, font=font, fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def run(name: str, fn):
    start = time.perf_counter()
    try:
        result = fn()
    except Exception as exc:  # report every failure, keep going
        print(f"[FAIL] {name:16} {exc}")
        return None
    print(f"[ OK ] {name:16} {time.perf_counter() - start:5.2f}s  {str(result)[:90]!r}")
    return result


def llm_check() -> str:
    import llm

    return llm.chat_json([{"role": "user", "content": 'Return the JSON object {"ok": true}.'}])


def main() -> int:
    results = {}
    results["ocr"] = run("indic-ocr", lambda: bodhan.ocr_page(make_test_page()))
    native = run("indic-translit", lambda: bodhan.transliterate("paudhe dhoop se khana banate hain", "hi"))
    results["translit"] = native
    results["translate"] = run("indic-translate", lambda: bodhan.translate(native or "पौधे धूप से खाना बनाते हैं", "en"))

    def tts() -> str:
        wav = bodhan.speak("पौधे धूप से अपना खाना बनाते हैं।", voice="Kavya", lang="hi", style="children's stories")
        (OUT / "smoke_tts.wav").write_bytes(wav)
        return f"{len(wav)} bytes -> outputs/smoke_tts.wav"

    results["speak"] = run("indic-speak", tts)
    if results["speak"]:
        results["transcribe"] = run(
            "indic-transcribe", lambda: bodhan.transcribe((OUT / "smoke_tts.wav").read_bytes(), "hi")
        )
    results["llm"] = run(f"llm ({os.environ.get('LLM_MODEL', 'gpt-4o-mini')})", llm_check)

    failed = [k for k, v in results.items() if v is None]
    print("\nAll good." if not failed else f"\nFailed: {', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
