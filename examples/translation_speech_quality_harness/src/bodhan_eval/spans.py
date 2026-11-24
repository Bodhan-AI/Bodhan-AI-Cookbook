import re
from dataclasses import dataclass

from .normalize import Normalized

Span = tuple[int, int]


@dataclass(frozen=True)
class SegSpan:
    text: str
    span: Span


def to_original_span(norm: Normalized, span: Span) -> Span:
    start, end = span
    if not norm.map:
        insertion = min(max(start, 0), norm.original_length)
        return (insertion, insertion)
    start_index = min(max(start, 0), len(norm.map) - 1)
    start_original = norm.spans[start_index][0] if norm.spans else norm.map[start_index]
    if end > start:
        end_index = min(max(end - 1, 0), len(norm.map) - 1)
        end_original = norm.spans[end_index][1] if norm.spans else norm.map[end_index] + 1
    elif start_index < len(norm.map):
        end_original = start_original
    else:
        end_original = norm.original_length
    return (min(start_original, end_original), max(start_original, end_original))


def overlap(a: Span, b: Span, mode: str = "iou") -> float:
    a_start, a_end = a
    b_start, b_end = b
    if a_start == a_end and b_start == b_end:
        return 1.0
    intersection = max(0, min(a_end, b_end) - max(a_start, b_start))
    if mode == "min":
        return intersection / max(1, min(a_end - a_start, b_end - b_start))
    union = max(a_end, b_end) - min(a_start, b_start)
    return intersection / union if union else 0.0


def sentence_split(text: str, lang: str) -> list[SegSpan]:
    del lang
    segments: list[SegSpan] = []
    block_pattern = re.compile(r"(^#{1,6}\s.*$|^[-*+]\s.*$|^\d+\.\s.*$|^\|.*\|$)", re.MULTILINE)
    cursor = 0
    blocks = list(block_pattern.finditer(text))
    if blocks:
        for match in blocks:
            segment = match.group(0)
            segments.append(SegSpan(segment, (match.start(), match.end())))
            cursor = match.end()
    else:
        for match in re.finditer(r".*?(?:[.!?\u0964\u06d4]+(?:\s+|$)|$)", text, re.DOTALL):
            segment = match.group(0).strip()
            if segment:
                start = text.find(segment, cursor)
                end = start + len(segment)
                segments.append(SegSpan(segment, (start, end)))
                cursor = end
    return segments
