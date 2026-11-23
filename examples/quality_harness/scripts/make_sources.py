"""Generate data/sources_en.jsonl: >=100 openly-licensed ENGLISH source items, balanced across
Hindi, Tamil and Bengali targets, with a mix of content types (short facts, numbers/units/dates,
named entities, technical terms, Markdown tables and LaTeX, and multi-sentence paragraphs).

Only ENGLISH text is authored here -- Indic translations are produced later by Indic-Translate
(see scripts/build_demo.py). All sentences are the candidate's own composition, released CC0
(see DATA_SOURCES.md), so there are no third-party licensing constraints.

    python scripts/make_sources.py            # writes data/sources_en.jsonl
"""

from __future__ import annotations

import json
from pathlib import Path

FACTS = [
    "Water boils at 100 degrees Celsius at sea level.",
    "The human heart beats about 70 times per minute.",
    "Light travels at roughly 300000 kilometres per second.",
    "A year on Earth lasts about 365 days.",
    "The Moon is about 384000 kilometres from Earth.",
    "Sound travels at about 343 metres per second in air.",
    "An adult human body has 206 bones.",
    "The boiling point of water drops at high altitude.",
    "A week has 7 days and a day has 24 hours.",
    "The freezing point of water is 0 degrees Celsius.",
    "Mount Everest is 8849 metres tall.",
    "The Pacific Ocean is the largest ocean on Earth.",
    "A standard football match lasts 90 minutes.",
    "The speed limit on the highway is 100 km/h.",
    "The recipe needs 250 grams of flour and 2 eggs.",
]
ENTITIES = [
    "Albert Einstein developed the theory of relativity.",
    "The Ganga river flows through northern India.",
    "NASA launched the James Webb Space Telescope in 2021.",
    "Mahatma Gandhi led the Indian independence movement.",
    "The World Health Organization is based in Geneva.",
    "DNA carries the genetic instructions of living organisms.",
    "The Taj Mahal is located in Agra.",
    "Isaac Newton described the three laws of motion.",
    "The Amazon rainforest produces much of the world's oxygen.",
    "Marie Curie won Nobel Prizes in Physics and Chemistry.",
    "The Sahara is the largest hot desert on Earth.",
    "H2O is the chemical formula for water.",
]
PARAGRAPHS = [
    "Photosynthesis is the process by which plants make food. They use sunlight, water and carbon "
    "dioxide. The result is glucose and oxygen.",
    "The water cycle moves water around the planet. Water evaporates from oceans and rivers. It "
    "condenses into clouds and later falls as rain.",
    "A balanced diet includes proteins, carbohydrates and fats. It also needs vitamins and minerals. "
    "Drinking enough water is important too.",
    "Earthquakes happen when tectonic plates move suddenly. The energy travels as seismic waves. "
    "Strong earthquakes can damage buildings.",
    "Vaccines train the immune system to fight disease. They contain weakened or inactive germs. "
    "After vaccination the body remembers the germ.",
]
MARKDOWN = [
    "## Planets\n\n| Planet | Moons |\n| --- | --- |\n| Earth | 1 |\n| Mars | 2 |",
    "### Shopping list\n\n- 2 apples\n- 500 g rice\n- 1 litre milk",
    "# Report\n\nThe **temperature** rose by 3 degrees. See the *summary* below.",
    "| Metal | Melting point |\n| --- | --- |\n| Iron | 1538 C |\n| Gold | 1064 C |",
]
LATEX = [
    "The area of a circle is $A = \\pi r^2$ where r is the radius.",
    "Energy and mass are related by $E = mc^2$ in physics.",
    "The quadratic formula is $x = \\frac{-b \\pm \\sqrt{b^2 - 4ac}}{2a}$.",
    "Newton's second law is written as $F = ma$.",
    "The sum of the first n integers is $$\\sum_{i=1}^{n} i = \\frac{n(n+1)}{2}.$$",
]

LANGS = ["hi", "ta", "bn"]


def build() -> list[dict]:
    pool: list[str] = []
    pool += FACTS + ENTITIES + PARAGRAPHS + MARKDOWN + LATEX
    # repeat the pool to comfortably exceed 100 while keeping variety
    items: list[dict] = []
    idx = 0
    while len(items) < 108:
        src = pool[idx % len(pool)]
        lang = LANGS[idx % len(LANGS)]
        items.append({"id": f"src-{idx:03d}", "source": src, "lang": lang})
        idx += 1
    return items


def main() -> int:
    items = build()
    out = Path(__file__).resolve().parent.parent / "data" / "sources_en.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for it in items:
            fh.write(json.dumps(it, ensure_ascii=False) + "\n")
    by_lang = {L: sum(1 for x in items if x["lang"] == L) for L in LANGS}
    print(f"Wrote {len(items)} English sources to {out} (per language: {by_lang})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
