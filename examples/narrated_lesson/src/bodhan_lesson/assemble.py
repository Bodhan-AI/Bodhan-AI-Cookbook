"""Stage 2: assemble per-page OCR records into one clean source lesson.

Text comes from the page Markdown (clean spacing). The blocks supply labels,
positions and confidence, which drive header/footer removal, figure grouping
and flagging.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path

NO_TEXT_LABELS = {"Diagram", "Figure", "Image", "Picture"}
HEADER_FOOTER_LABELS = {"Header", "Footer", "Page-header", "Page-footer"}
TOP_BAND = 0.15        # top 15% of the page height
BOTTOM_BAND = 0.90     # below 90% of the page height
FIG_CAPTION = re.compile(r"^\s*(Fig\.?|Figure)\s*[\d.]+", re.IGNORECASE)
TERMINAL = tuple(".?!:;।)\"'”’")
CODE_RE = re.compile(r"^\d{3,5}[A-Za-z]{2,}\d{1,3}$")
PAGE_NUMBER_RE = re.compile(r"^\W*\d{1,4}\W*$")
YEAR_RANGE_RE = re.compile(r"^\W*\d{4}\s*[-–]\s*\d{2,4}\W*$")
URL_RE = re.compile(r"(https?://|www\.)\S+", re.IGNORECASE)


# ---------- helpers ----------

def squash(text: str) -> str:
    """Remove all whitespace, so 'twoend points' and 'two end points' compare equal."""
    return re.sub(r"\s+", "", text or "")


def repeat_key(text: str) -> str:
    """Key for spotting running headers: letters only, lower case, digits dropped."""
    return re.sub(r"[\W\d_]+", "", (text or "").lower())


def unwrap(text: str) -> str:
    """Join hard-wrapped lines inside one paragraph; keep blank lines."""
    return re.sub(r"(?<!\n)\n(?!\n)", " ", text).strip()


ITALIC_MATH = re.compile(r"\$([A-Za-z]{3,})\$")


def clean_text(text: str) -> str:
    """Tidy OCR habits: wide spacing, and plain words wrapped in $...$ (italics read as math)."""
    text = re.sub(r"[ \t]{2,}", " ", text)

    def italic(m: re.Match) -> str:
        word = m.group(1)
        # all-caps ($AB$, $PQR$) are geometry names, so they stay math
        return f"*{word}*" if any(c.islower() for c in word) else m.group(0)

    return ITALIC_MATH.sub(italic, text)


def is_catalog_code(text: str) -> bool:
    plain = re.sub(r"\\text\{|\}|\$|\\|\s", "", text or "")
    return bool(CODE_RE.match(plain))


def overlap_ratio(inner: list, outer: list) -> float:
    """Share of `inner`'s area that lies inside `outer`."""
    ix1, iy1, ix2, iy2 = inner
    ox1, oy1, ox2, oy2 = outer
    w = max(0.0, min(ix2, ox2) - max(ix1, ox1))
    h = max(0.0, min(iy2, oy2) - max(iy1, oy1))
    area = max(1.0, (ix2 - ix1) * (iy2 - iy1))
    return (w * h) / area


def attach_markdown_text(markdown: str, blocks: list[dict]) -> None:
    """Give each block `md_text`: its text as it appears in the page Markdown.

    Walks the Markdown once, in block order, comparing with whitespace removed.
    """
    index, chars = [], []
    for i, ch in enumerate(markdown):
        if not ch.isspace():
            chars.append(ch)
            index.append(i)
    flat = "".join(chars)
    cursor = 0
    for b in sorted(blocks, key=lambda x: x.get("order", 0)):
        b["md_text"] = None
        target = squash(b.get("text"))
        if not target:
            continue
        pos = flat.find(target, cursor)
        if pos < 0:
            continue
        b["md_text"] = markdown[index[pos]: index[pos + len(target) - 1] + 1]
        cursor = pos + len(target)


# ---------- per-page processing ----------

def page_height(rec: dict) -> float:
    if rec.get("height"):
        return float(rec["height"])
    ys = [b["bbox_xyxy"][3] for b in rec.get("blocks", [])]
    return max(ys) * 1.1 if ys else 2000.0


def in_edge_band(block: dict, height: float) -> bool:
    y1, y2 = block["bbox_xyxy"][1], block["bbox_xyxy"][3]
    return y2 <= TOP_BAND * height or y1 >= BOTTOM_BAND * height


def find_repeated_keys(pages: list[dict]) -> set[str]:
    """Texts that sit in the top or bottom band on several pages (running headers/footers)."""
    counts: Counter = Counter()
    for rec in pages:
        if rec.get("status") != "ok":
            continue
        h = page_height(rec)
        seen = set()
        for b in rec["blocks"]:
            key = repeat_key(b.get("text"))
            if key and in_edge_band(b, h):
                seen.add(key)
        counts.update(seen)
    need = max(2, math.ceil(0.25 * len(pages)))
    return {k for k, n in counts.items() if n >= need}


def classify_drop(block: dict, height: float, repeated: set[str]) -> str | None:
    """Return a reason if the block is page furniture, else None."""
    label = block.get("label", "")
    text = (block.get("text") or "").strip()
    edge = in_edge_band(block, height)
    if label in HEADER_FOOTER_LABELS and (edge or not text):
        return "header_or_footer_label"
    if edge and text and repeat_key(text) in repeated:
        return "running_header_or_footer"
    if edge and (PAGE_NUMBER_RE.match(text) or YEAR_RANGE_RE.match(text)):
        return "page_number_or_year"
    if URL_RE.search(text) and (edge or len(text) < 120):
        return "website_banner"
    if label not in NO_TEXT_LABELS and is_catalog_code(text):
        return "catalog_code"
    return None


def element_type(label: str) -> str:
    low = label.lower()
    if low == "chapter-title":
        return "title"
    if "title" in low or "heading" in low:
        return "subheading" if "sub" in low else "heading"
    if low == "equation":
        return "equation"
    if "table" in low:
        return "table"
    if "caption" in low:
        return "caption"
    if "example" in low:
        return "example"
    if "question" in low or "exercise" in low:
        return "question"
    if "list" in low:
        return "list"
    return "paragraph"


def process_page(rec: dict, repeated: set[str], report: dict) -> list[dict]:
    """Turn one page record into an ordered list of elements."""
    page = rec["page"]
    height = page_height(rec)
    blocks = sorted(rec["blocks"], key=lambda b: b.get("order", 0))
    attach_markdown_text(rec.get("markdown", ""), blocks)

    kept = []
    for b in blocks:
        report["labels_seen"][b.get("label", "?")] += 1
        if b.get("label") == "Failed-region":      # a region the OCR could not read: keep a visible marker
            kept.append(b)
            continue
        reason = classify_drop(b, height, repeated)
        if reason:
            report["dropped"].append({"page": page, "label": b.get("label"), "reason": reason,
                                      "text": (b.get("text") or "")[:60]})
            continue
        kept.append(b)

    diagrams = [b for b in kept if b.get("label") in NO_TEXT_LABELS]
    absorbed: set[int] = set()
    figures: dict[int, dict] = {}
    for d in diagrams:
        figures[id(d)] = {"labels": [], "caption": None}

    # 1) labels drawn inside a diagram belong to the figure
    for b in kept:
        if b in diagrams:
            continue
        text = (b.get("md_text") or b.get("text") or "").strip()
        small = len(text) <= 40 and b.get("label") in {"Paragraph", "Text"}
        caption_label = element_type(b.get("label", "")) == "caption" and not FIG_CAPTION.match(text)
        if not (small or caption_label):
            continue
        for d in diagrams:
            if overlap_ratio(b["bbox_xyxy"], d["bbox_xyxy"]) >= 0.5:
                figures[id(d)]["labels"].append(b)
                absorbed.add(id(b))
                break

    # 2) the nearest "Fig. x.y" caption becomes the figure's caption
    free_captions = [b for b in kept if id(b) not in absorbed and b not in diagrams
                     and element_type(b.get("label", "")) == "caption"
                     and FIG_CAPTION.match((b.get("md_text") or b.get("text") or ""))]
    for d in diagrams:
        dx1, dy1, dx2, dy2 = d["bbox_xyxy"]
        best, best_gap = None, 260.0
        for c in free_captions:
            if id(c) in absorbed:
                continue
            cx1, cy1, cx2, cy2 = c["bbox_xyxy"]
            if min(cx2, dx2) - max(cx1, dx1) <= 0:
                continue  # no horizontal overlap
            gap = max(cy1 - dy2, dy1 - cy2, 0.0)
            if gap < best_gap:
                best, best_gap = c, gap
        if best is not None:
            figures[id(d)]["caption"] = best
            absorbed.add(id(best))

    elements = []
    for b in kept:
        if id(b) in absorbed:
            continue
        flags = list(b.get("flags", []))
        if b.get("label") == "Failed-region":
            elements.append({"type": "gap", "page": page, "bbox": b["bbox_xyxy"], "flags": ["ocr_failed_region"], "text": ""})
            continue
        if b in diagrams:
            fig = figures[id(b)]
            labels = sorted(fig["labels"], key=lambda x: (round(x["bbox_xyxy"][1] / 40), x["bbox_xyxy"][0]))
            cap = fig["caption"]
            elements.append({
                "type": "figure", "page": page, "bbox": b["bbox_xyxy"], "flags": [],
                "caption": unwrap((cap.get("md_text") or cap.get("text") or "")) if cap else None,
                "labels": [unwrap(x.get("md_text") or x.get("text") or "") for x in labels],
            })
            continue
        text = b.get("md_text")
        if text is None:
            text = b.get("text") or ""
            if text.strip():
                report["alignment_fallbacks"] += 1
        text = text.strip()
        if not text:
            continue
        etype = element_type(b.get("label", ""))
        if etype == "caption":   # a caption with no diagram block next to it
            elements.append({"type": "figure", "page": page, "bbox": b["bbox_xyxy"], "flags": [],
                             "caption": clean_text(unwrap(text)), "labels": []})
            continue
        if etype in {"paragraph", "title", "heading", "subheading", "caption", "example", "question", "list"}:
            text = clean_text(unwrap(text))
        elif etype == "equation":
            text = re.sub(r"[ \t]{2,}", " ", text)
        elements.append({"type": etype, "page": page, "bbox": b["bbox_xyxy"], "flags": flags,
                         "conf": b.get("conf"), "text": text})

    # reading-order sanity check: a block that jumps back up the page without a column change
    prev = None
    for e in elements:
        if prev and e["type"] != "figure" and prev["type"] != "figure":
            if e["bbox"][1] < prev["bbox"][1] - 100 and abs(e["bbox"][0] - prev["bbox"][0]) < 200:
                e["flags"].append("reading_order_suspect")
        prev = e
    return elements


# ---------- whole lesson ----------

def continues_sentence(text: str) -> bool:
    """True if text starts mid-sentence: a lower-case letter, or a letter in an uncased script."""
    ch = text[:1]
    return ch.isalpha() and not ch.isupper()


def merge_across_pages(per_page: list[list[dict]], report: dict) -> list[dict]:
    """Join a paragraph that continues from the end of one page to the start of the next."""
    out: list[dict] = []
    prev_page_last: dict | None = None
    for elements in per_page:
        for i, e in enumerate(elements):
            if (i == 0 and prev_page_last is not None and e["type"] == "paragraph"
                    and prev_page_last["type"] == "paragraph"
                    and max(prev_page_last.get("pages", [prev_page_last["page"]])) == e["page"] - 1
                    and not prev_page_last["text"].rstrip().endswith(TERMINAL)
                    and continues_sentence(e["text"])):
                prev_page_last["text"] = prev_page_last["text"].rstrip() + " " + e["text"].lstrip()
                prev_page_last["flags"] = sorted(set(prev_page_last["flags"] + e["flags"]))
                prev_page_last["pages"] = sorted(set(prev_page_last.get("pages", [prev_page_last["page"]]) + [e["page"]]))
                report["merged_across_pages"].append({"from_page": e["page"] - 1, "to_page": e["page"]})
                continue
            out.append(e)
        if elements:
            prev_page_last = out[-1]
    return out


def slugify(text: str) -> str:
    s = re.sub(r"[^\w]+", "-", text.lower()).strip("-")
    return s[:50] or "section"


def build_sections(elements: list[dict]) -> list[dict]:
    """Split into narration sections at headings. Text before the first heading is the intro."""
    sections: list[dict] = []
    current = {"title": "Introduction", "level": 2, "start": 0}
    for i, e in enumerate(elements):
        if e["type"] == "heading":
            if i > current["start"]:
                current["end"] = i
                sections.append(current)
            current = {"title": e["text"], "level": 2, "start": i}
    current["end"] = len(elements)
    if current["end"] > current["start"]:
        sections.append(current)
    for n, s in enumerate(sections, start=1):
        s["index"] = n
        s["slug"] = slugify(s["title"])
    return sections


def render_markdown(elements: list[dict]) -> str:
    lines: list[str] = []
    i = 0
    while i < len(elements):
        e = elements[i]
        if e["type"] == "title":
            parts = [e["text"]]
            while i + 1 < len(elements) and elements[i + 1]["type"] == "title":
                i += 1
                parts.append(elements[i]["text"])
            lines.append("# " + ": ".join(parts))
        else:
            note = [f for f in e["flags"] if f]
            if note:
                lines.append(f"<!-- flagged (page {e['page']}): {', '.join(note)} -->")
            if e["type"] == "heading":
                lines.append("## " + e["text"])
            elif e["type"] == "subheading":
                lines.append("### " + e["text"])
            elif e["type"] == "gap":
                pass                       # the flag comment above is the marker
            elif e["type"] == "figure":
                head = e["caption"] or "Figure"
                block = [f"> **{head}**"]
                if e["labels"]:
                    block.append("> Labels in the figure: " + "; ".join(e["labels"]))
                lines.append("\n".join(block))
            else:
                lines.append(e["text"])
        lines.append("")
        i += 1
    return "\n".join(lines).strip() + "\n"


def assemble(pages: list[dict]) -> tuple[list[dict], list[dict], str, dict]:
    report = {"labels_seen": Counter(), "dropped": [], "merged_across_pages": [],
              "alignment_fallbacks": 0, "failed_pages": [], "flagged": []}
    ok_pages = [p for p in pages if p.get("status") == "ok"]
    repeated = find_repeated_keys(pages)
    per_page = []
    for rec in sorted(pages, key=lambda r: r["page"]):
        if rec.get("status") != "ok":
            report["failed_pages"].append({"page": rec["page"], "error": rec.get("error")})
            continue
        per_page.append(process_page(rec, repeated, report))
    elements = merge_across_pages(per_page, report)
    for e in elements:
        if e["flags"]:
            report["flagged"].append({"page": e["page"], "type": e["type"], "flags": e["flags"],
                                      "text": (e.get("text") or "")[:80]})
    report["labels_seen"] = dict(report["labels_seen"])
    report["pages_ok"] = len(ok_pages)
    sections = build_sections(elements)
    return elements, sections, render_markdown(elements), report


def run_assemble(source_dir: Path) -> dict:
    """Read source/pages/*.json, write lesson.src.md, structure and the assembly report."""
    pages = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((source_dir / "pages").glob("page_*.json"))]
    if not pages:
        raise ValueError(f"No OCR page records in {source_dir / 'pages'}")
    elements, sections, markdown, report = assemble(pages)
    (source_dir / "lesson.src.md").write_text(markdown, encoding="utf-8")
    (source_dir / "lesson.structure.json").write_text(
        json.dumps({"elements": elements, "sections": sections}, ensure_ascii=False, indent=2), encoding="utf-8")
    (source_dir / "assembly_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Run the assembly stage on existing OCR output.")
    ap.add_argument("--out", default="out")
    args = ap.parse_args()
    rep = run_assemble(Path(args.out) / "source")
    print("labels seen:", rep["labels_seen"])
    print("dropped:", len(rep["dropped"]), "| merged across pages:", len(rep["merged_across_pages"]),
          "| flagged elements:", len(rep["flagged"]), "| failed pages:", len(rep["failed_pages"]))
    for d in rep["dropped"]:
        print("  dropped p%s %-14s %-24s %r" % (d["page"], d["label"], d["reason"], d["text"]))
    for f in rep["flagged"]:
        print("  FLAGGED p%s %-10s %s %r" % (f["page"], f["type"], f["flags"], f["text"]))