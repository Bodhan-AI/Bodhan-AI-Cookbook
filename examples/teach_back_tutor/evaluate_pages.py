"""How well does the app cope with real pages? Two tests, both written to eval/pages.json.

    python evaluate_pages.py photos/ more_photos/page.jpg --pdf book.pdf --pdf-page 20

1. Real pages, end to end: every photo (and the chosen PDF pages) goes through indic-ocr, the lesson card
   and the exercise finder. Records how much text was read, how many card ideas survived the grounding check
   (evidence must be on the page), and whether the app would start a session or refuse the page.
2. Damaged photos, with ground truth: one PDF page is rendered and then blurred, hazed, shrunk, tilted and
   JPEG-compressed. The PDF's own text layer is the answer key, so OCR word recall is measured exactly.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

import bodhan  # noqa: E402
import exercise  # noqa: E402
import lesson  # noqa: E402
from PIL import Image, ImageEnhance, ImageFilter  # noqa: E402

IMAGES = {".png", ".jpg", ".jpeg", ".webp"}


def _tokens(text: str) -> Counter:
    return Counter(re.findall(r"[a-z0-9]+", text.lower()))


def word_recall(truth: str, ocr: str) -> float:
    """Share of the true page's words that indic-ocr read back (multiset, order ignored)."""
    t, o = _tokens(truth), _tokens(ocr)
    return round(sum((t & o).values()) / max(1, sum(t.values())), 3)


def one_page(name: str, pages: list[tuple[bytes, str]]) -> dict:
    img = Image.open(io.BytesIO(pages[0][0]))
    row = {"page": name, "pixels": f"{img.width}x{img.height}", "kb": round(sum(len(b) for b, _ in pages) / 1024)}
    t = time.perf_counter()
    try:
        text = lesson.read_pages(pages)
    except bodhan.BodhanError as err:
        return {**row, "ocr_ok": False, "outcome": f"OCR error: {err}"[:160]}
    ppw = lesson.pixels_per_word(pages, text)
    row.update(ocr_ok=True, ocr_seconds=round(time.perf_counter() - t, 1), ocr_words=len(text.split()),
               ocr_sample=" ".join(text.split()[:30]), pixels_per_word=ppw,
               small_text_warning=ppw < lesson.MIN_PIXELS_PER_WORD)
    try:
        card = lesson.build_card(text)
        kept, dropped = len(card["concepts"]) + len(card["misconceptions"]), len(card["dropped"])
        row.update(card_ok=True, topic=card["topic"], concepts=len(card["concepts"]),
                   tricks=len(card["misconceptions"]), grounded=f"{kept}/{kept + dropped}")
    except ValueError as err:
        row.update(card_ok=False, card_error=str(err)[:120])
    try:
        row["questions_found"] = len(exercise.find_questions(text))
    except ValueError:
        row["questions_found"] = 0
    if row.get("card_ok"):
        row["outcome"] = "Teach session" + (f" + {row['questions_found']} homework questions"
                                            if row["questions_found"] else "")
    elif row["questions_found"]:
        row["outcome"] = f"Homework only ({row['questions_found']} questions)"
    else:
        row["outcome"] = "Refused: asks for a clearer photo"
    return row


def real_pages(paths: list[Path], pdf: Path | None, pdf_page: int, done: list[dict], save) -> list[dict]:
    """Pages already in `done` are skipped (resume after a rate-limit stop); results are saved after each page."""
    inputs = []
    for p in paths:
        files = sorted(f for f in p.iterdir() if f.suffix.lower() in IMAGES) if p.is_dir() else [p]
        inputs += [(f.name, [(f.read_bytes(), f.name)]) for f in files]
    if pdf:
        inputs.append((f"{pdf.name} p.{pdf_page}", lesson.pdf_pages(pdf.read_bytes(), pdf_page, 1)))
    rows = list(done)
    for name, pages in inputs:  # one at a time: each page is several LLM calls
        if any(r["page"] == name for r in done):
            continue
        row = one_page(name, pages)
        rows.append(row)
        save(rows)
        print(f"  {name[:45]:45} {row.get('pixels', ''):10} words={row.get('ocr_words', 0):4} "
              f"card={row.get('grounded', '-'):6} q={row.get('questions_found', 0)}  -> {row['outcome']}")
    return rows


def _jpeg(img: Image.Image, quality: int) -> Image.Image:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=quality)
    return Image.open(io.BytesIO(buf.getvalue()))


DAMAGE = {
    "clean (150 dpi render)": lambda im: im,
    "slight blur": lambda im: im.filter(ImageFilter.GaussianBlur(1.5)),
    "heavy blur": lambda im: im.filter(ImageFilter.GaussianBlur(3)),
    "hazy (dim, low contrast)": lambda im: ImageEnhance.Contrast(ImageEnhance.Brightness(im).enhance(1.3)).enhance(0.35),
    "low resolution (600 px wide)": lambda im: im.resize((600, round(im.height * 600 / im.width))),
    "very low resolution (400 px)": lambda im: im.resize((400, round(im.height * 400 / im.width))),
    "tilted 6 degrees": lambda im: im.rotate(6, expand=True, fillcolor="white"),
    "JPEG quality 10": lambda im: _jpeg(im, 10),
    "hazy + blur + low res": lambda im: ImageEnhance.Contrast(
        im.filter(ImageFilter.GaussianBlur(1.5)).resize((700, round(im.height * 700 / im.width)))).enhance(0.45),
}


def damage_test(pdf: Path, page: int) -> dict:
    import pypdfium2 as pdfium

    data = pdf.read_bytes()
    truth = pdfium.PdfDocument(data)[page - 1].get_textpage().get_text_range()
    base = Image.open(io.BytesIO(lesson.pdf_pages(data, page, 1)[0][0])).convert("RGB")

    def run(item):
        label, fn = item
        buf = io.BytesIO()
        fn(base).convert("RGB").save(buf, "PNG")
        try:
            return label, word_recall(truth, bodhan.ocr_page(buf.getvalue(), "page.png"))
        except bodhan.BodhanError as err:
            return label, f"error: {err}"[:80]

    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = [{"damage": label, "word_recall": r} for label, r in pool.map(run, DAMAGE.items())]
    for r in rows:
        print(f"  {r['damage']:32} recall={r['word_recall']}")
    return {"source": f"{pdf.name} page {page}", "truth_words": sum(_tokens(truth).values()), "rows": rows}


def multi_page_test(pdf: Path, first: int, count: int) -> dict:
    """A lesson spread over several book pages: is each page read well, and does the card use all of them?"""
    import pypdfium2 as pdfium

    data = pdf.read_bytes()
    doc = pdfium.PdfDocument(data)
    pages = lesson.pdf_pages(data, first, count)
    t = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2) as pool:
        texts = list(pool.map(lambda p: bodhan.ocr_page(*p), pages))
    ocr_seconds = round(time.perf_counter() - t, 1)
    per_page = []
    for n, text in zip(range(first, first + len(pages)), texts, strict=True):
        truth = doc[n - 1].get_textpage().get_text_range()
        per_page.append({"page": n, "truth_words": sum(_tokens(truth).values()), "word_recall": word_recall(truth, text)})
    joined = "\n\n".join(f"--- Page {i} ---\n{x}" for i, x in enumerate(texts, 1))
    card = lesson.build_card(joined)
    page_words = [set(lesson._words(x)) for x in texts]

    def source(evidence: str) -> int | None:  # the book page whose words best contain the quote
        scores = [sum(w in pw for w in lesson._words(evidence)) for pw in page_words]
        return first + scores.index(max(scores)) if max(scores) else None

    items = card["concepts"] + card["misconceptions"]
    from_pages = Counter(source(i.get("evidence", "")) for i in items)
    row = {"pages": f"{first}-{first + len(pages) - 1}", "ocr_seconds": ocr_seconds, "per_page": per_page,
           "prompt_words": len(joined.split()), "topic": card["topic"], "concepts": len(card["concepts"]),
           "tricks": len(card["misconceptions"]), "dropped": len(card["dropped"]),
           "card_items_per_page": {str(k): v for k, v in sorted(from_pages.items(), key=lambda kv: kv[0] or 0)},
           "concept_names": [c["name"] for c in card["concepts"]]}
    for p in per_page:
        print(f"  page {p['page']}: {p['truth_words']} words, indic-ocr recall {p['word_recall']}")
    print(f"  card '{row['topic']}': {row['concepts']} ideas + {row['tricks']} tricks, {row['dropped']} dropped; "
          f"items per page {row['card_items_per_page']}; prompt {row['prompt_words']} words")
    return row


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*", type=Path, help="page photos or folders of them")
    ap.add_argument("--pdf", type=Path, help="a PDF book (with a text layer, for the damage test)")
    ap.add_argument("--pdf-page", type=int, default=1)
    ap.add_argument("--skip-real", action="store_true")
    ap.add_argument("--skip-damage", action="store_true")
    ap.add_argument("--fresh", action="store_true", help="redo pages already in eval/pages.json")
    ap.add_argument("--pdf-lesson", help="pages of one multi-page lesson in --pdf, e.g. 16-19")
    args = ap.parse_args()
    path = HERE / "eval" / "pages.json"
    pages = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    ap_fresh = args.fresh

    def save_real(rows: list[dict]) -> None:
        pages["real"] = rows
        path.write_text(json.dumps(pages, ensure_ascii=False, indent=2), encoding="utf-8")

    if not args.skip_real:
        print("Real pages, end to end:")
        pages["real"] = real_pages(args.paths, args.pdf, args.pdf_page,
                                   [] if ap_fresh else pages.get("real", []), save_real)
    if args.pdf and args.pdf_lesson:
        a, b = (int(x) for x in args.pdf_lesson.split("-"))
        print(f"\nOne lesson over pages {a}-{b} of the PDF:")
        pages["multi_page"] = multi_page_test(args.pdf, a, b - a + 1)
        path.write_text(json.dumps(pages, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.pdf and not args.skip_damage:
        print("\nDamaged copies of one page vs its PDF text layer (indic-ocr word recall):")
        pages["damage"] = damage_test(args.pdf, args.pdf_page)
    pages["date"] = date.today().isoformat()
    pages["model"] = os.environ.get("LLM_MODEL", "")
    path.write_text(json.dumps(pages, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {path}")
