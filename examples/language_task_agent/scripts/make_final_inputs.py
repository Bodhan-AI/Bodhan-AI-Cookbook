"""Original final acceptance documents. Offline only; generated media stays outside git."""

import argparse
import hashlib
import json
import textwrap
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFont

from bodhan_core.media import command


def font(size):
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default(size=size)


CHAPTER = [
    (
        "Plan the school garden",
        "A fictional school starts a small learning garden beside its greenhouse. The class first draws a map showing paths, beds, a tool shelf and a shaded meeting place. Teams choose a few easy crops rather than filling every space. Their goal is to observe changes, keep careful records and share responsibilities. A clear plan makes it easier to compare what was intended with what actually happened.",
    ),
    (
        "Observe the soil",
        "Before sowing, pupils describe the soil in each bed. They record whether it feels sandy or holds together, and whether rain leaves puddles. They compare samples without assuming that every bed is identical. A label connects each sample with its place on the map. The teacher explains that observations should be written before conclusions. The class changes one condition at a time when making a simple comparison.",
    ),
    (
        "Sow and label seeds",
        "Teams sow beans and peas in separate marked rows. Each label records the crop, sowing date and number of seeds. The class leaves space between rows so people can inspect plants without stepping on the beds. Pupils keep spare labels beside the tool shelf. A missing label creates uncertainty, so they do not invent a date later. They record any mistake and explain which comparison it might affect.",
    ),
    (
        "Water thoughtfully",
        "The garden team checks the soil before watering and records what it finds. It waters the base of young plants gently. A shaded bed may dry differently from a sunny one. Teams compare observations across several days instead of treating one afternoon as a complete pattern. They report a broken watering can or leaking connection. Saving water should mean avoiding waste, rather than ignoring a plant that needs attention.",
    ),
    (
        "Compare light and shade",
        "Pupils mark which parts of the garden receive morning sunlight and which stay shaded. They observe the same places at agreed times. A sketch includes nearby buildings and trees because these can change the shade. They avoid claiming that one day's observation describes every season. When comparing plants, they consider both light and other recorded conditions. A conclusion should say which evidence supports it and what was not measured.",
    ),
    (
        "Use tools and compost carefully",
        "After a lesson, teams clean the tools and return them to the labeled shelf. The teacher supervises the use of sharp tools. Pupils keep walking paths clear and do not taste unfamiliar plants. Suitable plant scraps go into the school's compost system according to its rules. Teams note what they added and when. A tidy shared workspace helps the next class begin safely and find the materials it needs.",
    ),
    (
        "Keep a weekly notebook",
        "Each Friday, teams record plant height, visible leaves and any notable change. Every measurement includes a date and unit. If a plant cannot be measured, the notebook says so rather than filling the gap with a guess. Teams can draw a simple graph after several observations. They compare similar measurements, explain possible sources of error and keep the original records even when an early idea turns out to be wrong.",
    ),
    (
        "Review and share evidence",
        "At the end of the project, each team presents its map, labels and notebook. It explains one observation, one supported conclusion and one remaining question. Teams compare their work respectfully because different beds may have different conditions. The class discusses how its next plan could improve labeling, measurement or shared responsibilities. The garden is a place to learn from evidence, including mistakes, rather than to claim that every prediction was correct.",
    ),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = args.out
    if out.exists() and any(out.iterdir()):
        raise SystemExit("Choose a new/empty fixture directory.")
    chapter, pages, worksheet = out / "chapter", out / "source_pages", out / "worksheet"
    for folder in (chapter, pages, worksheet, out / "impossible", out / "audio_sources"):
        folder.mkdir(parents=True, exist_ok=True)
    with pymupdf.open() as pdf:
        for index, (title, body) in enumerate(CHAPTER, 1):
            image = Image.new("RGB", (1200, 1600), "white")
            draw = ImageDraw.Draw(image)
            draw.text((65, 80), f"{index}. {title}", font=font(34), fill="black")
            for row, line in enumerate(textwrap.wrap(body, width=54)):
                draw.text((65, 190 + row * 51), line, font=font(29), fill="black")
            draw.text(
                (65, 1510), "Original fictional school-garden chapter", font=font(21), fill="gray"
            )
            path = pages / f"page_{index:02d}.png"
            image.save(path)
            page = pdf.new_page(width=595, height=842)
            page.insert_image(page.rect, filename=str(path))
        pdf.save(chapter / "school_garden_scan.pdf")
    image = Image.new("RGB", (1200, 900), "white")
    draw = ImageDraw.Draw(image)
    draw.text((65, 60), "Garden seed inventory", font=font(42), fill="black")
    columns, rows = [65, 260, 615, 880, 1135], [190, 310, 430, 550, 670]
    values = [
        ["Bed", "Seed type", "Seeds sown", "Day"],
        ["A", "Beans", "18", "Monday"],
        ["B", "Peas", "24", "Tuesday"],
        ["C", "Marigold", "30", "Friday"],
    ]
    for x in columns:
        draw.line((x, rows[0], x, rows[-1]), fill="black", width=3)
    for y in rows:
        draw.line((columns[0], y, columns[-1], y), fill="black", width=3)
    for row, cells in enumerate(values):
        for column, cell in enumerate(cells):
            draw.text((columns[column] + 12, rows[row] + 40), cell, font=font(27), fill="black")
    image.save(worksheet / "seed_inventory.png")
    command(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=green:s=320x240:d=1",
            "-c:v",
            "mpeg4",
            str(out / "impossible" / "silent_garden.mp4"),
        ]
    )
    for name, text in {
        "telugu_one": "Three bamboo markers identify the pea beds beside the greenhouse.",
        "telugu_two": "Return the cleaned garden tools to the labeled shelf after class.",
        "hindi": "On Friday the students measure the plants and write the date in their notebooks.",
        "tamil": "The garden team compares sunlight and shade each morning.",
    }.items():
        (out / "audio_sources" / f"{name}.txt").write_text(text, encoding="utf-8")
    manifest = {
        "rights": "Original synthetic English teaching documents and silent video, created for this project. No personal data. Audio source text is original; any translated speech must be produced/verified through Bodhan and labeled synthetic.",
        "chapter": "New eight-page school-garden chapter, distinct from earlier water-cycle inputs and uncertain calls.",
        "files": [
            {
                "path": str(path.relative_to(out)),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in sorted(out.rglob("*"))
            if path.is_file()
        ],
    }
    (out / "rights_and_hashes.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"pages": len(CHAPTER), "files": len(manifest["files"]), "out": str(out)}))


if __name__ == "__main__":
    main()
