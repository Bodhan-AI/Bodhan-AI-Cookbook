"""Build original, openly reusable English demo inputs without model calls."""

import argparse
import json
from pathlib import Path

SOURCES = [
    "Water boils at 100 °C at sea level.",
    "The library opens at 09:30 and closes at 17:00.",
    "Carry 2 litres of water for the walk.",
    "The train leaves platform 4 at 18:15.",
    "A triangle has 3 sides and 3 angles.",
    "The parcel weighs 2.5 kg.",
    "Please submit the form by 25 December 2026.",
    "The classroom has 24 chairs and 6 tables.",
    "The battery must not be heated above 45 °C.",
    "Do not mix the two cleaning liquids.",
    "The meeting was postponed, not cancelled.",
    "The red switch turns the machine off.",
    "Only registered visitors may enter the laboratory.",
    "The fee is ₹250, including the service charge.",
    "The bus runs every 15 minutes on weekdays.",
    "If the light flashes, disconnect the power supply.",
    "The result is approximately 3.14, not exactly 3.",
    "The temperature fell from 30 °C to 22 °C.",
    "Mira gave Arun 5 books and kept 7 books.",
    "The river flows east before turning south.",
    "The museum is closed on Mondays.",
    "A solar panel converts sunlight into electrical energy.",
    "The seed needs moisture, air and warmth to germinate.",
    "Sound cannot travel through a vacuum.",
    "## Materials\n\n- 2 paper cups\n- 1 wooden ruler",
    "Read the [guide](https://example.org/guide) before you begin.",
    "The area is $A = \\pi r^2$ when the radius is $r$.",
    "```python\ncount = 12\nprint(count)\n```\nKeep this code unchanged.",
    "| Item | Quantity |\n|---|---:|\n| Pencil | 12 |\n| Eraser | 3 |",
    "**Warning:** Do not touch the exposed wire.",
    "The first experiment uses a glass container.\n\nThe second experiment uses a metal container.",
    "The morning group measured the rainfall.\n\nThe evening group recorded the wind speed.",
    "Keep the samples in a cool place.\n\nWrite the collection date on each label.",
    "The blue line shows the planned route; the dotted line shows the alternative.",
    "The discount applies to students aged 16 to 21.",
    "Add 0.5 g of salt to 100 mL of water.",
    "The clinic is 750 m from the railway station.",
    "Two workers checked the valve independently before opening it.",
    "This explanation describes a possibility, not a confirmed result.",
    "The software stores records locally unless synchronization is enabled.",
]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="data/demo.jsonl")
    args = parser.parse_args(argv)
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"id": f"demo-{i:03}", "parent_id": f"demo-{i:03}", "source": source, "lang": lang, "provenance": "Original synthetic English source, CC0-1.0"}
        for i, source in enumerate(SOURCES, 1)
        for lang in ("hi", "ta", "bn")
    ]
    output.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    print(f"Wrote {len(rows)} original source records to {output}")


if __name__ == "__main__":
    main()
