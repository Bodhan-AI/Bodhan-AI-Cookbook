"""Plain-code text work: script-based language ID, splitting for model limits, Markdown structure, similarity."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher

SCRIPT_RANGES = (
    ("Devanagari", 0x0900, 0x097F),
    ("Bengali", 0x0980, 0x09FF),
    ("Gurmukhi", 0x0A00, 0x0A7F),
    ("Gujarati", 0x0A80, 0x0AFF),
    ("Oriya", 0x0B00, 0x0B7F),
    ("Tamil", 0x0B80, 0x0BFF),
    ("Telugu", 0x0C00, 0x0C7F),
    ("Kannada", 0x0C80, 0x0CFF),
    ("Malayalam", 0x0D00, 0x0D7F),
    ("Arabic", 0x0600, 0x06FF),
    ("Arabic", 0x0750, 0x077F),
    ("OlChiki", 0x1C50, 0x1C7F),
    ("MeeteiMayek", 0xABC0, 0xABFF),
    ("Devanagari", 0xA8E0, 0xA8FF),
)
SCRIPT_LANG = {
    "Tamil": "ta",
    "Telugu": "te",
    "Gurmukhi": "pa",
    "Gujarati": "gu",
    "Oriya": "or",
    "Kannada": "kn",
    "Malayalam": "ml",
    "OlChiki": "sat",
    "MeeteiMayek": "mni",
}
AMBIGUOUS = {
    "Devanagari": ["hi", "mr", "ne", "mai", "sa", "kok", "doi", "brx", "bho", "sd"],
    "Bengali": ["bn", "as", "mni-Beng"],
    "Arabic": ["ur", "ks", "sd-Arab"],
}
INDIC_RUN = re.compile(r"[ऀ-෿؀-ۿ᱐-᱿ꯀ-꯿][ऀ-෿؀-ۿ᱐-᱿ꯀ-꯿‌‍\s\d.,!?;:'\"()\-]*")
ENGLISH_WORDS = set("the a an and or of to in is are was were be for on with that this it as at by from not you your we our they have has will can".split())
SENTENCE_END = re.compile(r"(?<=[.!?।॥])\s+|\n+")


def script_of(ch: str) -> str | None:
    cp = ord(ch)
    for name, lo, hi in SCRIPT_RANGES:
        if lo <= cp <= hi:
            return name
    if ch.isascii() and ch.isalpha():
        return "Latin"
    return "Other" if ch.isalpha() else None


def detect_language(text: str) -> dict:
    """Guess the language from the script; allowed by the brief and cheaper than a model call.

    Returns language (None when unknown), script, confidence (share of letters in the main script),
    ambiguous_with (other languages written in the same script) and a note.
    """
    counts = Counter(s for s in map(script_of, text) if s)
    if not counts:
        return {"language": None, "script": None, "confidence": 0.0, "ambiguous_with": [], "note": "No letters found."}
    script, n = counts.most_common(1)[0]
    guess = {"language": None, "script": script, "confidence": round(n / sum(counts.values()), 2), "ambiguous_with": [], "note": None}
    if script in SCRIPT_LANG:
        guess["language"] = SCRIPT_LANG[script]
    elif script in AMBIGUOUS:
        lang = AMBIGUOUS[script][0]
        if script == "Devanagari" and ("आहे" in text or "ळ" in text):
            lang = "mr"
        if script == "Bengali" and ("ৰ" in text or "ৱ" in text):
            lang = "as"
        guess.update(
            language=lang, ambiguous_with=[x for x in AMBIGUOUS[script] if x != lang], note=f"{script} script is shared by several languages; assumed '{lang}'."
        )
    elif script == "Latin":
        words = re.findall(r"[a-z]+", text.lower())
        if words and sum(w in ENGLISH_WORDS for w in words) / len(words) >= 0.08:
            guess["language"] = "en"
        elif len(words) < 30:  # labels and table cells have no function words; too little text to judge
            guess.update(language="en", note="Short Latin-script text; assumed English.")
        else:
            guess["note"] = "Latin-script text that does not read as English; it may be a language Bodhan does not support."
    else:
        guess["note"] = "Script is not one Bodhan supports."
    return guess


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE_END.split(text) if s and s.strip()]


def _split_long(piece: str, max_chars: int) -> list[str]:
    """Break one over-long sentence at commas, then spaces, then anywhere."""
    if len(piece) <= max_chars:
        return [piece]
    for sep in (", ", "، ", "; ", " "):
        if sep in piece:
            return pack(piece.split(sep), max_chars, joiner=sep)
    return [piece[i : i + max_chars] for i in range(0, len(piece), max_chars)]


def pack(pieces: list[str], max_chars: int, joiner: str = " ") -> list[str]:
    """Greedily join pieces into chunks of at most max_chars, splitting any piece that is too long on its own."""
    chunks: list[str] = []
    for piece in (p.strip() for p in pieces):
        if not piece:
            continue
        for part in _split_long(piece, max_chars):
            if chunks and len(chunks[-1]) + len(joiner) + len(part) <= max_chars:
                chunks[-1] += joiner + part
            else:
                chunks.append(part)
    return chunks


def split_for_speech(text: str, max_chars: int = 220) -> list[str]:
    """Indic-Speak wants a sentence or two (about 30 s of speech) per request."""
    return pack(split_sentences(text), max_chars)


def halves(text: str) -> list[str]:
    """Split text in two near the middle, at a sentence boundary if there is one. Returns [text] if it cannot split."""
    sentences = split_sentences(text)
    if len(sentences) >= 2:
        mid = len(sentences) // 2
        return [" ".join(sentences[:mid]), " ".join(sentences[mid:])]
    middle = len(text) // 2
    cut = text.rfind(" ", 0, middle + 1)
    cut = cut if cut > 0 else text.find(" ", middle)
    return [text[:cut].strip(), text[cut:].strip()] if cut > 0 else [text]


TABLE_SEP = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$")
LINE_MARKER = re.compile(r"^(\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s*)?)(.*)$")


def md_template(md: str, max_chars: int = 1500) -> tuple[list[list[str | int]], list[str]]:
    """Cut Markdown into translatable units, keeping table pipes, separators, list markers and headings out of them.

    Returns (lines, units). Each line is a list of literal strings and int indexes into units.
    """
    units: list[str] = []

    def unit(s: str) -> list[str | int]:
        if not any(c.isalpha() for c in s):
            return [s]
        out: list[str | int] = []
        for i, piece in enumerate(pack(split_sentences(s), max_chars)):
            if i:
                out.append(" ")
            units.append(piece)
            out.append(len(units) - 1)
        return out

    lines: list[list[str | int]] = []
    paragraph: list[str] = []  # consecutive plain lines are one Markdown paragraph (OCR breaks lines mid-sentence)

    def flush() -> None:
        if paragraph:
            lines.append(unit(" ".join(paragraph)))
            paragraph.clear()

    for line in md.split("\n"):
        stripped = line.strip()
        m = LINE_MARKER.match(line)
        if stripped and not m[1] and not stripped.startswith(("|", "<!--")) and not TABLE_SEP.match(stripped):
            paragraph.append(stripped)
            continue
        flush()
        if not stripped or TABLE_SEP.match(stripped) or stripped.startswith("<!--"):
            lines.append([line])
        elif stripped.startswith("|"):
            segs: list[str | int] = ["|"]
            for cell in stripped.strip("|").split("|"):
                segs += [" ", *unit(cell.strip()), " |"]
            lines.append(segs)
        else:
            lines.append([m[1], *unit(m[2])])
    flush()
    return lines, units


def md_fill(lines: list[list[str | int]], translations: list[str]) -> str:
    return "\n".join("".join(s if isinstance(s, str) else translations[s] for s in segs) for segs in lines)


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFC", text).lower()
    return "".join(c for c in text if unicodedata.category(c)[0] in "LMN")


def similarity(expected: str, heard: str) -> tuple[float, float]:
    """(character similarity 0-1, length ratio heard/expected) after dropping spaces and punctuation."""
    a, b = _norm(expected), _norm(heard)
    if not a:
        return (1.0 if not b else 0.0), 1.0
    return round(SequenceMatcher(None, a, b, autojunk=False).ratio(), 3), round(len(b) / len(a), 3)


def has_indic(text: str) -> bool:
    return bool(INDIC_RUN.search(text))
