"""Plain-code text handling: script detection, sentence splitting, chunking, structure-aware
Markdown parsing and similarity scoring. No model calls here.

Language identification is by Unicode script only (explicitly allowed by the assignment). A
script maps to one language for most Indic scripts (Tamil, Telugu, ...) and to a set of
candidates for shared ones (Devanagari, Bengali, Perso-Arabic, Latin).
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

# (start, end inclusive, script name)
_RANGES: list[tuple[int, int, str]] = [
    (0x0041, 0x005A, "Latin"),
    (0x0061, 0x007A, "Latin"),
    (0x00C0, 0x024F, "Latin"),
    (0x0370, 0x03FF, "Greek"),
    (0x0400, 0x04FF, "Cyrillic"),
    (0x0590, 0x05FF, "Hebrew"),
    (0x0600, 0x06FF, "Arabic"),
    (0x0750, 0x077F, "Arabic"),
    (0x08A0, 0x08FF, "Arabic"),
    (0xFB50, 0xFDFF, "Arabic"),
    (0xFE70, 0xFEFF, "Arabic"),
    (0x0900, 0x097F, "Devanagari"),
    (0xA8E0, 0xA8FF, "Devanagari"),
    (0x0980, 0x09FF, "Bengali"),
    (0x0A00, 0x0A7F, "Gurmukhi"),
    (0x0A80, 0x0AFF, "Gujarati"),
    (0x0B00, 0x0B7F, "Odia"),
    (0x0B80, 0x0BFF, "Tamil"),
    (0x0C00, 0x0C7F, "Telugu"),
    (0x0C80, 0x0CFF, "Kannada"),
    (0x0D00, 0x0D7F, "Malayalam"),
    (0x0E00, 0x0E7F, "Thai"),
    (0x1C50, 0x1C7F, "OlChiki"),
    (0xABC0, 0xABFF, "MeeteiMayek"),
    (0xAAE0, 0xAAFF, "MeeteiMayek"),
    (0x3040, 0x309F, "Hiragana"),
    (0x30A0, 0x30FF, "Katakana"),
    (0x4E00, 0x9FFF, "Han"),
    (0xAC00, 0xD7AF, "Hangul"),
]

INDIC_SCRIPTS = {
    "Devanagari",
    "Bengali",
    "Gurmukhi",
    "Gujarati",
    "Odia",
    "Tamil",
    "Telugu",
    "Kannada",
    "Malayalam",
    "OlChiki",
    "MeeteiMayek",
    "Arabic",
}

SCRIPT_LANGUAGES: dict[str, list[str]] = {
    "Tamil": ["ta"],
    "Telugu": ["te"],
    "Kannada": ["kn"],
    "Malayalam": ["ml"],
    "Gujarati": ["gu"],
    "Gurmukhi": ["pa"],
    "Odia": ["or"],
    "OlChiki": ["sat"],
    "MeeteiMayek": ["mni"],
    "Bengali": ["bn", "as", "mni-Beng"],
    "Devanagari": ["hi", "mr", "ne", "mai", "sa", "kok", "doi", "brx", "bho", "bhb", "ks-Deva", "sd"],
    "Arabic": ["ur", "ks", "sd-Arab"],
    "Latin": ["en"],
}

LANGUAGE_SCRIPT: dict[str, str] = {}
for _script, _langs in SCRIPT_LANGUAGES.items():
    for _lang in _langs:
        LANGUAGE_SCRIPT.setdefault(_lang, _script)
LANGUAGE_SCRIPT.update({"ks": "Arabic", "sd": "Devanagari", "mni": "MeeteiMayek"})


def char_script(ch: str) -> str | None:
    cp = ord(ch)
    for lo, hi, name in _RANGES:
        if lo <= cp <= hi:
            return name
    return None


def script_counts(text: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    for ch in text:
        if ch.isalpha() or unicodedata.category(ch) in {"Mn", "Mc"}:
            name = char_script(ch)
            if name:
                counts[name] += 1
    return counts


@dataclass
class ScriptReport:
    dominant: str | None
    share: float
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def likely_languages(self) -> list[str]:
        return SCRIPT_LANGUAGES.get(self.dominant or "", [])

    @property
    def unsupported_script(self) -> bool:
        return self.dominant is not None and self.dominant not in SCRIPT_LANGUAGES


def detect_script(text: str) -> ScriptReport:
    counts = script_counts(text)
    total = sum(counts.values())
    if not total:
        return ScriptReport(None, 0.0, {})
    name, n = counts.most_common(1)[0]
    return ScriptReport(name, round(n / total, 3), dict(counts))


def script_share(text: str, script: str) -> float:
    counts = script_counts(text)
    total = sum(counts.values())
    return 1.0 if total == 0 else counts.get(script, 0) / total


def contains_indic(text: str) -> bool:
    return any(char_script(ch) in INDIC_SCRIPTS for ch in text)


# ---------------------------------------------------------------------------- sentences & chunks

_SENTENCE_END = re.compile(r"(?<=[।॥.!?…۔؟])\s+|\n+")
_CLAUSE_END = re.compile(r"(?<=[,;:،—–])\s+")


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text.strip()) if s and s.strip()]


def _hard_split(piece: str, max_chars: int) -> list[str]:
    """Split one over-long sentence at clause marks, then at spaces, never mid-word if avoidable."""
    out: list[str] = []
    for clause in [c for c in _CLAUSE_END.split(piece) if c.strip()]:
        while len(clause) > max_chars:
            cut = clause.rfind(" ", 0, max_chars)
            cut = cut if cut > max_chars // 3 else max_chars
            out.append(clause[:cut].strip())
            clause = clause[cut:].strip()
        if clause:
            out.append(clause)
    return pack(out, max_chars)


def pack(pieces: list[str], max_chars: int, max_pieces: int | None = None) -> list[str]:
    chunks: list[str] = []
    cur: list[str] = []
    for p in pieces:
        candidate = " ".join([*cur, p])
        if cur and (len(candidate) > max_chars or (max_pieces and len(cur) >= max_pieces)):
            chunks.append(" ".join(cur))
            cur = [p]
        else:
            cur.append(p)
    if cur:
        chunks.append(" ".join(cur))
    return chunks


def chunk_for_speech(text: str, max_chars: int = 240, max_sentences: int = 2) -> list[str]:
    """Indic-Speak guidance: one or two sentences (~30 s) per request."""
    pieces: list[str] = []
    for sentence in split_sentences(text):
        pieces.extend(_hard_split(sentence, max_chars) if len(sentence) > max_chars else [sentence])
    return pack(pieces, max_chars, max_sentences)


def split_in_two(text: str) -> list[str]:
    """Used when a chunk must be retried as a *different*, smaller input (truncation, failed TTS check)."""
    sentences = split_sentences(text)
    if len(sentences) >= 2:
        mid = len(sentences) // 2
        return [" ".join(sentences[:mid]), " ".join(sentences[mid:])]
    clauses = [c for c in _CLAUSE_END.split(text) if c.strip()]
    if len(clauses) >= 2:
        mid = len(clauses) // 2
        return [" ".join(clauses[:mid]), " ".join(clauses[mid:])]
    words = text.split()
    if len(words) >= 4:
        mid = len(words) // 2
        return [" ".join(words[:mid]), " ".join(words[mid:])]
    return [text]


def chunk_for_translation(text: str, max_chars: int = 1200) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    pieces: list[str] = []
    for sentence in split_sentences(text):
        pieces.extend(_hard_split(sentence, max_chars) if len(sentence) > max_chars else [sentence])
    return pack(pieces, max_chars)


# ---------------------------------------------------------------------------- markdown structure

_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_HEADING = re.compile(r"^(\s*#{1,6}\s+)(.*)$")
_LIST = re.compile(r"^(\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?)(.*)$")
_QUOTE = re.compile(r"^(\s*>\s?)(.*)$")
_HTML_COMMENT = re.compile(r"^\s*<!--.*-->\s*$")
_NOTHING_TO_TRANSLATE = re.compile(r"^[\s\d\W_]*$", re.UNICODE)


@dataclass
class Segment:
    """One translatable span inside a line, or a literal kept as-is."""

    text: str
    translate: bool


@dataclass
class Line:
    parts: list[Segment]


def _is_table_row(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and s.count("|") >= 2


def _split_cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c for c in re.split(r"(?<!\\)\|", s)]


def needs_translation(text: str) -> bool:
    t = text.strip()
    if not t or _NOTHING_TO_TRANSLATE.match(t):
        return False
    if re.fullmatch(r"(https?://\S+|\S+@\S+\.\S+)", t):
        return False
    return True


def parse_markdown(text: str) -> list[Line]:
    """Turn Markdown into lines of literal and translatable segments.

    Structure (table pipes, heading hashes, list markers, code blocks, page markers) stays literal,
    so reassembly can never break a table: each cell is translated on its own.
    Consecutive plain lines form one paragraph so the translator sees whole sentences.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    out: list[Line] = []
    para: list[str] = []
    in_code = False

    def flush_para() -> None:
        if para:
            joined = " ".join(p.strip() for p in para)
            out.append(Line([Segment(joined, needs_translation(joined))]))
            para.clear()

    for raw in lines:
        if raw.strip().startswith("```"):
            flush_para()
            in_code = not in_code
            out.append(Line([Segment(raw, False)]))
            continue
        if (
            in_code
            or not raw.strip()
            or _HTML_COMMENT.match(raw)
            or _TABLE_SEP.match(raw)
            or raw.strip() in {"---", "***", "___"}
        ):
            flush_para()
            out.append(Line([Segment(raw, False)]))
            continue
        if _is_table_row(raw):
            flush_para()
            segs: list[Segment] = [Segment("|", False)]
            for cell in _split_cells(raw):
                lead = cell[: len(cell) - len(cell.lstrip())]
                trail = cell[len(cell.rstrip()) :]
                core = cell.strip()
                if lead:
                    segs.append(Segment(lead, False))
                if core:
                    segs.append(Segment(core, needs_translation(core)))
                if trail:
                    segs.append(Segment(trail, False))
                segs.append(Segment("|", False))
            out.append(Line(segs))
            continue
        for pattern in (_HEADING, _LIST, _QUOTE):
            m = pattern.match(raw)
            if m:
                flush_para()
                out.append(
                    Line([Segment(m.group(1), False), Segment(m.group(2).strip(), needs_translation(m.group(2)))])
                )
                break
        else:
            para.append(raw)
    flush_para()
    return out


def render_markdown(lines: list[Line]) -> str:
    return "\n".join("".join(seg.text for seg in line.parts) for line in lines)


def table_shape(text: str) -> list[int]:
    """Cells per table row, used to check a translated table kept its structure."""
    return [len(_split_cells(line)) for line in text.split("\n") if _is_table_row(line) and not _TABLE_SEP.match(line)]


# ---------------------------------------------------------------------------- comparison


def normalize_for_compare(text: str) -> str:
    text = unicodedata.normalize("NFC", text).lower()
    return "".join(ch for ch in text if ch.isalnum() or unicodedata.category(ch) in {"Mn", "Mc"})


def similarity(a: str, b: str) -> float:
    na, nb = normalize_for_compare(a), normalize_for_compare(b)
    if not na and not nb:
        return 1.0
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb, autojunk=False).ratio()


def length_ratio(source: str, candidate: str) -> float:
    ns, nc = len(normalize_for_compare(source)), len(normalize_for_compare(candidate))
    return nc / ns if ns else (1.0 if not nc else float("inf"))


_REPEAT = re.compile(r"(.{6,}?)\1{2,}")


def has_repetition(text: str) -> bool:
    """True when some 6+ char span repeats 3+ times back to back (a known TTS/ASR failure mode)."""
    return bool(_REPEAT.search(normalize_for_compare(text)))


_INJECTION = re.compile(
    r"(ignore (all |any )?(previous|prior|above) (instructions|prompts)|disregard (the )?(system|previous)|"
    r"you are now|system prompt|new instructions:|act as (an? )?(assistant|ai)|do not translate|instead,? (write|output|say))",
    re.IGNORECASE,
)


def looks_like_injection(text: str) -> bool:
    return bool(_INJECTION.search(text))


_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_EMPH = re.compile(r"(\*\*|__|\*|_|`)")


def speakable(text: str) -> str:
    """Markdown -> plain sentences for TTS (tables read row by row, markup dropped)."""
    out: list[str] = []
    in_code = False
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not line or _HTML_COMMENT.match(line) or _TABLE_SEP.match(line) or line in {"---", "***", "___"}:
            continue
        if _is_table_row(line):
            cells = [c.strip() for c in _split_cells(line) if c.strip()]
            line = ", ".join(cells)
        else:
            for pattern in (_HEADING, _LIST, _QUOTE):
                m = pattern.match(line)
                if m:
                    line = m.group(2)
                    break
        line = _EMPH.sub("", _LINK.sub(r"\1", line)).strip()
        if line:
            if not re.search(r"[।॥.!?…۔؟:]$", line):
                line += "."
            out.append(line)
    return " ".join(out)
