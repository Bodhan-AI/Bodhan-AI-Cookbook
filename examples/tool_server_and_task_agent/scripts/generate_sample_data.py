"""Generate the synthetic sample_data/ inputs (no real recordings, no
personal data). Re-run any time to regenerate:

    python scripts/generate_sample_data.py

Outputs (all < 1 MB):
- voice_note_hi.wav      3 s tone (Hindi voice note; mock transcript keyed
                         by this file name)
- clips/clip_01..05.wav  2 s tones each (Telugu, Hindi, Telugu, English,
                         Tamil; mock transcripts keyed by file name)
- chapter.pdf            2-page NCERT-style "Lines and Angles" chapter
- chapter.page01.png     page 1 as an image (text drawn on it)
- chapter.page02.png     page 2 as an image
- worksheet.png          marks table as an image
- paragraph.txt          short English paragraph
- sample_video.mp4       1 s black video (for the impossible-task demo)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from bodhan_mcp import media  # noqa: E402

SAMPLE = ROOT / "sample_data"

PAGE1_LINES = [
    "Lines and Angles",
    "",
    "A line is straight and goes on forever in both",
    "directions. Two lines that meet form an angle.",
    "",
    "Types of angles",
    "",
    "- Acute angle: less than 90 degrees",
    "- Right angle: exactly 90 degrees",
    "- Obtuse angle: more than 90 degrees",
]

PAGE2_LINES = [
    "Parallel lines",
    "",
    "Parallel lines never meet. When a third line crosses",
    "them, the angles formed follow simple rules.",
    "",
    "angle 1 = angle 2",
]

WORKSHEET_LINES = [
    "Worksheet: Marks",
    "",
    "Name      Marks",
    "Asha      85",
    "Ravi      92",
    "",
    "Write the total marks.",
]


def make_wavs() -> None:
    clips = [
        ("voice_note_hi.wav", 3.0, 523.0),
        ("clips/clip_01.wav", 2.0, 392.0),
        ("clips/clip_02.wav", 2.0, 440.0),
        ("clips/clip_03.wav", 2.0, 494.0),
        ("clips/clip_04.wav", 2.0, 330.0),
        ("clips/clip_05.wav", 2.0, 587.0),
    ]
    for name, seconds, freq in clips:
        media.synth_tone_wav(SAMPLE / name, seconds, freq=freq)
        print(f"wrote {name}")


def make_pdf() -> None:
    import fitz

    doc = fitz.open()
    for lines in (PAGE1_LINES, PAGE2_LINES):
        page = doc.new_page(width=595, height=842)
        y = 72
        for line in lines:
            size = 20 if line in ("Lines and Angles", "Parallel lines") else 12
            if line:
                page.insert_text((72, y), line, fontsize=size, fontname="helv")
            y += 26 if size == 20 else 18
    doc.save(str(SAMPLE / "chapter.pdf"))
    print("wrote chapter.pdf")


def _draw_png(name: str, lines: list[str]) -> None:
    from PIL import Image, ImageDraw, ImageFont

    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    try:
        title_font = ImageFont.truetype(font_path, 28)
        body_font = ImageFont.truetype(font_path, 20)
    except OSError:
        title_font = body_font = ImageFont.load_default()
    img = Image.new("RGB", (900, 560), "white")
    draw = ImageDraw.Draw(img)
    y = 30
    for line in lines:
        is_title = line in ("Lines and Angles", "Parallel lines", "Worksheet: Marks")
        font = title_font if is_title else body_font
        if line:
            draw.text((40, y), line, fill="black", font=font)
        y += 44 if font is title_font else 32
    img.save(str(SAMPLE / name))
    print(f"wrote {name}")


def make_images() -> None:
    _draw_png("chapter.page01.png", PAGE1_LINES)
    _draw_png("chapter.page02.png", PAGE2_LINES)
    _draw_png("worksheet.png", WORKSHEET_LINES)


def make_text() -> None:
    (SAMPLE / "paragraph.txt").write_text(
        "Hello, my name is Sujal. Water boils at 100 degrees Celsius.\n",
        encoding="utf-8",
    )
    print("wrote paragraph.txt")


def make_video() -> None:
    out = SAMPLE / "sample_video.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "color=c=black:s=320x240:d=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out),
        ],
        check=True,
    )
    print("wrote sample_video.mp4")


def main() -> None:
    (SAMPLE / "clips").mkdir(parents=True, exist_ok=True)
    make_wavs()
    make_pdf()
    make_images()
    make_text()
    make_video()


if __name__ == "__main__":
    main()
