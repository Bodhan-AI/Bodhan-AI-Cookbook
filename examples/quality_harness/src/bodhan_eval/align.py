"""Span alignment utilities: sentence splitting across scripts, character-level diff of two
normalized strings with offsets mapped back to original coordinates, and span-overlap used
by both localization scoring and calibration against human labels.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from .models import Span
from .normalize import NormResult, normalize

# sentence boundary: ASCII . ? ! plus danda (already mapped to '.' by normalize), and newlines.
# We split on the ORIGINAL text so sentence spans are in original coordinates.
_SENT_BOUNDARY = re.compile(r"(?<=[.!?।॥])\s+|\n{1,}")


@dataclass
class Sentence:
    text: str
    span: Span  # original coordinates


def split_sentences(text: str) -> list[Sentence]:
    """Split into sentences using punctuation and Markdown block boundaries. Robust to any
    script because the delimiters are script-independent (danda is handled explicitly)."""
    if not text:
        return []
    sentences: list[Sentence] = []
    pos = 0
    for m in _SENT_BOUNDARY.finditer(text):
        end = m.start()
        chunk = text[pos:end]
        if chunk.strip():
            sentences.append(Sentence(text=chunk, span=(pos, end)))
        pos = m.end()
    tail = text[pos:]
    if tail.strip():
        sentences.append(Sentence(text=tail, span=(pos, len(text))))
    return sentences


@dataclass
class DiffOp:
    tag: str  # "replace" | "delete" | "insert" | "equal"
    a_span: Span  # span in a's ORIGINAL coordinates
    b_span: Span  # span in b's ORIGINAL coordinates


def diff_original(a_norm: NormResult, b_norm: NormResult) -> list[DiffOp]:
    """Character diff between two normalized strings, with spans mapped to original coords."""
    sm = SequenceMatcher(a=a_norm.text, b=b_norm.text, autojunk=False)
    ops: list[DiffOp] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        a_span = a_norm.to_original_span((i1, i2)) or (0, 0)
        b_span = b_norm.to_original_span((j1, j2)) or (0, 0)
        ops.append(DiffOp(tag=tag, a_span=a_span, b_span=b_span))
    return ops


def diff_texts(a: str, b: str, lang: str | None = None) -> list[DiffOp]:
    return diff_original(normalize(a, lang), normalize(b, lang))


# ---- span overlap (localization + label matching) ------------------------------------


def _intersect(a: Span, b: Span) -> int:
    lo = max(a[0], b[0])
    hi = min(a[1], b[1])
    return max(0, hi - lo)


def spans_overlap(a: Span | None, b: Span | None) -> bool:
    if a is None or b is None:
        return False
    return _intersect(a, b) > 0


def overlap_iou(a: Span, b: Span) -> float:
    inter = _intersect(a, b)
    union = (a[1] - a[0]) + (b[1] - b[0]) - inter
    return inter / union if union > 0 else 0.0


def overlap_fraction_of(label: Span, predicted: Span) -> float:
    """Fraction of the label span covered by the predicted span (localization metric)."""
    inter = _intersect(label, predicted)
    length = label[1] - label[0]
    return inter / length if length > 0 else (1.0 if inter > 0 else 0.0)
