"""Run this FIRST with your real keys. It calls each of the 4 Bodhan endpoints once with tiny inputs
(total cost well under ₹1) and tells you exactly which one is misconfigured.

    python scripts/live_smoke.py
"""
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bodhan_mcp.config import load_env  # noqa: E402

load_env()
import os  # noqa: E402

os.environ.setdefault("BODHAN_WORK_DIR", tempfile.mkdtemp(prefix="bodhan_smoke_"))

from bodhan_mcp import tools  # noqa: E402
from bodhan_mcp.costs import tracker  # noqa: E402
from bodhan_mcp.errors import ToolFailure  # noqa: E402


def ok(msg): print(f"  OK   {msg}")
def bad(msg): print(f"  FAIL {msg}")


async def main() -> int:
    failures = 0
    print("1/4 translate (Indic-Translate)")
    try:
        r = await tools.translate_text("ta", text="Hello, how are you today?")
        ok(f"{r.preview}")
    except ToolFailure as e:
        failures += 1
        bad(f"{e.code}: {e.message} {e.hint or ''}")

    print("2/4 speak (Indic-Speak)")
    audio_path = None
    try:
        r = await tools.synthesize_speech("hi", text="नमस्ते, आज कक्षा नौ बजे शुरू होती है।", output_path="smoke.hi.wav")
        audio_path = r
        ok(f"{r.duration_s}s of audio, voice {r.voice}")
    except ToolFailure as e:
        failures += 1
        bad(f"{e.code}: {e.message} {e.hint or ''}")

    print("3/4 transcribe (Indic-Transcribe), on the audio from step 2")
    if audio_path:
        try:
            v = await tools.verify_speech(audio_path.audio_path, "hi", expected_path=audio_path.spoken_text_path)
            ok(f"CER {v.cer} ok={v.ok} heard: {v.heard_preview}")
        except ToolFailure as e:
            failures += 1
            bad(f"{e.code}: {e.message} {e.hint or ''}")
    else:
        bad("skipped (step 2 failed)")

    print("4/4 OCR (Indic-OCR), on an image of English text")
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (900, 300), "white")
        d = ImageDraw.Draw(img)
        try:
            font = ImageFont.load_default(size=36)
        except TypeError:
            font = ImageFont.load_default()
        d.text((40, 40), "Chapter 1: Lines and Angles", fill="black", font=font)
        d.text((40, 120), "A right angle is 90 degrees.", fill="black", font=font)
        p = Path(os.environ["BODHAN_WORK_DIR"]) / "smoke.png"
        img.save(p)
        r = await tools.ocr_document(str(p))
        ok(f"{r.total_chars} chars: {r.preview}")
    except ToolFailure as e:
        failures += 1
        bad(f"{e.code}: {e.message} {e.hint or ''}")

    print(f"\nTotal spent: ₹{tracker.total:.3f}")
    print("All good - you can run the agent." if not failures else f"{failures} endpoint(s) need attention.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
