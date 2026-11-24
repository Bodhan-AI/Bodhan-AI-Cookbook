"""Machine checks on the LLM's narration, and the repairs applied when it still falls short.

`check_batch` finds problems (they are fed back to the LLM for a retry). `repair_batch` then
guarantees a usable result whatever the LLM did: every element is narrated or skipped with a
reason, no symbol reaches the TTS, and examples and exercise problems respect their caps.
"""

from __future__ import annotations

from typing import Any

from bodhan_lesson import config
from bodhan_lesson.models import FigureDescription
from bodhan_lesson.stages.narrate import fallback
from bodhan_lesson.stages.narrate.grouping import EXAMPLE, EXERCISE, EXPLANATION, Batch, Group
from bodhan_lesson.text import speech

Unit = dict[str, Any]  # {"element_ids": [...], "text": str, "origin": "llm"|"fallback", "warnings": [...]}
WORD_CAPS = {EXERCISE: config.EXERCISE_MAX_WORDS, EXAMPLE: config.EXAMPLE_MAX_WORDS}


def _first_group(unit: Unit, batch: Batch) -> Group:
    return batch.group_of[unit["element_ids"][0]]


def _latex_of(unit: Unit, batch: Batch) -> str:
    return " ".join(batch.elements[i].markdown for i in unit["element_ids"] if batch.elements[i].kind == "equation")


def _group_words(units: list[Unit], group: Group, batch: Batch) -> int:
    return sum(speech.word_count(u["text"]) for u in units if _first_group(u, batch) is group)


def _group_source(group: Group, batch: Batch) -> str:
    """Everything the page (and the figure description) says in this group, for number checks."""
    return " ".join(batch.elements[e.id].markdown + " " + batch.extra_text.get(e.id, "") for e in group.elements)


def _invented_numbers(units: list[Unit], group: Group, batch: Batch) -> list[str]:
    spoken = " ".join(u["text"] for u in units if _first_group(u, batch) is group)
    return speech.unexpected_numbers(_group_source(group, batch), spoken)


def parse_units(data: dict[str, Any], batch: Batch) -> tuple[list[Unit], list[str]]:
    """Read the `units` list; unknown element ids are dropped and reported."""
    raw_units = data.get("units")
    if not isinstance(raw_units, list) or not raw_units:
        return [], ['the JSON must contain a non-empty "units" list']
    units: list[Unit] = []
    issues: list[str] = []
    for number, raw in enumerate(raw_units, start=1):
        text = raw.get("text") if isinstance(raw, dict) else None
        ids = raw.get("element_ids") if isinstance(raw, dict) else None
        if not isinstance(text, str) or not text.strip() or not isinstance(ids, list):
            issues.append(f"unit {number} needs non-empty string text and an element_ids list")
            continue
        known = [i for i in ids if i in batch.elements]
        if len(known) != len(ids):
            issues.append(f"unit {number} lists unknown element ids {[i for i in ids if i not in batch.elements]}")
        if known:
            units.append({"element_ids": known, "text": " ".join(text.split()), "origin": "llm", "warnings": []})
    return units, issues


def check_batch(data: dict[str, Any], batch: Batch) -> tuple[list[Unit], list[str]]:
    """Parse the answer and list every rule it breaks."""
    units, issues = parse_units(data, batch)
    covered = {i for u in units for i in u["element_ids"]}
    if missing := [i for i in batch.ids if i not in covered]:
        issues.append(f"these element ids are not narrated: {missing}")
    for number, unit in enumerate(units, start=1):
        if symbols := speech.forbidden_symbols(unit["text"]):
            issues.append(f"unit {number} contains symbols that cannot be spoken ({' '.join(symbols)}); say them in words")
        if speech.word_count(unit["text"]) > config.UNIT_MAX_WORDS:
            issues.append(f"unit {number} is longer than {config.UNIT_MAX_WORDS} words; split it")
        if _first_group(unit, batch).kind == EXPLANATION and (
                lost := speech.missing_numbers(_latex_of(unit, batch), unit["text"])):
            issues.append(f"unit {number} leaves out numbers from its equations: {lost}")
    for group in batch.groups:
        cap = WORD_CAPS.get(group.kind)
        label = "an exercise problem" if group.kind == EXERCISE else "an example"
        where = [e.id for e in group.elements][:2]
        if cap and (words := _group_words(units, group, batch)) > cap:
            issues.append(f"{label} (elements {where}...) uses {words} words; the maximum is {cap}")
        if cap and (invented := _invented_numbers(units, group, batch)):
            issues.append(f"{label} (elements {where}...) states numbers that are not in its text: {invented}. "
                          "Never state results you worked out yourself; describe the method instead")
    return units, issues


def feedback(issues: list[str]) -> str:
    return ("Your answer broke these rules:\n- " + "\n- ".join(issues)
            + "\nReturn the corrected JSON object only, following every rule in the instructions.")


# ---- repair --------------------------------------------------------------------
def _fallback_unit(element_id: str, batch: Batch, figures: dict[str, FigureDescription],
                   captions: dict[str, str], skipped: list[dict[str, str]]) -> Unit | None:
    text, reason = fallback.fallback_text(batch.elements[element_id], figures.get(element_id), captions.get(element_id, ""))
    if text is None:
        skipped.append({"element": element_id, "reason": reason})
        return None
    return {"element_ids": [element_id], "text": text, "origin": "fallback", "warnings": ["deterministic fallback"]}


def _enforce_caps(units: list[Unit], batch: Batch) -> list[Unit]:
    for group in batch.groups:
        cap = WORD_CAPS.get(group.kind)
        mine = [u for u in units if _first_group(u, batch) is group]
        if not cap or not mine or sum(speech.word_count(u["text"]) for u in mine) <= cap:
            continue
        merged = {"element_ids": [i for u in mine for i in u["element_ids"]],
                  "text": speech.trim_to_words(" ".join(u["text"] for u in mine), cap),
                  "origin": "fallback" if all(u["origin"] == "fallback" for u in mine) else "llm",
                  "warnings": [w for u in mine for w in u["warnings"]] + [f"trimmed to {cap} words"]}
        units = [u for u in units if u not in mine[1:]]
        units[units.index(mine[0])] = merged
    return units


def repair_batch(units: list[Unit], batch: Batch, figures: dict[str, FigureDescription],
                 captions: dict[str, str]) -> tuple[list[Unit], list[dict[str, str]]]:
    """Make the units safe to speak: narrate what is missing, scrub symbols, enforce caps."""
    skipped: list[dict[str, str]] = []
    covered = {i for u in units for i in u["element_ids"]}
    for element_id in batch.ids:
        if element_id not in covered:
            if unit := _fallback_unit(element_id, batch, figures, captions, skipped):
                units.append(unit)
    for unit in units:
        if speech.forbidden_symbols(unit["text"]):
            unit["text"] = speech.scrub_symbols(unit["text"])
            unit["warnings"].append("symbols scrubbed")
    for unit in units:
        unit["text"] = speech.spell_angle_labels(unit["text"])
    units = _enforce_caps(units, batch)
    for unit in units:
        if _first_group(unit, batch).kind == EXPLANATION and (
                lost := speech.missing_numbers(_latex_of(unit, batch), unit["text"])):
            unit["warnings"].append(f"numbers possibly missing: {lost}")
    for group in batch.groups:
        if WORD_CAPS.get(group.kind) and (invented := _invented_numbers(units, group, batch)):
            first = next(u for u in units if _first_group(u, batch) is group)
            first["warnings"].append(f"numbers not in the source: {invented}")
    units = [u for u in units if u["text"].strip()]
    units.sort(key=lambda u: min(batch.position[i] for i in u["element_ids"]))
    return units, skipped
