"""Textbook page -> lesson card.

The card is built once per session and every later turn works only from it:
key concepts the student should explain, plausible misconceptions the buddy
can plant, and the evidence on the page for each.

    python lesson.py sample_data/photosynthesis_page.png
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import bodhan
import llm

CARD_PROMPT = """You prepare a "lesson card" for a study buddy app. A school student will explain this \
textbook lesson (one or more pages) out loud to an AI classmate, and the AI will check their explanation \
against the card.

Use ONLY what the page says. Do not add facts that are not on the page.

Return a JSON object with exactly these keys:
- "topic": short title of the lesson.
- "summary": one or two simple sentences a 10-year-old could follow.
- "concepts": 3 to 5 of the most important ideas the student should be able to explain. Each item:
    {{"id": "c1", "name": "<2-5 words>", "explanation": "<one simple sentence>",
      "evidence": "<a short phrase copied word for word from the page that supports it>"}}
- "misconceptions": 3 or 4 believable WRONG statements a confused classmate might say about this page. \
Each must be clearly contradicted by the page. Each item:
    {{"id": "m1", "concept_id": "<id of the concept it is about>",
      "wrong_statement": "<said confidently, as a classmate would, in one sentence>",
      "correction": "<the right idea in one simple sentence>",
      "evidence": "<a short phrase copied word for word from the page that proves it wrong>",
      "difficulty": "easy" | "medium" | "hard"}}
  Mix difficulties: at least one easy, at least one medium or hard.

Textbook page:
\"\"\"
{page}
\"\"\""""


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def is_grounded(evidence: str, page_words: set[str], threshold: float = 0.8) -> bool:
    """True when most of the evidence's words appear on the page (tolerates OCR line breaks and punctuation)."""
    words = _words(evidence)
    if not words:
        return False
    return sum(w in page_words for w in words) / len(words) >= threshold


def build_card(page_text: str) -> dict:
    card = llm.chat_json([{"role": "user", "content": CARD_PROMPT.format(page=page_text)}])

    page_words = set(_words(page_text))
    dropped = []
    concepts = []
    for c in card.get("concepts", []):
        (concepts if is_grounded(c.get("evidence", ""), page_words) else dropped).append(c)
    concept_ids = {c["id"] for c in concepts}
    misconceptions = []
    for m in card.get("misconceptions", []):
        ok = m.get("concept_id") in concept_ids and is_grounded(m.get("evidence", ""), page_words)
        (misconceptions if ok else dropped).append(m)

    if len(concepts) < 2 or not misconceptions:
        raise ValueError("Could not build a lesson card grounded in this page. Try a clearer photo of a text-heavy page.")

    card["concepts"] = concepts
    card["misconceptions"] = misconceptions
    card["dropped"] = dropped
    return card


CARDS_DIR = Path(__file__).resolve().parent / "outputs" / "cards"
MAX_PAGES = 4  # one lesson card per session; more pages would not fit in a 12-turn conversation


def card_from_images(pages: list[tuple[bytes, str]]) -> tuple[str, dict]:
    """One lesson card for one or more page photos ([(image_bytes, filename), ...]).

    Cached per set of images, so the same pages always get the same card: student memory refers to
    its concept names, and a rebuilt card could name them differently."""
    pages = pages[:MAX_PAGES]
    digest = hashlib.sha256(b"".join(hashlib.sha256(img).digest() for img, _ in pages)).hexdigest()[:16]
    cached = CARDS_DIR / f"{digest}.json"
    if cached.exists():
        data = json.loads(cached.read_text(encoding="utf-8"))
        return data["page_text"], data["card"]
    with ThreadPoolExecutor(max_workers=MAX_PAGES) as pool:  # pages are independent; keep their order
        texts = list(pool.map(lambda page: bodhan.ocr_page(*page), pages))
    page_text = "\n\n".join(texts) if len(texts) == 1 else "\n\n".join(
        f"--- Page {i} ---\n{t}" for i, t in enumerate(texts, 1))
    card = build_card(page_text)
    CARDS_DIR.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps({"page_text": page_text, "card": card}, ensure_ascii=False, indent=2), encoding="utf-8")
    return page_text, card


def card_from_image(image_bytes: bytes, filename: str = "page.png") -> tuple[str, dict]:
    return card_from_images([(image_bytes, filename)])


if __name__ == "__main__":
    from dotenv import load_dotenv

    here = Path(__file__).resolve().parent
    load_dotenv(here / ".env")
    path = Path(sys.argv[1] if len(sys.argv) > 1 else here / "sample_data" / "photosynthesis_page.png")
    text, card = card_from_image(path.read_bytes(), path.name)
    out = here / "outputs" / "lesson_card.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"page_text": text, "card": card}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(card, ensure_ascii=False, indent=2))
    print(f"\nsaved {out}")
