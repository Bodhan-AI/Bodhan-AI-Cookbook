"""Character error rate between a script and its back-transcription."""

from __future__ import annotations

import re
import unicodedata

_DROP = re.compile(r"[\W_]+", re.UNICODE)


def normalise(text: str) -> str:
    """Case-fold, NFC-normalise and drop punctuation and whitespace, so only letters are compared.
    Combining marks (\\w includes them in Python's re) are kept: they carry Indic vowels."""
    return _DROP.sub("", unicodedata.normalize("NFC", text).casefold())


def edit_distance(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def errors(reference: str, hypothesis: str) -> tuple[int, int]:
    """(edit distance, reference length) over normalised characters."""
    ref, hyp = normalise(reference), normalise(hypothesis)
    return edit_distance(ref, hyp), len(ref)


def cer(reference: str, hypothesis: str) -> float:
    distance, length = errors(reference, hypothesis)
    return distance / length if length else 0.0
