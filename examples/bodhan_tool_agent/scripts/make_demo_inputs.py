"""Create small, rights-free demo inputs in ./demo_inputs (uses your Bodhan keys for the audio, a few paise).

    python scripts/make_demo_inputs.py
"""
import asyncio
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from bodhan_mcp.config import load_env  # noqa: E402

load_env()
OUT = Path("demo_inputs").resolve()
OUT.mkdir(exist_ok=True)
os.environ["BODHAN_WORK_DIR"] = str(OUT)

from PIL import Image, ImageDraw, ImageFont  # noqa: E402


def font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def page(lines, path, table=None):
    img = Image.new("RGB", (1000, 1300), "white")
    d = ImageDraw.Draw(img)
    y = 60
    for text, size in lines:
        d.text((60, y), text, fill="black", font=font(size))
        y += size + 24
    if table:
        y += 20
        x0, cw, rh = 60, 280, 60
        for r, row in enumerate(table):
            for c, cell in enumerate(row):
                d.rectangle([x0 + c * cw, y + r * rh, x0 + (c + 1) * cw, y + (r + 1) * rh], outline="black", width=2)
                d.text((x0 + c * cw + 12, y + r * rh + 14), cell, fill="black", font=font(28))
    img.save(path)


def main():
    # 1. an 3-page "scanned chapter" PDF (English)
    pages = []
    texts = [
        [("Chapter 6: Lines and Angles", 44), ("6.1 Introduction", 34),
         ("A line has length but no breadth. A point has no dimensions.", 28),
         ("Two lines that never meet are called parallel lines.", 28)],
        [("6.2 Angles", 34), ("An angle is formed when two rays share an end point.", 28),
         ("A right angle measures 90 degrees. A straight angle measures 180 degrees.", 28)],
        [("6.3 Summary", 34), ("Vertically opposite angles are equal.", 28),
         ("The angles of a linear pair add up to 180 degrees.", 28)],
    ]
    for i, t in enumerate(texts):
        p = OUT / f"_p{i}.png"
        page(t, p)
        pages.append(Image.open(p).convert("RGB"))
    pages[0].save(OUT / "chapter.pdf", save_all=True, append_images=pages[1:])
    for i in range(len(texts)):
        (OUT / f"_p{i}.png").unlink()
    # 2. a worksheet photo with a table
    page([("Worksheet: Angles", 40), ("Fill in the table.", 28)], OUT / "worksheet.png",
         table=[["Angle", "Degrees"], ["Right", "90"], ["Straight", "180"], ["Full turn", "360"]])
    # 3. a text file
    (OUT / "paragraph.txt").write_text(
        "Water is essential for life. Every plant and animal needs water to survive. "
        "We should save water every day.", encoding="utf-8")
    # 4. a tiny MP4 (tone over colour bars) for the impossible 'translate this video into French' task
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=3",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-shortest", "-pix_fmt", "yuv420p",
                    str(OUT / "video.mp4")], check=True)
    # 5. audio clips made with Indic-Speak itself (so no recordings are needed)
    from bodhan_mcp import tools

    async def audio():
        clips = {
            "voice_note_hindi.wav": ("hi", "नमस्ते, मैं कल शाम पाँच बजे बाज़ार जाऊँगा। मुझे तीन किलो सेब और दो किलो केले चाहिए।"),
            "clip1_telugu.wav": ("te", "నమస్కారం, ఈ రోజు వాతావరణం చాలా బాగుంది."),
            "clip2_tamil.wav": ("ta", "வணக்கம், இன்று பள்ளி காலை ஒன்பது மணிக்கு தொடங்குகிறது."),
            "clip3_telugu.wav": ("te", "మా ఊరిలో పెద్ద చెరువు ఉంది."),
            "clip4_hindi.wav": ("hi", "आज मौसम बहुत अच्छा है।"),
        }
        for name, (lang, text) in clips.items():
            r = await tools.synthesize_speech(lang, text=text, output_path=name, loudness_normalize=False)
            print("made", name, f"{r.duration_s}s")
    asyncio.run(audio())
    for junk in list(OUT.glob("*.spoken.txt")) + list(OUT.glob("intermediate")):
        if junk.is_file():
            junk.unlink()
    print("\nDemo inputs are in", OUT)


if __name__ == "__main__":
    main()
