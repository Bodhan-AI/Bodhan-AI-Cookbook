"""Stage 5: translate every unit with Indic-Translate, protect kept terms, check the result in code."""

import logging
import re
from pathlib import Path

import yaml

from .bodhan import Bodhan, TruncatedError
from .text import numbers

log = logging.getLogger(__name__)


def load_glossary(path: Path) -> list[dict]:
    """Read --glossary. Format:

        keep:            # stay exactly as written, in every language (names, products, code)
          - Bodhan
          - NumPy
        terms:           # translate, but the planner spells them this way and keeps them consistent
          - linear pair
    """
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError("glossary must be a mapping with 'keep' and/or 'terms' lists")
    keep = [{"term": str(t), "keep": True, "from": "user"} for t in data.get("keep") or []]
    terms = [{"term": str(t), "keep": False, "from": "user"} for t in data.get("terms") or []]
    return keep + terms


def translate(units: list[dict], glossary: list[dict], lang: str, bodhan: Bodhan, script: str = "native") -> list[dict]:
    """Translate all units, then check each translation.

    Units go in one request, one per line, so the model sees the whole video and renders terms
    consistently. If the line count comes back wrong (the model sometimes merges two lines),
    the batch is halved and retried, down to single units, which cannot misalign.
    Terms marked "keep" are swapped for placeholders before translation and put back after, so
    they come through exactly as written; a line whose placeholders get lost is re-translated
    without them.
    """
    keep = kept_terms(glossary)
    protected = [protect(u["text"], keep) for u in units]
    texts = _translate_lines([p for p, _ in protected], lang, bodhan, script)

    rows = []
    for unit, (_, slots), text in zip(units, protected, texts, strict=True):
        restored = restore(text, slots)
        if restored is None:
            log.info("[%s] unit %d: placeholders lost, translating without them", lang, unit["id"], extra={"lang": lang, "unit": unit["id"]})
            restored = _translate_lines([unit["text"]], lang, bodhan, script)[0]
        rows.append({"id": unit["id"], "start": unit["start"], "end": unit["end"], "source": unit["text"],
                     "translation": restored, "translation_qa": check(unit["text"], restored, keep)})
        if rows[-1]["translation_qa"]:
            log.info("[%s] unit %d: %s", lang, unit["id"], "; ".join(rows[-1]["translation_qa"]),
                     extra={"lang": lang, "unit": unit["id"]})
    return rows


def kept_terms(glossary: list[dict]) -> list[str]:
    """Terms that must stay as written, longest first so "Titan Centaur" wins over "Titan"."""
    return sorted({g["term"] for g in glossary if g.get("keep")}, key=len, reverse=True)


def translate_text(text: str, keep: list[str], lang: str, bodhan: Bodhan, script: str = "native") -> str:
    """Translate one piece of text with kept terms protected (used when a shortened line is re-translated)."""
    protected, slots = protect(text, keep)
    restored = restore(_translate_lines([protected], lang, bodhan, script)[0], slots)
    return restored if restored is not None else _translate_lines([text], lang, bodhan, script)[0]


def check(source: str, translation: str, keep: list[str]) -> list[str]:
    """Problems with a translation that code can see: changed numbers, lost kept terms or labels."""
    problems = []
    if numbers(source) != numbers(translation):
        problems.append(f"numbers differ: {numbers(source)} vs {numbers(translation)}")
    labels = set(keep) | set(re.findall(r"\b[A-Z]{2,4}\b", source))  # letter labels like AOC stay as written
    missing = sorted(t for t in labels if _find(t, source) and t not in translation)
    if missing:
        problems.append(f"missing kept terms: {missing}")
    return problems


def protect(text: str, keep: list[str]) -> tuple[str, list[str]]:
    """Replace each kept term with a numbered placeholder ⟦n⟧, which Indic-Translate leaves alone."""
    slots: list[str] = []
    for term in keep:
        def swap(m, term=term):
            slots.append(term)
            return f"⟦{len(slots) - 1}⟧"
        text = re.sub(_pattern(term), swap, text)
    return text, slots


def restore(text: str, slots: list[str]) -> str | None:
    """Put kept terms back. None if any placeholder is missing or duplicated."""
    for i, term in enumerate(slots):
        if text.count(f"⟦{i}⟧") != 1:
            return None
        text = text.replace(f"⟦{i}⟧", term)
    return None if "⟦" in text else text


def _pattern(term: str) -> str:
    return rf"(?<!\w){re.escape(term)}(?!\w)"


def _find(term: str, text: str) -> bool:
    return re.search(_pattern(term), text) is not None


def _translate_lines(lines: list[str], lang: str, bodhan: Bodhan, script: str) -> list[str]:
    if lang == "en" or not lines:  # English is re-voiced as it is; nothing to send for no lines
        return list(lines)
    try:
        reply = bodhan.translate("\n".join(lines), lang, script)
        got = [line.strip() for line in reply.split("\n") if line.strip()]
    except TruncatedError:
        if len(lines) == 1:
            raise
        got = []
    if len(lines) == 1:
        return [" ".join(got)]
    if len(got) == len(lines):
        return got
    log.info("[%s] %d lines came back as %d; splitting the batch", lang, len(lines), len(got), extra={"lang": lang})
    mid = len(lines) // 2
    return _translate_lines(lines[:mid], lang, bodhan, script) + _translate_lines(lines[mid:], lang, bodhan, script)
