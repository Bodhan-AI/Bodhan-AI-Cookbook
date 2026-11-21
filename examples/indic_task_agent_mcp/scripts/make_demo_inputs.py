"""Build the demo inputs in demo_inputs/ (git-ignored). Media is generated or downloaded, never committed.

    python scripts/make_demo_inputs.py            # everything (Telugu clips cost about ₹0.30 of Bodhan credit)
    python scripts/make_demo_inputs.py --no-clips # skip the paid clip generation

#Plan
- chapter/: the NCERT "Lines and Angles" chapter the assignment links, first 8 pages, rendered as a scanned PDF.
- worksheet/: a synthetic worksheet image with a table (drawn with Pillow).
- clips/: five short clips, unnamed by language: three Telugu and one English made with Indic-Translate and
  Indic-Speak through this recipe's own tools, plus the Cookbook's shared Hindi sample.
- video/: a 5 s synthetic MP4 for the impossible French task.
- voice_note/: you record a 2-3 minute Hindi voice note yourself (rights are yours); the script reminds you.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "demo_inputs"
CHAPTER_URL = "https://www.aspirationsinstitute.com/wp-content/uploads/2021/01/9-Maths-NCERT-Book-Lines-and-Angles.pdf"
REPO_HINDI = ROOT.parents[1] / "sample_data" / "sample_hindi.wav"


def chapter(pages: int = 8) -> None:
    import httpx
    import pypdfium2 as pdfium
    from PIL import Image

    dest = OUT / "chapter"
    dest.mkdir(parents=True, exist_ok=True)
    raw = dest / "source.pdf.part"
    try:
        r = httpx.get(CHAPTER_URL, timeout=60, follow_redirects=True)
        r.raise_for_status()
        raw.write_bytes(r.content)
    except Exception as exc:
        print(f"! chapter download failed ({exc}); put an 8-page scanned PDF in {dest} yourself.")
        return
    # Re-render as images so the demo has no text layer, exactly like a scan.
    doc = pdfium.PdfDocument(str(raw))
    imgs = [doc[i].render(scale=150 / 72).to_pil().convert("RGB") for i in range(min(pages, len(doc)))]
    doc.close()
    raw.unlink()
    out = dest / "lines_and_angles_8pages.pdf"
    imgs[0].save(out, save_all=True, append_images=imgs[1:], resolution=150)
    print(f"+ {out} ({len(imgs)} pages, image-only)")
    del Image


def worksheet() -> None:
    from PIL import Image, ImageDraw, ImageFont

    dest = OUT / "worksheet"
    dest.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (1240, 1754), "white")
    d = ImageDraw.Draw(img)

    def font(size):
        for name in ("DejaVuSans.ttf", "Arial.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf"):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default(size=size)

    d.text((90, 90), "Worksheet 6.1: Lines and Angles", fill="black", font=font(48))
    d.text((90, 170), "Name: ____________    Class: 9    Date: ________", fill="black", font=font(30))
    d.text((90, 240), "Fill in the table. Two angles whose sum is 90 degrees are complementary.", fill="black", font=font(28))
    rows = [["Angle pair", "Sum (degrees)", "Type"], ["30 and 60", "90", "Complementary"], ["110 and 70", "180", "Supplementary"],
            ["45 and 45", "90", "?"], ["120 and 60", "?", "?"]]
    x0, y0, widths, h = 90, 320, [380, 330, 350], 80
    for r, row in enumerate(rows):
        x = x0
        for c, cell in enumerate(row):
            d.rectangle([x, y0 + r * h, x + widths[c], y0 + (r + 1) * h], outline="black", width=3)
            d.text((x + 20, y0 + r * h + 22), cell, fill="black", font=font(30))
            x += widths[c]
    d.text((90, 760), "Question 2. Why are vertically opposite angles equal?",
           fill="black", font=font(28))
    out = dest / "worksheet_photo.jpg"
    img.rotate(1.2, expand=True, fillcolor="white").save(out, quality=88)  # slight skew, like a phone photo
    print(f"+ {out}")


async def clips() -> None:
    from dotenv import load_dotenv

    from bodhan_mcp import tools
    from bodhan_mcp.config import Settings

    load_dotenv(ROOT / ".env")
    dest = OUT / "clips"
    dest.mkdir(parents=True, exist_ok=True)
    work = OUT / ".clip_work"
    ctx = tools.ToolContext(Settings(output_dir=work, allowed_dirs=[ROOT], max_cost_inr=1.0))
    lines = {
        "clip_b": ("te", "The library opens at nine in the morning and closes at six in the evening."),
        "clip_c": ("te", "Please submit your science project before Friday."),
        "clip_e": ("te", "The bus to the railway station leaves every twenty minutes."),
        "clip_d": ("en", "Good morning everyone, today we will learn about triangles."),
    }
    for name, (lang, english) in lines.items():
        if lang == "en":
            res = await tools.synthesize_speech(ctx, "en", text=english, gender="male", verify=False, output_name=name)
        else:
            tr = await tools.translate_text(ctx, lang, text=english, output_name=f"{name}_{lang}")
            res = await tools.synthesize_speech(ctx, lang, path=tr.output_path, gender="female", verify=False, output_name=name)
        shutil.copy(res.audio_path, dest / f"{name}.mp3")
        print(f"+ {dest / name}.mp3")
    if REPO_HINDI.exists():
        shutil.copy(REPO_HINDI, dest / "clip_a.wav")
        print(f"+ {dest / 'clip_a.wav'} (Cookbook Hindi sample)")
    await ctx.client.aclose()
    shutil.rmtree(work, ignore_errors=True)
    print(f"  clip generation cost ₹{ctx.ledger.spent:.3f}")


def video() -> None:
    dest = OUT / "video"
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / "lecture_clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=5:size=640x360:rate=25",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=5", "-shortest", "-c:v", "libx264", "-c:a", "aac", str(out)], check=True)
    print(f"+ {out}")


def text_inputs() -> None:
    for folder, name in (("paragraph", "paragraph_photosynthesis.txt"), ("notice", "notice_with_injection.txt")):
        dest = OUT / folder
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / "sample_data" / name, dest / name)
        print(f"+ {dest / name}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-clips", action="store_true", help="skip the paid Telugu clip generation")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    text_inputs()
    worksheet()
    video()
    chapter()
    if not args.no_clips:
        asyncio.run(clips())
    vn = OUT / "voice_note"
    vn.mkdir(exist_ok=True)
    if not any(vn.iterdir()):
        print(f"! Record a 2-3 minute Hindi voice note and save it in {vn} (WAV, MP3 or M4A).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
