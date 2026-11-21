"""Deterministic feasibility gate: rejects provably impossible tasks at ₹0."""

from __future__ import annotations

import re

from bodhan_agent.models import DeliverableSpec, FeasibilityResult, TaskSpec
from bodhan_mcp.pricing import cost_ocr, cost_speak, cost_transcribe, cost_translate, estimate_output_tokens
from bodhan_mcp.rules import (
    UNSUPPORTED_LANGUAGES,
    is_language_supported,
    language_names,
    resolve_language,
    supported_languages,
)
from bodhan_mcp.schemas import FileFact

# Regex to spot explicit unsupported target-language phrasing even before calling the LLM.
_TARGET_LANG_PHRASE = re.compile(
    r"\b(?:into|to|in)\s+([A-Za-z]+)\b",
    flags=re.IGNORECASE,
)


def pre_llm_feasibility_check(
    task: str,
    file_facts: list[FileFact],
    max_cost_inr: float,
) -> FeasibilityResult:
    """Fast ₹0 deterministic check before Epoch A (still works even if the LLM is unavailable).

    Only rejects provably impossible tasks:
      - Zero or negative budget when non-trivial input files require paid Bodhan models
      - Explicit unsupported target language requested in the task string (e.g. 'into French')
      - Every supplied input path is missing (file not found)
    """
    checks: list[str] = []

    if file_facts and all(not f.exists for f in file_facts):
        missing = [f.path for f in file_facts]
        return FeasibilityResult(
            feasible=False,
            reason=f"All specified input files are missing: {', '.join(missing)}.",
            cost_floor_inr=0.0,
            checks=["all_inputs_missing"],
        )

    # Check explicit "into <UnsupportedLang>" / "to <UnsupportedLang>" phrasing in task text.
    for m in _TARGET_LANG_PHRASE.finditer(task):
        word = m.group(1).lower()
        if word in UNSUPPORTED_LANGUAGES:
            iso = UNSUPPORTED_LANGUAGES[word]
            supported = ", ".join(supported_languages("indic-translate"))
            return FeasibilityResult(
                feasible=False,
                reason=(
                    f"Requested language '{m.group(1)}' ({iso}) is not supported by Bodhan models. "
                    f"Bodhan supports English and 22+ Indian languages ({supported})."
                ),
                cost_floor_inr=0.0,
                checks=[f"unsupported_language:{iso}"],
            )

    if max_cost_inr < 0:
        return FeasibilityResult(
            feasible=False,
            reason=f"Invalid budget: --max-cost-inr ({max_cost_inr}) cannot be negative.",
            cost_floor_inr=0.0,
            checks=["negative_budget"],
        )

    checks.append("pre_llm_ok")
    return FeasibilityResult(feasible=True, cost_floor_inr=0.0, checks=checks)


def _required_models_for_deliverable(d: DeliverableSpec, file_facts: list[FileFact]) -> list[str]:
    models: list[str] = []
    kinds = {f.kind for f in file_facts if f.exists}
    if "pdf" in kinds or "image" in kinds:
        models.append("indic-ocr")
    if "audio" in kinds or "video" in kinds:
        models.append("indic-transcribe")
    if d.kind == "audio":
        models.append("indic-speak")
        models.append("indic-transcribe")  # For mandatory verify_speech back-transcription.
    if d.language and d.kind in ("markdown", "text", "subtitles", "audio", "answer"):
        models.append("indic-translate")
    return list(dict.fromkeys(models))


def compute_cost_floor(task_spec: TaskSpec, file_facts: list[FileFact]) -> float:
    """Compute a conservative PROVABLE minimum cost floor in INR (never over-estimates!).

    Rejects ONLY when even the cheapest possible execution path exceeds --max-cost-inr.
    """
    existing = [f for f in file_facts if f.exists]
    floor = 0.0

    # Check if inputs are used directly.
    used_set = set(task_spec.inputs_used) if task_spec.inputs_used else {f.path for f in existing}
    used_facts = [f for f in existing if f.path in used_set] or existing

    needs_audio_output = any(d.kind == "audio" for d in task_spec.deliverables)
    needs_text_translation = any(
        d.language and d.kind in ("markdown", "text", "subtitles", "audio")
        for d in task_spec.deliverables
    )

    for f in used_facts:
        if f.kind in ("pdf", "image"):
            pages = f.page_count or 1
            floor += cost_ocr(pages)
        elif f.kind in ("audio", "video"):
            dur = f.duration_s or 0.0
            # If task only filters clips by language (probes), minimum is <=10s probe per clip.
            if any(p.path == f.path for p in task_spec.needs_probe) and len(used_facts) > 1:
                floor += cost_transcribe(min(10.0, dur))
            else:
                floor += cost_transcribe(dur)
        elif f.kind == "text" and needs_audio_output and f.char_count:
            # Reading a text file aloud directly requires synthesizing its characters.
            if not any(d.constraints.duration_s for d in task_spec.deliverables):
                floor += cost_speak(f.char_count)
        elif f.kind == "text" and needs_text_translation and f.char_count:
            floor += cost_translate(estimate_output_tokens("x" * f.char_count)) * 0.5

    # Duration-constrained speech synthesis floor (e.g. 2-minute summary ≈ at least ~900 chars of speech).
    for d in task_spec.deliverables:
        if d.kind == "audio" and d.constraints.duration_s:
            # Very conservative floor: ~8 chars/second * 0.8 (lower end of ±20% tolerance).
            min_chars = int(d.constraints.duration_s * 6.4)
            floor += cost_speak(min_chars)

    return round(floor, 6)


def check_feasibility(
    task_spec: TaskSpec,
    file_facts: list[FileFact],
    max_cost_inr: float,
) -> FeasibilityResult:
    """Full ₹0 feasibility gate on the parsed TaskSpec + FileFacts."""
    checks: list[str] = []

    if task_spec.infeasible_reason:
        return FeasibilityResult(
            feasible=False,
            reason=task_spec.infeasible_reason,
            cost_floor_inr=0.0,
            checks=["planner_declared_infeasible"],
        )

    # 1. Check missing required input files.
    missing_facts = [f for f in file_facts if not f.exists]
    existing_facts = [f for f in file_facts if f.exists]
    if file_facts and not existing_facts:
        return FeasibilityResult(
            feasible=False,
            reason=f"Required input file(s) not found: {', '.join(f.path for f in missing_facts)}.",
            cost_floor_inr=0.0,
            checks=["missing_files"],
        )

    # Check if inputs_used references a missing file.
    missing_paths = {f.path for f in missing_facts}
    for inp in task_spec.inputs_used:
        if inp in missing_paths:
            return FeasibilityResult(
                feasible=False,
                reason=f"Task requires missing input file: {inp}.",
                cost_floor_inr=0.0,
                checks=["missing_required_file"],
            )

    # Check unsupported/unknown file kinds when no valid file exists.
    if existing_facts and all(f.kind == "unknown" for f in existing_facts):
        return FeasibilityResult(
            feasible=False,
            reason=f"Unsupported file format(s): {', '.join(f.path for f in existing_facts)}.",
            cost_floor_inr=0.0,
            checks=["unsupported_file_kind"],
        )

    # 2. Validate source language if explicitly stated.
    if task_spec.source_language_raw and task_spec.source_language_raw.lower() not in ("auto", "unknown", "none", ""):
        iso, known = resolve_language(task_spec.source_language_raw)
        if not known:
            supported = ", ".join(supported_languages("indic-transcribe"))
            return FeasibilityResult(
                feasible=False,
                reason=(
                    f"Source language '{task_spec.source_language_raw}' is not supported by Bodhan models. "
                    f"Supported codes: {supported}."
                ),
                cost_floor_inr=0.0,
                checks=[f"unsupported_source_language:{task_spec.source_language_raw}"],
            )
        task_spec.source_language = iso

    # 3. Validate every deliverable's target language against the required Bodhan model capability table.
    names = language_names()
    for d in task_spec.deliverables:
        if d.language_raw:
            iso, known = resolve_language(d.language_raw)
            d.language = iso
            if not iso or not known:
                d.language_supported = False
                supported = ", ".join(supported_languages("indic-translate"))
                return FeasibilityResult(
                    feasible=False,
                    reason=(
                        f"Target language '{d.language_raw}' for deliverable '{d.id}' is not supported by Bodhan. "
                        f"Supported languages: {supported}."
                    ),
                    cost_floor_inr=0.0,
                    checks=[f"unsupported_target_language:{d.language_raw}"],
                )
            # Check specific model support (e.g. bho/bhb are transcribe-only, not translate/speak).
            if d.kind == "audio" and not is_language_supported("indic-speak", iso):
                return FeasibilityResult(
                    feasible=False,
                    reason=(
                        f"Language '{names.get(iso, iso)}' ({iso}) is not supported by indic-speak. "
                        f"Supported for TTS: {', '.join(supported_languages('indic-speak'))}."
                    ),
                    cost_floor_inr=0.0,
                    checks=[f"unsupported_speak_language:{iso}"],
                )
            if d.kind in ("markdown", "text", "subtitles", "audio") and not is_language_supported("indic-translate", iso):
                # Allow if text is already in that language and only needs reading/transcription.
                if d.kind != "audio" or not is_language_supported("indic-speak", iso):
                    return FeasibilityResult(
                        feasible=False,
                        reason=(
                            f"Language '{names.get(iso, iso)}' ({iso}) is not supported by indic-translate. "
                            f"Supported for translation: {', '.join(supported_languages('indic-translate'))}."
                        ),
                        cost_floor_inr=0.0,
                        checks=[f"unsupported_translate_language:{iso}"],
                    )
        # Also resolve filter_language if present.
        if d.constraints.filter_language:
            f_iso, f_known = resolve_language(d.constraints.filter_language)
            if not f_iso or not f_known:
                return FeasibilityResult(
                    feasible=False,
                    reason=f"Filter language '{d.constraints.filter_language}' is not supported by Bodhan.",
                    cost_floor_inr=0.0,
                    checks=[f"unsupported_filter_language:{d.constraints.filter_language}"],
                )
            d.constraints.filter_language = f_iso

    checks.append("languages_supported")

    # 4. Provable cost floor vs --max-cost-inr.
    floor = compute_cost_floor(task_spec, file_facts)
    checks.append(f"cost_floor_inr={floor:.4f}")
    if floor > max_cost_inr + 1e-9:
        return FeasibilityResult(
            feasible=False,
            reason=(
                f"Budget too small to complete this task: provable minimum Bodhan API cost is ₹{floor:.4f}, "
                f"which exceeds --max-cost-inr ₹{max_cost_inr:.2f}."
            ),
            cost_floor_inr=floor,
            checks=checks + ["budget_below_floor"],
        )

    checks.append("budget_floor_ok")
    return FeasibilityResult(feasible=True, cost_floor_inr=floor, checks=checks)
