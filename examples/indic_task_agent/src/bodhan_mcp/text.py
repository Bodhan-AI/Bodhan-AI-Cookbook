"""Pure text utilities: script detection, sentence splitting, windowing, Markdown blocks, similarity.

Language identification here is by Unicode script and character set only (the kind the brief
allows). No statistical or ML language-ID library is used.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

# --- scripts -------------------------------------------------------------------------------

_RANGES: list[tuple[str, int, int]] = [
    ("Devanagari", 0x0900, 0x097F), ("Devanagari", 0xA8E0, 0xA8FF), ("Devanagari", 0x1CD0, 0x1CFF),
    ("Bengali", 0x0980, 0x09FF),
    ("Gurmukhi", 0x0A00, 0x0A7F),
    ("Gujarati", 0x0A80, 0x0AFF),
    ("Odia", 0x0B00, 0x0B7F),
    ("Tamil", 0x0B80, 0x0BFF),
    ("Telugu", 0x0C00, 0x0C7F),
    ("Kannada", 0x0C80, 0x0CFF),
    ("Malayalam", 0x0D00, 0x0D7F),
    ("OlChiki", 0x1C50, 0x1C7F),
    ("MeeteiMayek", 0xABC0, 0xABFF), ("MeeteiMayek", 0xAAE0, 0xAAFF),
    ("Arabic", 0x0600, 0x06FF), ("Arabic", 0x0750, 0x077F), ("Arabic", 0x08A0, 0x08FF),
    ("Arabic", 0xFB50, 0xFDFF), ("Arabic", 0xFE70, 0xFEFF),
]
INDIC_SCRIPTS = frozenset(name for name, _, _ in _RANGES)

SCRIPT_LANGUAGES: dict[str, list[str]] = {
    "Devanagari": ["hi", "mr", "ne", "mai", "sa", "kok", "doi", "bho", "brx", "bhb", "ks-Deva", "sd"],
    "Bengali": ["bn", "as", "mni-Beng"],
    "Gurmukhi": ["pa"],
    "Gujarati": ["gu"],
    "Odia": ["or"],
    "Tamil": ["ta"],
    "Telugu": ["te"],
    "Kannada": ["kn"],
    "Malayalam": ["ml"],
    "OlChiki": ["sat"],
    "MeeteiMayek": ["mni"],
    "Arabic": ["ur", "ks", "sd-Arab"],
    "Latin": ["en"],
}

LANGUAGE_SCRIPT: dict[str, str] = {}
for _script, _langs in SCRIPT_LANGUAGES.items():
    for _lang in _langs:
        LANGUAGE_SCRIPT.setdefault(_lang, _script)

_INDIC_CLASS = "".join(f"\\u{lo:04x}-\\u{hi:04x}" for _, lo, hi in _RANGES if hi <= 0xFFFF)
INDIC_RE = re.compile(f"[{_INDIC_CLASS}]")
# A run of Indian-language text, allowing spaces, digits and punctuation between letters.
INDIC_RUN_RE = re.compile(f"[{_INDIC_CLASS}](?:[{_INDIC_CLASS}\\s\\d.,;:!?'\"()\\-–—।॥]*[{_INDIC_CLASS}])?")


def char_script(ch: str) -> str | None:
    cp = ord(ch)
    for name, lo, hi in _RANGES:
        if lo <= cp <= hi:
            return name
    if ch.isalpha() and (ch.isascii() or unicodedata.name(ch, "").startswith("LATIN")):
        return "Latin"
    return None


def script_counts(text: str) -> Counter:
    counts: Counter = Counter()
    for ch in text:
        script = char_script(ch)
        if script:
            counts[script] += 1
    return counts


def has_indic(text: str) -> bool:
    return bool(INDIC_RE.search(text or ""))


def script_fraction(text: str, script: str) -> float:
    counts = script_counts(text)
    total = sum(counts.values())
    return counts[script] / total if total else 0.0


def detect_language(text: str) -> dict:
    """Script-based language detection. Returns the dominant script, candidate languages, a best
    guess and a confidence that is only 'high' when the script maps to exactly one language."""
    counts = script_counts(text)
    total = sum(counts.values())
    if not total:
        return {"script": None, "script_fraction": 0.0, "candidates": [], "best_guess": None,
                "confidence": "none", "scripts": {}, "note": "no letters found"}
    script, n = counts.most_common(1)[0]
    candidates = list(SCRIPT_LANGUAGES.get(script, []))
    best = candidates[0] if candidates else None
    note = ""
    if script == "Bengali" and re.search("[ৰৱ]", text):  # ৰ ৱ are Assamese-only letters
        best, note = "as", "Assamese-only letters (ৰ/ৱ) present"
    elif script == "Devanagari" and text.count("ळ") >= 2 and "ळ" in text:  # ळ is common in Marathi
        best, note = "mr", "frequent ळ suggests Marathi"
    confidence = "high" if len(candidates) == 1 else ("medium" if note else "low")
    return {
        "script": script,
        "script_fraction": round(n / total, 3),
        "candidates": candidates,
        "best_guess": best,
        "confidence": confidence,
        "scripts": {k: round(v / total, 3) for k, v in counts.most_common()},
        "note": note or ("script is shared by several languages" if len(candidates) > 1 else ""),
    }


# --- splitting and windows -----------------------------------------------------------------

_SENTENCE_END = re.compile(r"(?<=[.!?।॥۔])\s+")
_SOFT_BREAK = re.compile(r"(?<=[,;:،])\s+")


def split_sentences(text: str) -> list[str]:
    out: list[str] = []
    for line in re.split(r"\n+", text.strip()):
        line = line.strip()
        if line:
            out.extend(s.strip() for s in _SENTENCE_END.split(line) if s.strip())
    return out


def split_long(piece: str, max_chars: int) -> list[str]:
    """Split one over-long sentence at commas, then at spaces, then hard-cut."""
    if len(piece) <= max_chars:
        return [piece]
    parts = [p for p in _SOFT_BREAK.split(piece) if p]
    if len(parts) == 1:
        parts = piece.split(" ")
        if len(parts) == 1:
            return [piece[i:i + max_chars] for i in range(0, len(piece), max_chars)]
    return pack(parts, max_chars, joiner=" ", split_oversize=True)


def pack(units: list[str], max_chars: int, joiner: str = " ", split_oversize: bool = True) -> list[str]:
    """Greedily pack units into windows of at most max_chars, never splitting a unit unless it alone
    is longer than max_chars."""
    windows: list[str] = []
    current = ""
    for unit in units:
        pieces = split_long(unit, max_chars) if split_oversize and len(unit) > max_chars else [unit]
        for piece in pieces:
            if not current:
                current = piece
            elif len(current) + len(joiner) + len(piece) <= max_chars:
                current = current + joiner + piece
            else:
                windows.append(current)
                current = piece
    if current:
        windows.append(current)
    return windows


def halve(text: str) -> list[str]:
    """Split text into two roughly equal parts at a sentence or word boundary."""
    sentences = split_sentences(text)
    if len(sentences) >= 2:
        mid, acc = len(text) / 2, 0
        for i, s in enumerate(sentences):
            acc += len(s) + 1
            if acc >= mid:
                left, right = " ".join(sentences[: i + 1]), " ".join(sentences[i + 1:])
                if left and right:
                    return [left, right]
                break
    words = text.split(" ")
    if len(words) >= 2:
        m = len(words) // 2
        return [" ".join(words[:m]), " ".join(words[m:])]
    m = len(text) // 2
    return [text[:m], text[m:]]


@dataclass
class Window:
    text: str
    joiner: str  # inserted before the next window when reassembling


def text_windows(text: str, max_chars: int) -> list[Window]:
    """Windows for plain text that keep paragraph breaks when reassembled."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    windows: list[Window] = []
    group: list[str] = []

    def flush() -> None:
        if group:
            windows.append(Window("\n\n".join(group), "\n\n"))
            group.clear()

    for para in paragraphs:
        if len(para) > max_chars:
            flush()
            for piece in pack(split_sentences(para), max_chars):
                windows.append(Window(piece, " "))
            if windows:
                windows[-1].joiner = "\n\n"
        elif sum(len(g) + 2 for g in group) + len(para) <= max_chars:
            group.append(para)
        else:
            flush()
            group.append(para)
    flush()
    return windows


def join_windows(windows: list[Window], outputs: list[str]) -> str:
    parts: list[str] = []
    for i, (w, out) in enumerate(zip(windows, outputs, strict=True)):
        parts.append(out.strip())
        if i < len(windows) - 1:
            parts.append(w.joiner)
    return "".join(parts).strip()


# --- Markdown --------------------------------------------------------------------------------

@dataclass
class Block:
    kind: str  # "text" | "table" | "code"
    text: str


_MD_HINT = re.compile(r"(^|\n)\s*(#{1,6}\s|\|.*\||[-*+]\s|\d+\.\s|```)")


def looks_like_markdown(text: str) -> bool:
    return bool(_MD_HINT.search(text))


def markdown_blocks(text: str) -> list[Block]:
    """Split Markdown into text, table and fenced-code blocks (blank lines separate text blocks)."""
    blocks: list[Block] = []
    lines = text.replace("\r\n", "\n").split("\n")
    i = 0
    buf: list[str] = []

    def flush_text() -> None:
        if any(line.strip() for line in buf):
            blocks.append(Block("text", "\n".join(buf).strip("\n")))
        buf.clear()

    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            flush_text()
            code = [line]
            i += 1
            while i < len(lines):
                code.append(lines[i])
                if lines[i].strip().startswith("```"):
                    i += 1
                    break
                i += 1
            blocks.append(Block("code", "\n".join(code)))
            continue
        if line.strip().startswith("|"):
            flush_text()
            table = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table.append(lines[i])
                i += 1
            blocks.append(Block("table", "\n".join(table)))
            continue
        if not line.strip():
            flush_text()
        else:
            buf.append(line)
        i += 1
    flush_text()
    return blocks


_SEPARATOR_CELL = re.compile(r"^:?-{1,}:?$")


def table_rows(table: str) -> list[list[str]]:
    rows = []
    for line in table.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        rows.append(cells)
    return rows


def is_separator_row(cells: list[str]) -> bool:
    filled = [c for c in cells if c]  # an all-empty row (e.g. a blank header) is content, not a separator
    return bool(filled) and all(_SEPARATOR_CELL.match(c.replace(" ", "")) for c in filled)


def table_shape(table: str) -> list[int]:
    return [len(r) for r in table_rows(table)]


_HTML_TABLE = re.compile(r"<table\b.*?</table>", re.S | re.I)
_HTML_ROW = re.compile(r"<tr\b.*?</tr>", re.S | re.I)
_HTML_CELL = re.compile(r"<t[hd]\b", re.I)


def table_structure(text: str) -> list[tuple]:
    """Shape of every table in a document, independent of cell text: Markdown tables as their rows
    (cell count, separator or not), HTML tables (which OCR sometimes emits) as cells per <tr>.
    Comparing source and translation catches tables the translation broke."""
    shapes: list[tuple] = []
    for block in markdown_blocks(_HTML_TABLE.sub("", text)):
        if block.kind == "table":
            shapes.append(("md",) + tuple((len(r), is_separator_row(r)) for r in table_rows(block.text)))
    for table in _HTML_TABLE.findall(text):
        shapes.append(("html",) + tuple(len(_HTML_CELL.findall(row)) for row in _HTML_ROW.findall(table)))
    return shapes


def render_table(rows: list[list[str]]) -> str:
    return "\n".join("| " + " | ".join(r) + " |" for r in rows)


def strip_markdown_for_speech(text: str) -> str:
    """Turn Markdown into plain prose suitable for reading aloud."""
    out_lines = []
    for block in markdown_blocks(text):
        if block.kind == "code":
            continue
        if block.kind == "table":
            for cells in table_rows(block.text):
                if not is_separator_row(cells):
                    out_lines.append(", ".join(c for c in cells if c) + ".")
            continue
        for line in block.text.split("\n"):
            line = re.sub(r"^\s*#{1,6}\s*", "", line)
            line = re.sub(r"^\s*([-*+]|\d+\.)\s+", "", line)
            line = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", line)
            line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)
            line = re.sub(r"[*_`>]+", "", line)
            line = re.sub(r"<!--.*?-->", "", line)
            if line.strip():
                out_lines.append(line.strip())
    return "\n".join(out_lines)


# --- comparison ------------------------------------------------------------------------------

def normalize_for_compare(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "").lower()
    return "".join(ch for ch in text if ch.isalnum() or unicodedata.category(ch).startswith("M"))


def similarity(a: str, b: str) -> float:
    """Character-level similarity in [0, 1] (1 - a rough character error rate)."""
    na, nb = normalize_for_compare(a), normalize_for_compare(b)
    if not na and not nb:
        return 1.0
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb, autojunk=False).ratio()


# --- prompt-injection heuristics -------------------------------------------------------------

_INJECTION = re.compile(
    r"(?i)(ignore (all |any |the )?(previous|prior|above) (instructions|prompts?)|disregard (the|all|previous)"
    r"|you are now|system prompt|new instructions?:|act as |do not follow|override (your|the) "
    r"|assistant:|<\s*/?\s*system\s*>|forget (everything|all|your))"
)


def injection_markers(text: str) -> list[str]:
    return sorted({m.group(0).strip() for m in _INJECTION.finditer(text or "")})
