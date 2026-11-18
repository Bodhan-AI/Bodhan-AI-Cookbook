"""Create small demo inputs in sample_data/demo/ (git-ignored). Costs well under ₹0.01.

    python scripts/make_demo_inputs.py

* hindi_note.wav: the cookbook's 6 s Hindi sample clip (../../sample_data/sample_hindi.wav)
* worksheet.png: a synthetic English worksheet photo with a table
* bengali_paragraph.txt: an English paragraph translated to Bengali by Indic-Translate (Bodhan),
  so even the test input's Indian-language text comes from a Bodhan model
* french_talk.mp4: 3 s of video with a tone (for the "translate into French" impossible task)
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "src"))

OUT = HERE / "sample_data" / "demo"
PARAGRAPH = ("The village library opens at nine in the morning. Children come after school to read stories, "
             "and on Saturdays a volunteer teaches basic computer skills to anyone who wants to learn.")


def worksheet(path: Path) -> None:
    img = Image.new("RGB", (1100, 760), "white")
    d = ImageDraw.Draw(img)
    try:
        title, body = ImageFont.truetype("Arial.ttf", 42), ImageFont.truetype("Arial.ttf", 30)
    except OSError:
        title = body = ImageFont.load_default()
    d.text((60, 40), "Science Worksheet - Class 6", font=title, fill="black")
    d.text((60, 120), "Fill in the table using what you learned about plants.", font=body, fill="black")
    rows = [("Part of plant", "Main job", "Example"), ("Root", "Absorbs water", "Carrot"),
            ("Leaf", "Makes food", "Spinach"), ("Stem", "Carries water", "Sugarcane")]
    x0, y0, widths, h = 60, 200, [300, 360, 300], 90
    for r, row in enumerate(rows):
        x = x0
        for c, cell in enumerate(row):
            d.rectangle([x, y0 + r * h, x + widths[c], y0 + (r + 1) * h], outline="black", width=3)
            d.text((x + 18, y0 + r * h + 28), cell, font=body, fill="black")
            x += widths[c]
    d.text((60, 600), "Question 1: Why do leaves need sunlight?", font=body, fill="black")
    img.save(path)


async def bengali(path: Path) -> None:
    from bodhan_mcp import server

    r = await server.translate_text("bn", text=PARAGRAPH, out_dir=str(OUT / ".work"))
    if not r.get("ok"):
        raise SystemExit(f"translation failed: {r}")
    path.write_text(Path(r["output_path"]).read_text(encoding="utf-8"), encoding="utf-8")


def video(path: Path) -> None:
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=navy:s=320x240:d=3",
                    "-f", "lavfi", "-i", "sine=frequency=330:duration=3", "-shortest", "-c:v", "libx264",
                    "-c:a", "aac", str(path)], check=True)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    shutil.copy(HERE.parents[1] / "sample_data" / "sample_hindi.wav", OUT / "hindi_note.wav")
    worksheet(OUT / "worksheet.png")
    video(OUT / "french_talk.mp4")
    asyncio.run(bengali(OUT / "bengali_paragraph.txt"))
    shutil.rmtree(OUT / ".work", ignore_errors=True)
    for p in sorted(OUT.iterdir()):
        print(f"{p.name:24} {p.stat().st_size:>8} bytes")


if __name__ == "__main__":
    main()
