"""Output checks, pure code: compare generated speech with its text, and a translation with its source."""

import re
import unicodedata
from collections import Counter

# Zero digit of every script whose digits Bodhan may emit (translation turns 0.75 into ०.७५).
_ZEROS = [0x0660, 0x06F0, 0x0966, 0x09E6, 0x0A66, 0x0AE6, 0x0B66, 0x0BE6, 0x0C66, 0x0CE6, 0x0D66, 0x1C50, 0xABF0]
_DIGITS = str.maketrans({chr(z + i): str(i) for z in _ZEROS for i in range(10)})

TTS_MAX_CER = 0.3  # back-transcripts are close (Phase 0: only "10" -> "टेन" differed), so 30% means real trouble
TTS_LENGTH_RATIO = (0.7, 1.4)  # heard / expected; below = words omitted, above = words repeated


def normalize(text: str) -> str:
    """For comparison only: ASCII digits, no punctuation or symbols, single spaces, lower case."""
    text = unicodedata.normalize("NFC", text).translate(_DIGITS)
    text = "".join(" " if unicodedata.category(c)[0] in "PSZ" else c for c in text)
    return " ".join(text.lower().split())


def cer(reference: str, heard: str) -> float:
    """Character error rate: edits needed to turn `reference` into `heard`, divided by its length."""
    a, b = normalize(reference), normalize(heard)
    if not a:
        return 0.0 if not b else 1.0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return round(prev[-1] / len(a), 3)


def speech_check(expected: str, heard: str) -> dict:
    """Did the synthesised audio say the text? Catches Indic-Speak's omissions and repeats."""
    ratio = round(len(normalize(heard)) / max(len(normalize(expected)), 1), 2)
    score = cer(expected, heard)
    lo, hi = TTS_LENGTH_RATIO
    return {"cer": score, "length_ratio": ratio, "ok": score <= TTS_MAX_CER and lo <= ratio <= hi}


def numbers(text: str) -> Counter:
    """Numbers that must survive translation. Single digits are left out: translators rightly spell
    them as words ("8 am" -> "सुबह आठ बजे").
    ponytail: a lost single digit goes unnoticed; per-language number words would catch it."""
    found = re.findall(r"\d+(?:[.,:/]\d+)*", text.translate(_DIGITS))
    return Counter(n for n in found if len(n) > 1)


def translation_issues(source: str, translated: str) -> list[str]:
    """Problems with one translated chunk: empty, suspicious length, lost numbers, lost table rows."""
    if not translated.strip():
        return ["came back empty"]
    issues = []
    ratio = len(translated) / max(len(source), 1)
    if ratio < 0.25 or ratio > 4:
        issues.append(f"length ratio {ratio:.2f} (part may be missing or repeated)")
    missing = numbers(source) - numbers(translated)
    if missing:
        issues.append(f"numbers missing: {', '.join(sorted(missing))}")
    rows = [sum(1 for line in t.splitlines() if line.lstrip().startswith("|")) for t in (source, translated)]
    if rows[0] != rows[1]:
        issues.append(f"table rows {rows[0]} -> {rows[1]}")
    return issues
