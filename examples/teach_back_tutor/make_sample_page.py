"""Render the synthetic textbook pages in sample_data/ (original text, not from any real textbook):
a lesson page for Teach mode and an exercise page (questions, no answers) for Homework mode.

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


EXERCISE_TITLE = "Exercises: How Plants Make Their Food"
EXERCISES = [
    "1. A leaf makes 12 grams of glucose in one day. It uses one third of it for energy and stores the "
    "rest as starch. How many grams of starch does it store in 5 days?",
    "2. Riya keeps one potted plant in a dark cupboard and one on a sunny window for two weeks, and waters "
    "both. Which plant will be healthier, and why?",
    "3. A student says, \"Plants get their food from the soil.\" Use what you have learnt to explain "
    "whether the student is right.",
    "4. A plant is covered with a glass jar so that no fresh air can reach it. Which ingredient for "
    "making food will run out first, and what will happen to the plant?",
]


def render(out: Path, title: str = TITLE, paragraphs: list[str] = PARAGRAPHS) -> Path:
    img = Image.new("RGB", (1240, 1020), "white")
    draw = ImageDraw.Draw(img)
    y = 70
    draw.text((70, y), title, font=_font(44, bold=True), fill="black")
    y += 100
    body = _font(30)
    for para in paragraphs:
        for line in textwrap.wrap(para, width=68):
            draw.text((70, y), line, font=body, fill="black")
            y += 44
        y += 30
    out.parent.mkdir(exist_ok=True)
    img.save(out)
    return out


if __name__ == "__main__":
    print(render(HERE / "sample_data" / "photosynthesis_page.png"))
    print(render(HERE / "sample_data" / "exercise_page.png", EXERCISE_TITLE, EXERCISES))
