"""One tiny real call per Bodhan model (~Rs 0.25 total, all cached). Run: uv run python scripts/live_smoke.py"""
import asyncio
import os

from dotenv import load_dotenv
from PIL import Image, ImageDraw

load_dotenv()
os.environ.setdefault("BODHAN_WORKDIR", ".bodhan_work")
from bodhan_mcp import core
from bodhan_mcp.tools import ocr, speak, translate
from bodhan_mcp.tools.audio import transcribe_audio


async def main():
    w = core.workdir()
    os.environ["BODHAN_MAX_COST_INR"] = "1.0"  # hard stop for this smoke test
    async def step(name, coro):
        try:
            return await coro
        except Exception as e:  # report each model independently
            print(f"{name}: FAILED {e}")

    t = await step("translate", translate.translate_text(text="Hello, how are you?", target_language="ta"))
    t and print("translate:", t["translation"], "| cost", t["cost_inr"])
    s = await step("speak", speak.synthesize_speech(text="नमस्ते दोस्तों", language="hi", format="wav", out_name="smoke.wav"))
    s and print("speak:", s["audio_ref"], s["duration_s"], "s | cost", s["cost_inr"])
    if s:
        a = await step("transcribe", transcribe_audio(s["audio_ref"], language="hi"))
        a and print("transcribe:", a["preview"], "| cost", a["cost_inr"], a["warnings"])
    im = Image.new("RGB", (400, 100), "white")
    ImageDraw.Draw(im).text((20, 40), "Hello 123 Invoice", fill="black")
    im.save(w / "smoke.png")
    o = await step("ocr", ocr.ocr_document(str(w / "smoke.png")))
    o and print("ocr:", o["preview"], "| cost", o["cost_inr"])
    print("total spent Rs", round(core.spent(), 4))


asyncio.run(main())
