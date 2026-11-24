"""Render the synthetic textbook page in sample_data/ (original text, not from any real textbook).

    python make_sample_page.py
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent

TITLE = "Chapter 1: How Plants Make Their Food"
PARAGRAPHS = [
    "All living things need food for energy. Animals eat plants or other animals, "
    "but green plants make their own food. This process is called photosynthesis.",
    "Photosynthesis happens mainly in the leaves. Leaves contain a green pigment called "
    "chlorophyll, which traps energy from sunlight.",
    "To make food, a plant needs three things: sunlight, water and carbon dioxide. The roots "
    "absorb water from the soil, and it travels up the stem to the leaves. Carbon dioxide from "
    "the air enters the leaf through tiny pores called stomata.",
    "Using the energy of sunlight, the leaf combines water and carbon dioxide to make glucose, "
    "a kind of sugar. Oxygen is released into the air as a by-product. Some glucose is used for "
    "energy, and the rest is stored as starch.",
    "Plants do not take food from the soil. The soil gives them water and minerals, but the food "
    "itself is made in the leaves.",
]


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    for name in (("arialbd.ttf", "DejaVuSans-Bold.ttf") if bold else ("arial.ttf", "DejaVuSans.ttf")):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render(out: Path) -> Path:
    img = Image.new("RGB", (1240, 1020), "white")
    draw = ImageDraw.Draw(img)
    y = 70
    draw.text((70, y), TITLE, font=_font(44, bold=True), fill="black")
    y += 100
    body = _font(30)
    for para in PARAGRAPHS:
        for line in textwrap.wrap(para, width=68):
            draw.text((70, y), line, font=body, fill="black")
            y += 44
        y += 30
    out.parent.mkdir(exist_ok=True)
    img.save(out)
    return out


if __name__ == "__main__":
    print(render(HERE / "sample_data" / "photosynthesis_page.png"))
