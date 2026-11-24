"""Textbook page -> lesson card.

The card is built once per session and every later turn works only from it:
key concepts the student should explain, plausible misconceptions the buddy
can plant, and the evidence on the page for each.

    python lesson.py sample_data/photosynthesis_page.png
"""

from __future__ import annotations

import hashlib
import io
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


def card_from_images(pages: list[tuple[bytes, str]], page_text: str | None = None) -> tuple[str, dict]:
    """One lesson card for one or more page photos ([(image_bytes, filename), ...]).

    Cached per set of images, so the same pages always get the same card: student memory refers to
    its concept names, and a rebuilt card could name them differently. Pass `page_text` if the pages
    were already read, to skip a second OCR."""
    pages = pages[:MAX_PAGES]
    digest = hashlib.sha256(b"".join(hashlib.sha256(img).digest() for img, _ in pages)).hexdigest()[:16]
    cached = CARDS_DIR / f"{digest}.json"
    if cached.exists():
        data = json.loads(cached.read_text(encoding="utf-8"))
        return data["page_text"], data["card"]
    page_text = page_text or read_pages(pages)
    card = build_card(page_text)
    CARDS_DIR.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps({"page_text": page_text, "card": card}, ensure_ascii=False, indent=2), encoding="utf-8")
    return page_text, card


MAX_SIDE = 2400  # phone photos are often 4000+ px; more pixels only make the upload slower


def prepare_image(image_bytes: bytes, filename: str) -> tuple[bytes, str]:
    """Turn a phone photo the right way up (EXIF) and into a plain PNG/JPEG indic-ocr accepts.

    Files that are already upright PNG/JPEG of a sensible size are passed through unchanged."""
    from PIL import Image, ImageOps

    img = Image.open(io.BytesIO(image_bytes))
    rotated = img.getexif().get(0x0112, 1) != 1  # EXIF orientation tag
    if img.format in ("PNG", "JPEG") and not rotated and max(img.size) <= MAX_SIDE:
        return image_bytes, filename
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=90)
    return out.getvalue(), Path(filename).stem + ".jpg"


MIN_PIXELS_PER_WORD = 500  # below this the letters are only a few pixels tall and indic-ocr starts misreading


def pixels_per_word(pages: list[tuple[bytes, str]], text: str) -> int:
    """Image pixels per word read: a cheap sign that the text was too small to read reliably.

    The lesson card is grounded in the OCR text, so a misread page still "grounds" (in the wrong words);
    this catches that case. On the test pages, the two misread photos had ~250 and ~440, clean pages 850+."""
    from PIL import Image

    pixels = sum(Image.open(io.BytesIO(img)).size[0] * Image.open(io.BytesIO(img)).size[1] for img, _ in pages)
    return pixels // max(1, len(text.split()))


def pdf_page_count(pdf_bytes: bytes) -> int:
    import pypdfium2 as pdfium

    return len(pdfium.PdfDocument(pdf_bytes))


def pdf_pages(pdf_bytes: bytes, first: int = 1, count: int = MAX_PAGES, dpi: int = 150) -> list[tuple[bytes, str]]:
    """Render pages first..first+count-1 (1-based) of a PDF to PNG, so they go through indic-ocr like photos.

    Rendering (not the PDF's text layer) keeps scanned books working, and keeps one reading path."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(pdf_bytes)
    last = min(len(pdf), first + min(count, MAX_PAGES) - 1)
    pages = []
    for n in range(first, last + 1):
        buf = io.BytesIO()
        pdf[n - 1].render(scale=dpi / 72).to_pil().save(buf, "PNG")
        pages.append((buf.getvalue(), f"page_{n}.png"))
    return pages


def read_pages(pages: list[tuple[bytes, str]]) -> str:
    """indic-ocr on each page photo, in parallel, joined in reading order."""
    with ThreadPoolExecutor(max_workers=MAX_PAGES) as pool:
        texts = list(pool.map(lambda page: bodhan.ocr_page(*prepare_image(*page)), pages[:MAX_PAGES]))
    if len(texts) == 1:
        return texts[0]
    return "\n\n".join(f"--- Page {i} ---\n{t}" for i, t in enumerate(texts, 1))


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
