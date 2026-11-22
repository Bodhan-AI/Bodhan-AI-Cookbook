"""Deterministic OCR block cleanup (no LLM).

Drops page furniture and obvious junk; flags low-confidence blocks.
Never invents or rewrites OCR text.
"""

from __future__ import annotations

import re
from typing import Any

from bodhan_lesson.constants import LOW_CONF_THRESHOLD

# Labels that are never part of the lesson body.
FURNITURE_LABELS = {
    "Header",
    "Footer",
    "Page-number",
    "Folio",
    "Website-link",
    "Advertisement",
}

# Edition / QR-style codes OCR often mislabels as Paragraph (e.g. 0962CH06).
_EDITION_CODE_RE = re.compile(
    r"^\$?\s*\d{3,5}\s*\\?text\{?\s*CH\s*\}?\s*\d{1,3}\s*\$?$",
    re.IGNORECASE,
)
_EDITION_CODE_PLAIN_RE = re.compile(r"^\d{3,5}\s*CH\s*\d{1,3}$", re.IGNORECASE)
_URL_RE = re.compile(r"(https?://|www\.)", re.IGNORECASE)
_BARE_YEAR_RE = re.compile(r"^(19|20)\d{2}$")
_LATEX_NOISE_RE = re.compile(r"\\text\{|\$|[{}]")


def block_id(block: dict[str, Any]) -> str:
    """Stable id for a block (OCR `order` when present)."""
    if "id" in block and block["id"] is not None:
        return str(block["id"])
    order = block.get("order")
    if order is None:
        raise ValueError("block needs id or order")
    return str(order)


def normalize_preview(text: str, *, max_len: int = 80) -> str:
    """Short single-line preview for logs / LLM prompts."""
    flat = " ".join((text or "").split())
    if len(flat) <= max_len:
        return flat
    return flat[: max_len - 1] + "…"


def _plain_text(text: str) -> str:
    """Strip light LaTeX wrappers for junk matching."""
    cleaned = _LATEX_NOISE_RE.sub("", text or "")
    return " ".join(cleaned.split()).strip()


def is_furniture_label(label: str | None) -> bool:
    return (label or "") in FURNITURE_LABELS


def is_edition_code(text: str) -> bool:
    """True for short textbook edition / QR codes like 0962CH06."""
    raw = (text or "").strip()
    plain = _plain_text(raw)
    if _EDITION_CODE_PLAIN_RE.match(plain.replace(" ", "")):
        return True
    compact = re.sub(r"\s+", "", raw)
    return bool(_EDITION_CODE_RE.match(compact) or _EDITION_CODE_PLAIN_RE.match(plain))


def is_url_line(text: str) -> bool:
    plain = _plain_text(text)
    if not plain or len(plain) > 120:
        return False
    return bool(_URL_RE.search(plain))


def is_bare_year(text: str) -> bool:
    return bool(_BARE_YEAR_RE.match(_plain_text(text)))


def is_running_chapter_title(
    block: dict[str, Any],
    *,
    page_index: int,
    chapter_titles: set[str],
) -> bool:
    """Drop repeated chapter titles on pages after the first."""
    if page_index <= 0:
        return False
    label = block.get("label") or ""
    if label not in {"Chapter-title", "Title"}:
        return False
    plain = _plain_text(block.get("text") or "").casefold()
    if not plain:
        return False
    if plain in chapter_titles:
        return True
    # Short ALL-CAPS running header (e.g. LINES AND ANGLES).
    raw = (block.get("text") or "").strip()
    letters = re.sub(r"[^A-Za-z]", "", raw)
    if letters and letters.isupper() and len(plain.split()) <= 6:
        return True
    return False


def collect_chapter_titles(blocks: list[dict[str, Any]]) -> set[str]:
    """Chapter titles from the first page (for later running-header drops)."""
    titles: set[str] = set()
    for block in blocks:
        if (block.get("label") or "") in {"Chapter-title", "Title"}:
            plain = _plain_text(block.get("text") or "").casefold()
            if plain:
                titles.add(plain)
    return titles


def drop_reason(
    block: dict[str, Any],
    *,
    page_index: int,
    chapter_titles: set[str],
) -> str | None:
    """Return why a block should be dropped, or None to keep it."""
    label = block.get("label")
    text = block.get("text") or ""
    plain = _plain_text(text)

    if is_furniture_label(label):
        return "furniture_label"
    if is_running_chapter_title(
        block, page_index=page_index, chapter_titles=chapter_titles
    ):
        return "running_chapter_title"
    if is_edition_code(text):
        return "edition_code"
    if is_url_line(text):
        return "url"
    if is_bare_year(text):
        return "bare_year"
    # Empty figure/diagram boxes stay (assemble may still need the slot);
    # empty furniture already handled. Empty Paragraph/Title with no text → drop.
    if not plain and (block.get("type") or "") in {
        "Text",
        "Title",
        "SectionHeader",
        "Caption",
    }:
        return "empty_text"
    if not plain and label in {
        "Paragraph",
        "Section-title",
        "Chapter-title",
        "Image-caption",
        "Caption",
        "List-item",
    }:
        return "empty_text"
    return None


def cleanup_blocks(
    blocks: list[dict[str, Any]],
    *,
    page_index: int = 0,
    chapter_titles: set[str] | None = None,
    low_conf: float = LOW_CONF_THRESHOLD,
) -> dict[str, Any]:
    """Apply deterministic drops and low-conf flags. Preserves OCR order."""
    titles = chapter_titles or set()
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    doubtful_ids: list[str] = []

    for block in blocks:
        bid = block_id(block)
        reason = drop_reason(block, page_index=page_index, chapter_titles=titles)
        if reason:
            dropped.append({"id": bid, "reason": reason, "label": block.get("label")})
            continue

        conf = block.get("conf")
        flags: list[str] = []
        if isinstance(conf, (int, float)) and conf < low_conf:
            flags.append("low_conf")
            doubtful_ids.append(bid)

        kept.append(
            {
                "id": bid,
                "order": block.get("order"),
                "label": block.get("label"),
                "type": block.get("type"),
                "bbox_xyxy": block.get("bbox_xyxy"),
                "conf": conf,
                "text": block.get("text") or "",
                "flags": flags,
            }
        )

    return {
        "kept": kept,
        "dropped": dropped,
        "doubtful_ids": doubtful_ids,
        "low_conf_threshold": low_conf,
    }
