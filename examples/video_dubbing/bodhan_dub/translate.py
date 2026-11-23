"""Translation with enough context to be worth reading.

``indic-translate`` takes text and a target language — no context field, no
glossary, no source language (it detects that itself). That single fact decides
the design:

* **Context comes from batching.** A four-word fragment translated alone is
  noise. So consecutive segments are merged into a block of roughly one to three
  sentences, and the block is translated as one unit. That is the only way to
  give the model surrounding words.
* **Alignment comes from line structure.** The model preserves paragraph
  structure, so the block is sent with one segment per line and the translation
  comes back with one translated line per segment. That gives per-segment text
  *and* block-level context — the thing proportional splitting could never do,
  because Indic word order does not follow English.
* **Timing comes from the original segments.** Each translated line keeps its
  source segment's start and end, so no time is redistributed at all.
* When the model does not return exactly one line per segment, the pipeline
  falls back to splitting the translation in proportion to segment duration and
  records ``alignment: "proportional"`` so the degradation is visible.
* **Terminology is verified, not enforced.** See ``glossary.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from . import text
from .glossary import Glossary

MIN_BLOCK_CHARS = 40
_WORD_BOUNDARY = re.compile(r"\s+")


@dataclass(frozen=True)
class Block:
    index: int
    segment_ids: tuple[int, ...]
    text: str
    start: float
    end: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "segments": list(self.segment_ids),
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "source_chars": len(self.text),
        }


def build_blocks(segments: list[dict[str, Any]], max_chars: int = 250) -> list[Block]:
    """Group segments so each translation request has real context.

    Segments are joined with newlines, not spaces: one line per segment is what
    lets the translation come back aligned.
    """
    blocks: list[Block] = []
    current: list[dict[str, Any]] = []

    def joined(items: list[dict[str, Any]]) -> str:
        return "\n".join(item["text"] for item in items)

    def flush() -> None:
        nonlocal current
        if not current:
            return
        blocks.append(
            Block(
                index=len(blocks),
                segment_ids=tuple(item["id"] for item in current),
                text=joined(current),
                start=float(current[0]["start"]),
                end=float(current[-1]["end"]),
            )
        )
        current = []

    for segment in segments:
        if current and len(joined(current + [segment])) > max_chars:
            flush()
        current.append(segment)
        if text.ends_sentence(segment["text"]) and len(joined(current)) >= MIN_BLOCK_CHARS:
            flush()

    flush()
    return blocks


def align_lines(translation: str, expected: int) -> list[str] | None:
    """One translated line per segment, or None if the output does not align."""
    raw_lines = translation.splitlines()
    lines = [text.normalize_ws(line) for line in raw_lines]
    lines = [line for line in lines if line]
    if expected == 1:
        collapsed = text.normalize_ws(translation)
        return [collapsed] if collapsed else None
    if len(lines) == expected:
        return lines
    return None


def split_translation(translation: str, weights: list[float]) -> list[str]:
    """Distribute a translation across slots, at word boundaries, by weight.

    Weights are normally segment durations, because duration is what the audio
    has to fill. Every slot gets at least one word when there are enough words
    to go round; otherwise the extras are left empty rather than padded.
    """
    count = len(weights)
    cleaned = text.normalize_ws(translation)
    if count <= 1:
        return [cleaned]
    words = cleaned.split()
    if not words:
        return [""] * count

    if len(words) < count:
        # Fewer words than slots: fill from the start, leave the rest empty.
        return [" ".join(words[i : i + 1]) if i < len(words) else "" for i in range(count)]

    total = sum(max(weight, 1e-6) for weight in weights)
    pieces: list[str] = []
    cursor = 0
    cumulative = 0.0
    for index, weight in enumerate(weights):
        cumulative += max(weight, 1e-6)
        target = round(cumulative / total * len(words))
        minimum = cursor + 1
        maximum = len(words) - (count - index - 1)
        end = min(max(target, minimum), maximum)
        pieces.append(" ".join(words[cursor:end]))
        cursor = end
    return pieces


def translate_segments(
    segments: list[dict[str, Any]],
    *,
    lang: str,
    client: Any,
    glossary: Glossary,
    code_mixed: bool,
    max_block_chars: int,
    logger: Any = None,
) -> dict[str, Any]:
    """Translate every segment, returning per-segment records."""
    if not segments:
        return {"segments": [], "blocks": [], "glossary_issues": []}

    blocks = build_blocks(segments, max_block_chars)
    script = "codemix" if code_mixed else None
    by_id = {segment["id"]: segment for segment in segments}

    results: list[dict[str, Any]] = []
    alignment_counts = {"lines": 0, "proportional": 0}
    truncated_blocks = 0

    for block in blocks:
        result = client.translate(
            block.text,
            target_language=lang,
            script=script,
            stage="translate",
            item=block.index,
        )
        raw_translation = str(result.data.get("translation", ""))
        if result.data.get("truncated"):
            truncated_blocks += 1
        weights = [float(by_id[sid].get("duration_s", 1.0)) for sid in block.segment_ids]

        aligned = align_lines(raw_translation, len(block.segment_ids))
        if aligned is not None:
            pieces = aligned
            alignment = "lines"
        else:
            pieces = split_translation(raw_translation, weights)
            alignment = "proportional"
            if logger is not None:
                from .log import event

                event(
                    logger,
                    "translate.unaligned",
                    level=30,
                    stage="translate",
                    block=block.index,
                    expected_lines=len(block.segment_ids),
                    received_lines=len([line for line in raw_translation.splitlines() if line.strip()]),
                )
        alignment_counts[alignment] += 1

        for sid, piece in zip(block.segment_ids, pieces, strict=False):
            segment = by_id[sid]
            results.append(
                {
                    "id": sid,
                    "start": segment["start"],
                    "end": segment["end"],
                    "budget_s": round(float(segment.get("duration_s", 0.0)), 3),
                    "source": segment["text"],
                    "translation": piece,
                    "block": block.index,
                    "alignment": alignment,
                    "empty": not piece.strip(),
                }
            )

    issues = glossary.check(
        {segment["id"]: segment["text"] for segment in segments},
        {item["id"]: item["translation"] for item in results},
        lang,
    )

    return {
        "segments": results,
        "blocks": [block.to_dict() for block in blocks],
        "glossary_issues": issues,
        "alignment": alignment_counts,
        "truncated_blocks": truncated_blocks,
    }
