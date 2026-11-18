"""Draw a small synthetic exam paper so the recipe has an input with no real student data.

    python make_sample_paper.py   ->   sample_data/sample_paper.png
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

LINES = [
    ("Class 8 Science - Unit Test", 40),
    ("Time: 30 minutes        Maximum marks: 10", 26),
    ("", 26),
    ("1. What is photosynthesis? Name the gas that plants release.", 28),
    ("2. Why does an iron nail rust faster in the rainy season?", 28),
    ("3. State two differences between a plant cell and an animal cell.", 28),
    ("4. A car travels 120 kilometres in 2 hours. Find its speed.", 28),
]


def load_font(size: int) -> ImageFont.ImageFont:
    for name in ("arial.ttf", "DejaVuSans.ttf", "Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def main() -> None:
    image = Image.new("RGB", (1100, 520), "white")
    draw = ImageDraw.Draw(image)
    y = 40
    for text, size in LINES:
        draw.text((50, y), text, font=load_font(size), fill="black")
        y += size + 30
    out = Path(__file__).parent / "sample_data" / "sample_paper.png"
    image.save(out)
    print(f"saved {out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
