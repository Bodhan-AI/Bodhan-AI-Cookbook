"""Build tiny synthetic demo inputs in ./demo_inputs (git-ignored). Spends ~Rs 0.2 on Bodhan TTS/translate.
Run: uv run python scripts/make_demo_inputs.py"""
import asyncio
import os
import shutil
from pathlib import Path

import pymupdf
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

load_dotenv(".env")
ROOT = Path("demo_inputs")
os.environ.setdefault("BODHAN_WORKDIR", str(ROOT / "_work"))
os.environ["BODHAN_MAX_COST_INR"] = "1.0"
from bodhan_mcp.tools import speak, translate

FONT = ImageFont.load_default(size=26)
CHAPTER = [
    "Chapter 3: The Water Cycle",
    "Water evaporates from oceans and lakes when the sun heats it.",
    "The vapour rises, cools, and condenses into clouds.",
    "When clouds grow heavy, water falls as rain or snow.",
    "Rivers carry the water back to the sea and the cycle repeats.",
]


def page(lines, size=(1000, 500)):
    im = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(im)
    for i, ln in enumerate(lines):
        d.text((40, 40 + i * 50), ln, fill="black", font=FONT)
    return im, d


def scanned_pdf():
    doc = pymupdf.open()
    for chunk in (CHAPTER[:3], CHAPTER[3:]):
        im, _ = page(chunk)
        im.save(ROOT / "_p.png")
        pg = doc.new_page(width=600, height=300)
        pg.insert_image(pg.rect, filename=str(ROOT / "_p.png"))
    d = ROOT / "pdf"
    d.mkdir(exist_ok=True)
    doc.save(d / "chapter.pdf")


def worksheet():
    im, d = page(["Class 5 Worksheet", "Fruit prices at the market"], (900, 420))
    rows = [("Fruit", "Price", "Quantity"), ("Apple", "40", "3"), ("Banana", "12", "6"), ("Mango", "60", "2")]
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            x, y = 40 + c * 260, 140 + r * 60
            d.rectangle([x, y, x + 260, y + 60], outline="black", width=2)
            d.text((x + 15, y + 15), cell, fill="black", font=FONT)
    out = ROOT / "worksheet"
    out.mkdir(exist_ok=True)
    im.save(out / "worksheet.png")


async def audio():
    for name, text, lang in [("hindi_note", None, "hi"), ("clip_a", "The train will arrive at six in the evening.", "te"),
                             ("clip_b", "Please bring your books to school tomorrow.", "te"),
                             ("clip_c", None, "hi")]:
        out = ROOT / ("note" if name == "hindi_note" else "clips")
        out.mkdir(exist_ok=True)
        if text:
            text = (await translate.translate_text(text=text, target_language=lang))["translation"]
        else:
            text = "आज मौसम बहुत अच्छा है। हम बाज़ार जाकर फल खरीदेंगे। फिर घर लौटकर खाना खाएँगे।" if name == "hindi_note" else "नमस्ते दोस्तों"
        r = await speak.synthesize_speech(text=text, language=lang, format="wav", out_name=name)
        shutil.copy(r["audio_ref"], out / f"{name}.wav")


ROOT.mkdir(exist_ok=True)
scanned_pdf()
worksheet()
asyncio.run(audio())
print({p.name: [f.name for f in p.iterdir()] for p in ROOT.iterdir() if p.is_dir() and not p.name.startswith("_")})
