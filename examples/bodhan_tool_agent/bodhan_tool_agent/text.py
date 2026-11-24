from __future__ import annotations

import math
import re
from dataclasses import dataclass

import regex

MAX_TRANSLATION_TEXT_CHARACTERS = 4000


class TextChunkingError(ValueError):
    """Raised when text cannot be split safely."""


@dataclass(frozen=True)
class TextChunk:
    text: str
    start: int
    end: int


def grapheme_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for match in regex.finditer(r"\X", text):
        spans.append((match.start(), match.end()))
    return spans


def grapheme_boundaries(text: str) -> list[int]:
    spans = grapheme_spans(text)
    if not spans:
        return [0]
    boundaries = [0]
    boundaries.extend(end for _, end in spans)
    return boundaries


def _preferred_boundary(
    text: str,
    start: int,
    candidate_end: int,
    cluster_ends: list[int],
) -> int:
    if candidate_end >= len(text):
        return len(text)

    for pattern in (r"\r?\n\r?\n", r"[.!?।॥](?:[\"'”’)\]]*)[\t ]*"):
        matches = list(re.finditer(pattern, text[start:candidate_end]))
        if matches:
            match_end = start + matches[-1].end()
            valid = [
                end
                for end in cluster_ends
                if match_end <= end <= candidate_end
            ]
            if valid:
                return valid[0]

    whitespace = max(
        text.rfind(" ", start, candidate_end),
        text.rfind("\t", start, candidate_end),
        text.rfind("\n", start, candidate_end),
    )
    if whitespace >= start:
        match_end = whitespace + 1
        valid = [
            end
            for end in cluster_ends
            if match_end <= end <= candidate_end
        ]
        if valid:
            return valid[0]
    return candidate_end


def split_text(
    text: str,
    max_characters: int = MAX_TRANSLATION_TEXT_CHARACTERS,
) -> list[TextChunk]:
    if not isinstance(text, str):
        raise TextChunkingError("Translation text must be a string")
    if not text.strip():
        raise TextChunkingError("Translation text must not be empty")
    if isinstance(max_characters, bool) or not isinstance(max_characters, int):
        raise TextChunkingError("Translation chunk size must be a positive integer")
    if max_characters <= 0:
        raise TextChunkingError("Translation chunk size must be a positive integer")
    if not math.isfinite(float(max_characters)):
        raise TextChunkingError("Translation chunk size must be finite")

    spans = grapheme_spans(text)
    if not spans:
        raise TextChunkingError("Translation text produced no grapheme clusters")

    for cluster_start, cluster_end in spans:
        if cluster_end - cluster_start > max_characters:
            raise TextChunkingError(
                "A single Unicode grapheme cluster exceeds the "
                f"{max_characters}-character internal translation chunk limit"
            )

    cluster_ends = [end for _, end in spans]
    chunks: list[TextChunk] = []
    start = 0
    length = len(text)
    while start < length:
        limit = min(start + max_characters, length)
        candidate_ends = [
            end
            for end in cluster_ends
            if start < end <= limit
        ]
        if not candidate_ends:
            next_span = next(
                (span for span in spans if span[0] == start),
                None,
            )
            if next_span is None:
                raise TextChunkingError(
                    "Translation chunking could not find a grapheme boundary"
                )
            raise TextChunkingError(
                "A single Unicode grapheme cluster exceeds the "
                f"{max_characters}-character internal translation chunk limit"
            )

        candidate_end = candidate_ends[-1]
        end = _preferred_boundary(
            text,
            start,
            candidate_end,
            cluster_ends,
        )
        if end <= start:
            raise TextChunkingError(
                "Translation chunking could not make forward progress"
            )
        if end > candidate_end:
            end = candidate_end

        chunks.append(TextChunk(text[start:end], start, end))
        start = end
    if not chunks:
        raise TextChunkingError("Translation text produced no safe chunks")
    return chunks


def chunk_count(text: str, max_characters: int = MAX_TRANSLATION_TEXT_CHARACTERS) -> int:
    return len(split_text(text, max_characters))
