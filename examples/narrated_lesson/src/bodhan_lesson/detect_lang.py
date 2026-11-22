"""Detect OCR/source language when --source-lang is omitted.

Uses Facebook fastText lid.176 via fasttext-langdetect (compressed model).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from bodhan_lesson.languages import SOURCE_LANGS

# fastText lid.176 codes that differ from Bodhan Translate codes.
_FASTTEXT_ALIASES: dict[str, str] = {
    "gom": "kok",  # Goan Konkani → Bodhan kok
    "bh": "hi",  # Bihari → Hindi (coarse)
    "pnb": "pa",  # Western Punjabi → Punjabi
}

_MIN_CHARS = 40
_MIN_SCORE = 0.25


def _default_detect(text: str) -> dict[str, Any]:
    from ftlangdetect import detect

    return detect(text=text, low_memory=True)


def map_fasttext_to_source(code: str) -> str | None:
    """Map a fastText lang code to a Bodhan SOURCE_LANGS code, or None."""
    raw = (code or "").strip().lower()
    if not raw:
        return None
    mapped = _FASTTEXT_ALIASES.get(raw, raw)
    if mapped in SOURCE_LANGS:
        return mapped
    return None


def detect_source_lang(
    text: str,
    *,
    detect_fn: Callable[[str], dict[str, Any]] | None = None,
    min_chars: int = _MIN_CHARS,
    min_score: float = _MIN_SCORE,
) -> tuple[str, float]:
    """Return (source_lang, score) from lesson/OCR text.

    Raises ValueError when text is too short, score is low, or the predicted
    code is not in SOURCE_LANGS — caller should pass --source-lang explicitly.
    """
    sample = " ".join((text or "").split())
    if len(sample) < min_chars:
        raise ValueError(
            f"Not enough text to detect source language ({len(sample)} chars; "
            f"need ≥{min_chars}). Pass --source-lang explicitly."
        )

    fn = detect_fn or _default_detect
    try:
        result = fn(sample)
    except Exception as exc:  # noqa: BLE001 — surface as detect failure
        raise ValueError(
            f"Source-language detection failed: {exc}. "
            "Pass --source-lang explicitly."
        ) from exc

    raw_lang = str(result.get("lang") or "")
    score = float(result.get("score") or 0.0)
    mapped = map_fasttext_to_source(raw_lang)
    if mapped is None:
        raise ValueError(
            f"Detected language '{raw_lang}' is not a supported --source-lang. "
            "Pass --source-lang explicitly "
            f"(allowed: {', '.join(sorted(SOURCE_LANGS))})."
        )
    if score < min_score:
        raise ValueError(
            f"Low-confidence source-language detection "
            f"({mapped}, score={score:.2f}). Pass --source-lang explicitly."
        )
    return mapped, score


def detect_source_lang_from_path(
    path: Path,
    *,
    detect_fn: Callable[[str], dict[str, Any]] | None = None,
) -> tuple[str, float]:
    """Detect source language from a UTF-8 lesson markdown file."""
    text = path.read_text(encoding="utf-8")
    return detect_source_lang(text, detect_fn=detect_fn)
