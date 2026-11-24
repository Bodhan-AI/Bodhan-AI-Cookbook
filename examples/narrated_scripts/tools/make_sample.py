"""Generate two original English lesson pages for local development."""

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def make_sample(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    heading = ImageFont.load_default(size=48)
    body = ImageFont.load_default(size=30)
    small = ImageFont.load_default(size=24)

    for number in (1, 2):
        image = Image.new("RGB", (1200, 1500), "white")
        draw = ImageDraw.Draw(image)
        if number == 1:
            draw.text((90, 105), "Angles on a straight line", font=heading, fill="black")
            draw.multiline_text(
                (90, 215),
                "A straight line makes an angle of 180 degrees.\n"
                "Place a point on the line and draw a ray from that point.\n"
                "The ray divides the straight angle into two parts.\n\n"
                "A classroom example",
                font=body, spacing=18, fill="black",
            )
            draw.line([(210, 680), (900, 680)], fill="black", width=5)
            draw.line([(510, 680), (690, 475)], fill="black", width=5)
            draw.text((490, 700), "O", font=body, fill="black")
            draw.text((200, 752), "Figure 1. A ray divides a straight angle.", font=small, fill="black")
            draw.multiline_text(
                (90, 1050), "If the smaller angle measures 65 degrees, then the",
                font=body, fill="black",
            )
        else:
            draw.text((90, 120), "larger angle measures 115 degrees.", font=body, fill="black")
            draw.text((90, 230), "Checking the sum", font=heading, fill="black")
            draw.text((90, 330), "65° + 115° = 180°", font=body, fill="black")
            draw.text((90, 480), "Table 1. Two pairs of adjacent angles", font=small, fill="black")
            for x in (90, 400, 710):
                draw.line([(x, 525), (x, 720)], fill="black", width=2)
            for row, values in enumerate((("Smaller angle", "Larger angle"),
                                           ("65°", "115°"), ("80°", "100°"))):
                y = 525 + 65 * row
                draw.line([(90, y), (710, y)], fill="black", width=2)
                for column, label in enumerate(values):
                    draw.text((110 + 310 * column, y + 15), label, font=body, fill="black")
            draw.line([(90, 720), (710, 720)], fill="black", width=2)
            draw.text((90, 825), "Try another pair whose sum is 180 degrees.", font=body, fill="black")
        draw.text((90, 1400), "Classroom geometry notes", font=small, fill="black")
        draw.text((1080, 1400), str(number), font=small, fill="black")
        image.save(out / f"page-{number}.png")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    make_sample(parser.parse_args().out)


if __name__ == "__main__":
    main()
