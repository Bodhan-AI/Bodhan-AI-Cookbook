"""Text helpers: sentence chunking, Markdown-aware units, number checks and script detection.

Nothing here generates or rewrites Indian-language text. It only splits, compares and labels.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

SENTENCE_END = re.compile(r"(?<=[.!?।॥])\s+")


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE_END.split(text.strip()) if s.strip()]


def chunk_text(text: str, max_chars: int) -> list[str]:
    """Group whole sentences into chunks of at most ``max_chars``. Only breaks a sentence if it is longer than the limit."""
    chunks: list[str] = []
    current = ""
    for sentence in split_sentences(text):
        while len(sentence) > max_chars:
            cut = sentence.rfind(" ", 0, max_chars)
            if cut <= max_chars // 2:
                cut = max_chars
            piece, sentence = sentence[:cut].strip(), sentence[cut:].strip()
            if current:
                chunks.append(current)
                current = ""
            chunks.append(piece)
        if current and len(current) + 1 + len(sentence) > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return chunks


def normalize_digits(text: str) -> str:
    """Map any Unicode decimal digit (Devanagari २, Bengali ৫, Tamil ௧ ...) to ASCII."""
    out = []
    for ch in text:
        value = unicodedata.decimal(ch, None)
        out.append(str(value) if value is not None else ch)
    return "".join(out)


def numbers_in(text: str) -> Counter:
    return Counter(re.findall(r"\d+", normalize_digits(text)))


def missing_numbers(source: str, translated: str) -> list[str]:
    """Numbers that appear in the source but not in the translation (multiset difference)."""
    diff = numbers_in(source) - numbers_in(translated)
    return sorted(diff.elements())


def comparable(text: str) -> str:
    """Lower-case, digits normalized, punctuation and spaces removed. Used only to compare two texts."""
    kept = "".join(ch for ch in normalize_digits(text) if unicodedata.category(ch)[0] not in "PZS")
    return kept.lower()


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, comparable(a), comparable(b), autojunk=False).ratio()


# ---- Markdown-aware translation units -------------------------------------

TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
HEADING = re.compile(r"^(\s*#{1,6}\s+)(.*)$")
LIST_ITEM = re.compile(r"^(\s*(?:[-*+]|\d+[.)])\s+)(.*)$")
FENCE = re.compile(r"^\s*```")


@dataclass
class Structure:
    tables: int = 0
    table_rows: int = 0
    headings: int = 0
    list_items: int = 0
    fences: int = 0
    math_blocks: int = 0


def _is_translatable(fragment: str) -> bool:
    """Skip fragments with no letters (numbers, symbols, LaTeX-only)."""
    stripped = fragment.strip()
    if not stripped:
        return False
    if "$" in stripped or "\\(" in stripped or "\\[" in stripped:
        return False
    return bool(re.search(r"[^\W\d_]", stripped))


def translate_markdown(text: str, translate_fragment) -> tuple[str, Structure]:
    """Rebuild Markdown with its prose translated, and its structure kept.

    ``translate_fragment(str) -> str`` is called only for prose, headings, list text and table cells.
    Fenced code, LaTeX blocks, separator rows and numbers-only cells are copied unchanged.
    """
    out: list[str] = []
    stats = Structure()
    lines = text.split("\n")
    in_fence = False
    in_math = False
    paragraph: list[str] = []
    in_table = False

    def flush_paragraph() -> None:
        if paragraph:
            joined = " ".join(p.strip() for p in paragraph)
            out.append(translate_fragment(joined) if _is_translatable(joined) else joined)
            paragraph.clear()

    for line in lines:
        if in_fence:
            out.append(line)
            if FENCE.match(line):
                in_fence = False
            continue
        if FENCE.match(line):
            flush_paragraph()
            stats.fences += 1
            in_fence = True
            out.append(line)
            continue
        if in_math:
            out.append(line)
            if line.strip().endswith("$$") or line.strip() == "\\]":
                in_math = False
            continue
        if line.strip().startswith("$$") or line.strip().startswith("\\["):
            flush_paragraph()
            stats.math_blocks += 1
            out.append(line)
            single_line = line.strip().endswith("$$") and len(line.strip()) > 4 or line.strip().endswith("\\]")
            in_math = not single_line
            continue
        if TABLE_ROW.match(line):
            flush_paragraph()
            if not in_table:
                stats.tables += 1
                in_table = True
            stats.table_rows += 1
            if TABLE_SEPARATOR.match(line):
                out.append(line)
                continue
            out.append(_translate_table_row(line, translate_fragment))
            continue
        in_table = False
        if not line.strip():
            flush_paragraph()
            out.append(line)
            continue
        heading = HEADING.match(line)
        if heading:
            flush_paragraph()
            stats.headings += 1
            prefix, body = heading.groups()
            out.append(prefix + (translate_fragment(body) if _is_translatable(body) else body))
            continue
        item = LIST_ITEM.match(line)
        if item:
            flush_paragraph()
            stats.list_items += 1
            prefix, body = item.groups()
            out.append(prefix + (translate_fragment(body) if _is_translatable(body) else body))
            continue
        paragraph.append(line)

    flush_paragraph()
    return "\n".join(out), stats


def _translate_table_row(line: str, translate_fragment) -> str:
    leading = re.match(r"^\s*", line).group(0)
    trailing = re.search(r"\s*$", line).group(0)
    cells = line.strip().strip("|").split("|")
    new_cells = []
    for cell in cells:
        body = cell.strip()
        if body and _is_translatable(body):
            pad_left = cell[: len(cell) - len(cell.lstrip())]
            pad_right = cell[len(cell.rstrip()) :]
            new_cells.append(pad_left + translate_fragment(body) + pad_right)
        else:
            new_cells.append(cell)
    return f"{leading}|{'|'.join(new_cells)}|{trailing}"


def structure_of(text: str) -> Structure:
    """Count Markdown structure without translating anything. Used to compare source and translation."""
    _, stats = translate_markdown(text, lambda s: s)
    return stats


def structure_mismatches(source: Structure, target: Structure) -> list[str]:
    problems = []
    for field in ("tables", "table_rows", "headings", "list_items", "fences", "math_blocks"):
        a, b = getattr(source, field), getattr(target, field)
        if a != b:
            problems.append(f"{field}: source {a}, translation {b}")
    return problems


# ---- Script detection (allowed: language identification by character set) -----

SCRIPTS = [
    ("devanagari", 0x0900, 0x097F, ["hi", "mr", "ne", "mai", "sa", "kok", "doi", "sd"]),
    ("bengali", 0x0980, 0x09FF, ["bn", "as"]),
    ("gurmukhi", 0x0A00, 0x0A7F, ["pa"]),
    ("gujarati", 0x0A80, 0x0AFF, ["gu"]),
    ("odia", 0x0B00, 0x0B7F, ["or"]),
    ("tamil", 0x0B80, 0x0BFF, ["ta"]),
    ("telugu", 0x0C00, 0x0C7F, ["te"]),
    ("kannada", 0x0C80, 0x0CFF, ["kn"]),
    ("malayalam", 0x0D00, 0x0D7F, ["ml"]),
    ("arabic", 0x0600, 0x06FF, ["ur", "ks", "sd-Arab"]),
    ("ol_chiki", 0x1C50, 0x1C7F, ["sat"]),
    ("meetei_mayek", 0xABC0, 0xABFF, ["mni"]),
]


def detect_script(text: str) -> dict:
    counts: Counter = Counter()
    for ch in text:
        if ch.isascii() and ch.isalpha():
            counts["latin"] += 1
            continue
        code = ord(ch)
        for name, lo, hi, _ in SCRIPTS:
            if lo <= code <= hi and ch.isalpha():
                counts[name] += 1
                break
    total = sum(counts.values())
    if total == 0:
        return {"dominant_script": None, "scripts": {}, "candidate_languages": [], "note": "No letters found."}
    shares = {name: round(n / total, 3) for name, n in counts.most_common()}
    dominant = counts.most_common(1)[0][0]
    if dominant == "latin":
        candidates = ["en"]
    else:
        candidates = next(langs for name, _, _, langs in SCRIPTS if name == dominant)
    note = ""
    if len(candidates) > 1:
        note = "This script is shared by several languages. Confirm the language before using it."
    return {"dominant_script": dominant, "scripts": shares, "candidate_languages": list(candidates), "note": note}
