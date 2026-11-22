"""Stage 5b: write the English narration script, section by section.

For each batch of consecutive lesson elements the text LLM writes teacher-style narration;
code then checks it (no spoken symbols, every element covered, length caps for examples and
exercise problems, numbers kept) and repairs or replaces anything that falls short. The script
is English only: translation into other languages happens later with Indic-Translate.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from bodhan_lesson import config
from bodhan_lesson.clients import llm
from bodhan_lesson.models import FigureDescription, NarrationUnit, Section, SectionNarration
from bodhan_lesson.stages.assemble import Lesson
from bodhan_lesson.stages.narrate import prompts, validate
from bodhan_lesson.stages.narrate.grouping import Batch, group_elements, make_batches, place_figures_at_first_reference
from bodhan_lesson.text import speech

log = logging.getLogger(__name__)


@dataclass
class NarrationStats:
    batches: int = 0
    llm_retries: int = 0
    splits: int = 0
    batches_with_fallback: int = 0
    llm_units: int = 0
    fallback_units: int = 0
    skipped_elements: int = 0
    llm_failures: list[str] = field(default_factory=list)


def _ask_llm(client: llm.LlmClient, messages: list[dict], batch: Batch,
             stats: NarrationStats, model: str | None = None) -> tuple[list[validate.Unit], bool]:
    """Ask for narration, feeding problems back for up to NARRATION_RETRIES retries.

    Returns (units, too_large); `too_large` means the endpoint refused the request as over its
    size limit (HTTP 413) or the model ran out of tokens before answering; the caller splits the batch.
    """
    units: list[validate.Unit] = []
    for attempt in range(1 + config.NARRATION_RETRIES):
        raw = None
        try:
            raw = client.complete(messages, model=model)
            units, issues = validate.check_batch(llm.extract_json(raw), batch)
        except llm.LlmError as exc:
            units, issues = [], [str(exc)]
            if exc.status in (413, 429) or isinstance(exc, llm.TruncatedError):
                return [], True  # too much for one call: the caller splits the batch
            if exc.status is not None and exc.status != 429 and 400 <= exc.status < 500:
                stats.llm_failures.append(str(exc))
                return units, False  # the endpoint rejects the request itself: retrying cannot help
        if not issues:
            return units, False
        log.warning("narration attempt %d had %d problem(s): %s", attempt + 1, len(issues), issues[0])
        if attempt < config.NARRATION_RETRIES:
            stats.llm_retries += 1
            if raw is not None:
                messages = [*messages, llm.text_message("assistant", raw)]
            messages = [*messages, llm.text_message("user", validate.feedback(issues))]
    return units, False  # still imperfect: repair_batch will fix what it can


def narrate(lesson: Lesson, figures: dict[str, FigureDescription], client: llm.LlmClient | None
            ) -> tuple[list[SectionNarration], NarrationStats]:
    stats = NarrationStats()
    by_id = {e.id: e for e in lesson.elements}
    captions = {e.paired_with: speech.plain_speech(e.markdown).rstrip(".")
                for e in lesson.elements if e.paired_with}
    results: list[SectionNarration] = []
    previous = ""
    results_known = prompts.known_results(lesson.elements)
    for section in lesson.sections:
        narration = SectionNarration(section.id, section.title, section.slug)
        elements = place_figures_at_first_reference(_narratable(section, by_id, narration), captions)
        extra = {i: f"{f.caption} {f.description}" for i, f in figures.items()}
        for batch in make_batches(group_elements(elements), config.NARRATION_BATCH_CHARS, extra):
            opening = not results and not narration.units
            units = _narrate_batch(batch, section, figures, captions, client, stats, opening, previous, narration,
                                   results_known)
            for unit in units:
                narration.units.append(NarrationUnit(
                    id=f"{section.id}.u{len(narration.units) + 1:02d}", section_id=section.id,
                    element_ids=unit["element_ids"], kind=batch.group_of[unit["element_ids"][0]].kind,
                    text=unit["text"], origin=unit["origin"], warnings=unit["warnings"]))
            if narration.units:
                previous = prompts.previous_tail(narration.units[-1].text)
        stats.llm_units += sum(u.origin == "llm" for u in narration.units)
        stats.fallback_units += sum(u.origin == "fallback" for u in narration.units)
        stats.skipped_elements += len(narration.skipped)
        results.append(narration)
    return results, stats


def _narratable(section: Section, by_id: dict, narration: SectionNarration) -> list:
    """The section's elements the narrator should see: no paired captions, no doubtful blocks."""
    elements = []
    for element_id in section.element_ids:
        element = by_id[element_id]
        if element.paired_with:
            continue  # spoken together with its figure
        if element.doubtful:
            narration.skipped.append({"element": element.id, "reason": "doubtful OCR or math: never narrated"})
            continue
        elements.append(element)
    return elements


def _narrate_batch(batch: Batch, section: Section, figures: dict, captions: dict, client: llm.LlmClient | None,
                   stats: NarrationStats, opening: bool, previous: str, narration: SectionNarration,
                   results_known: list[str] | None = None) -> list[validate.Unit]:
    stats.batches += 1
    units: list[validate.Unit] = []
    if client is not None:
        practice = any(g.kind == "exercise_problem" for g in batch.groups)
        messages = [llm.text_message("system", prompts.EXERCISE_PROMPT if practice else prompts.SYSTEM_PROMPT),
                    llm.text_message("user", prompts.user_message(
                        batch, section.title, captions, figures, opening=opening, previous=previous,
                        known_results=results_known if practice else None))]
        units, too_large = _ask_llm(client, messages, batch, stats,
                                    client.settings.exercise_model if practice else None)
        if too_large and len(batch.groups) > 1:
            stats.splits += 1
            middle = len(batch.groups) // 2
            first = _narrate_batch(Batch(batch.groups[:middle], batch.extra_text), section, figures, captions, client, stats,
                                   opening, previous, narration, results_known)
            tail = prompts.previous_tail(first[-1]["text"]) if first else previous
            return first + _narrate_batch(Batch(batch.groups[middle:], batch.extra_text), section, figures, captions, client, stats,
                                          False, tail, narration, results_known)
        if too_large:
            stats.llm_failures.append("a single group is larger than the endpoint's request limit")
    units, skipped = validate.repair_batch(units, batch, figures, captions)
    narration.skipped += skipped
    if any(u["origin"] == "fallback" for u in units):
        stats.batches_with_fallback += 1
    return units
