"""Unicode-script language identification. Local, free, honest about ambiguity."""
from __future__ import annotations

from pathlib import Path

from .errors import BodhanError

_MAX_CHARS = 200_000

# (script, first code point, last code point); inspected in order, first match wins.
_BLOCKS: tuple[tuple[str, int, int], ...] = (
    ("Latin", 0x0041, 0x005A),
    ("Latin", 0x0061, 0x007A),
    ("Devanagari", 0x0900, 0x097F),
    ("Devanagari", 0xA8E0, 0xA8FF),
    ("Bengali", 0x0980, 0x09FF),
    ("Gurmukhi", 0x0A00, 0x0A7F),
    ("Gujarati", 0x0A80, 0x0AFF),
    ("Odia", 0x0B00, 0x0B7F),
    ("Tamil", 0x0B80, 0x0BFF),
    ("Telugu", 0x0C00, 0x0C7F),
    ("Kannada", 0x0C80, 0x0CFF),
    ("Malayalam", 0x0D00, 0x0D7F),
    ("Ol Chiki", 0x1C50, 0x1C7F),
    ("Latin", 0x00C0, 0x024F),
    ("Latin", 0x1E00, 0x1EFF),
    ("Urdu/Arabic", 0x0600, 0x06FF),
    ("Urdu/Arabic", 0x0750, 0x077F),
    ("Urdu/Arabic", 0xFB50, 0xFDFF),
    ("Urdu/Arabic", 0xFE70, 0xFEFF),
)
_NON_LATIN = {name for name, _, _ in _BLOCKS} - {"Latin"}
_SINGLE = {
    "Tamil": "ta",
    "Telugu": "te",
    "Kannada": "kn",
    "Malayalam": "ml",
    "Gujarati": "gu",
    "Gurmukhi": "pa",
    "Odia": "or",
    "Ol Chiki": "sat",
}
# Scripts Bodhan serves for more than one language: candidates, never a verdict.
_SHARED = {"Bengali": ["bn", "as"], "Devanagari": ["hi", "mr", "ne", "sa"], "Urdu/Arabic": ["ur", "sd", "ks"]}
_SHARED_CONF = {"Bengali": 0.55, "Devanagari": 0.30, "Urdu/Arabic": 0.40}
_DOMINANCE = 0.80
_ENGLISH_INDIC_MAX = 0.10
ENGLISH_ASCII_MIN = 0.90


def _script_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for ch in text:
        code = ord(ch)
        for name, lo, hi in _BLOCKS:
            if lo <= code <= hi:
                counts[name] = counts.get(name, 0) + 1
                break
    return counts


def _ascii_letters(text: str) -> int:
    return sum(1 for ch in text if ch.isascii() and ch.isalpha())


def _label(counts: dict[str, int]) -> str:
    if not counts:
        return "None"
    name, count = max(counts.items(), key=lambda kv: kv[1])
    return name if count / sum(counts.values()) >= _DOMINANCE else "Mixed"


def script_of_text(text: str) -> str:
    """The text's script name, "Mixed" when no script holds 80% of the letters."""
    return _label(_script_counts(text))


def dominant_script(text: str) -> str:
    """The single most frequent script, regardless of mixing; "None" if letterless."""
    counts = _script_counts(text)
    return max(counts, key=counts.get) if counts else "None"  # type: ignore[arg-type]


def has_indic_script(text: str) -> bool:
    return any(name in _script_counts(text) for name in _NON_LATIN)


def is_english(text: str) -> bool:
    """Latin letters dominate (ASCII), with no meaningful Indic block."""
    counts = _script_counts(text)
    latin = counts.get("Latin", 0)
    if not latin:
        return False
    other = sum(count for name, count in counts.items() if name != "Latin")
    return other / (latin + other) < _ENGLISH_INDIC_MAX and _ascii_letters(text) / latin >= ENGLISH_ASCII_MIN


def _read(source: str | Path) -> str:
    if isinstance(source, Path):
        if not source.is_file():
            raise BodhanError("MISSING_FILE", f"{source} does not exist.", hint="Pass a readable text file.")
        return source.read_text(encoding="utf-8", errors="replace")[:_MAX_CHARS]
    text = str(source)
    if "\n" in text or len(text) > 4096:
        return text[:_MAX_CHARS]
    try:
        path = Path(text)
        looks_like_path = path.suffix.lower() in {".txt", ".md", ".srt", ".csv"} or text.startswith(("/", "~"))
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")[:_MAX_CHARS]
    except (OSError, ValueError):
        looks_like_path = False
    if looks_like_path:
        raise BodhanError("MISSING_FILE", f"{text} does not exist.", hint="Pass a readable text file.")
    return text[:_MAX_CHARS]


def identify(source: str | Path) -> dict:
    """Guess the language from the writing system. Accepts a file path or raw text."""
    text = _read(source)
    counts = _script_counts(text)
    total = sum(counts.values())
    script = _label(counts)
    result: dict = {"language": None, "script": script, "confidence": 0.0, "candidates": [], "chars_sampled": len(text)}
    if not total:
        return result

    share = max(counts.values()) / total
    if script in _SINGLE:
        result.update(language=_SINGLE[script], candidates=[_SINGLE[script]], confidence=round(0.95 * share, 2))
    elif script in _SHARED:
        result.update(candidates=list(_SHARED[script]), confidence=round(_SHARED_CONF[script] * share, 2))
    elif script == "Latin":
        ascii_share = _ascii_letters(text) / counts["Latin"]
        if ascii_share >= ENGLISH_ASCII_MIN:
            result.update(language="en", candidates=["en"], confidence=round(0.90 * share, 2))
        else:
            result.update(candidates=["en"], confidence=round(0.40 * share, 2))
    else:  # Mixed: name every code the blocks support, claim nothing
        codes: list[str] = []
        for name in counts:
            if name in _SHARED:
                codes += _SHARED[name]
            elif name in _SINGLE:
                codes.append(_SINGLE[name])
            elif name == "Latin":
                codes.append("en")
        result.update(candidates=sorted(set(codes)), confidence=round(0.30 * share, 2))
    return result
