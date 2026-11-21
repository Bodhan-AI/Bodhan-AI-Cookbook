"""Deterministic Plan Compiler: expands foreach, validates schemas & scope, infers DAG waves."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from bodhan_mcp.pricing import cost_ocr, cost_speak, cost_transcribe, cost_translate
from bodhan_mcp.rules import is_language_supported, resolve_language, select_voice, supported_styles
from bodhan_mcp.schemas import FileFact
from bodhan_mcp.text_utils import contains_indic_script

from bodhan_agent.models import (
    CompiledPlan,
    CompiledStep,
    PlanStep,
    ProbeFact,
    RawPlan,
    TaskSpec,
)

REF_PATTERN = re.compile(r"^\$([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)$")
PATH_ARG_KEYS = frozenset({"path", "audio_path", "segments_path", "translations_path", "expected_path", "chunk_manifest_path", "text_ref", "source_ref"})
TOOL_MODEL_MAP = {
    "transcribe_audio": "indic-transcribe",
    "verify_speech": "indic-transcribe",
    "translate_text": "indic-translate",
    "synthesize_speech": "indic-speak",
    "ocr_document": "indic-ocr",
}

# Known output metadata fields per tool (for static $step.field compile checks & compact planner catalog).
TOOL_OUTPUT_FIELDS: dict[str, tuple[str, ...]] = {
    "inspect_files": ("files", "count"),
    "detect_language": ("language", "candidates", "confidence", "ambiguous", "method", "script_histogram", "path", "probe_duration_s"),
    "transcribe_audio": ("text_path", "segments_path", "language", "duration_s", "window_count", "segment_count", "char_count"),
    "translate_text": ("text_path", "target_language", "script", "chunks_in", "chunks_out", "truncation_splits", "coverage"),
    "synthesize_speech": ("audio_path", "chunk_manifest_path", "language", "voice", "style", "format", "duration_s", "chunk_count"),
    "verify_speech": ("passed", "mean_cer", "cer_threshold", "chunks_checked", "failed_chunk_indices", "verdicts"),
    "ocr_document": ("text_path", "page_count", "char_count", "pages", "table_shapes", "detected_language"),
    "write_subtitles": ("subtitle_path", "format", "segment_count", "cue_count", "validation"),
    "q_summarize_en": ("text_path", "word_count", "char_count", "target_words", "language"),
    "q_classify": ("choice", "question"),
    "q_extract_number": ("value", "question"),
    "merge_text_artifacts": ("text_path", "char_count", "item_count"),
    "publish_artifact": ("artifact_path", "deliverable_id", "filename"),
}


class PlanCompileError(RuntimeError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def _extract_refs(value: Any) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    if isinstance(value, str):
        m = REF_PATTERN.match(value)
        if m:
            refs.append((m.group(1), m.group(2)))
    elif isinstance(value, list):
        for item in value:
            refs.extend(_extract_refs(item))
    elif isinstance(value, dict):
        for v in value.values():
            refs.extend(_extract_refs(v))
    return refs


def _substitute_var(value: Any, var_name: str, item_dict: dict[str, Any]) -> Any:
    prefix = f"${var_name}."
    if isinstance(value, str) and value.startswith(prefix):
        field = value[len(prefix) :]
        return item_dict.get(field, value)
    if isinstance(value, list):
        return [_substitute_var(v, var_name, item_dict) for v in value]
    if isinstance(value, dict):
        return {k: _substitute_var(v, var_name, item_dict) for k, v in value.items()}
    return value


def _expand_foreach(
    steps: list[PlanStep],
    file_facts: list[FileFact],
    probe_facts: list[ProbeFact],
) -> list[PlanStep]:
    """Deterministically expand foreach steps against probe_facts or file_facts."""
    expanded: list[PlanStep] = []
    expanded_group_ids: dict[str, list[str]] = {}

    for step in steps:
        if step.foreach is None:
            # Allow $group_id.field inside lists (e.g. merge_text_artifacts) to expand into all group members!
            new_args = dict(step.args)
            for k, v in list(new_args.items()):
                if isinstance(v, list):
                    flat: list[Any] = []
                    for elem in v:
                        if isinstance(elem, str) and elem.startswith("$") and "." in elem:
                            gid, fld = elem[1:].split(".", 1)
                            if gid in expanded_group_ids:
                                flat.extend(f"${sub_id}.{fld}" for sub_id in expanded_group_ids[gid])
                                continue
                        flat.append(elem)
                    new_args[k] = flat
            expanded.append(step.model_copy(update={"args": new_args}))
            continue

        source_items = (
            [p.model_dump() for p in probe_facts]
            if step.foreach.over == "probe_facts"
            else [f.model_dump() for f in file_facts]
        )
        matched = [
            item
            for item in source_items
            if all(item.get(fk) == fv for fk, fv in step.foreach.filter.items())
        ]
        sub_ids: list[str] = []
        for idx, item in enumerate(matched):
            sub_id = f"{step.id}_{idx}"
            sub_ids.append(sub_id)
            sub_args = _substitute_var(step.args, step.foreach.as_var, item)
            # Also map $prev_group.field -> $prev_group_{idx}.field when chaining foreach groups.
            for k, v in list(sub_args.items()):
                if isinstance(v, str) and v.startswith("$") and "." in v:
                    gid, fld = v[1:].split(".", 1)
                    if gid in expanded_group_ids and idx < len(expanded_group_ids[gid]):
                        sub_args[k] = f"${expanded_group_ids[gid][idx]}.{fld}"
            expanded.append(
                PlanStep(
                    id=sub_id,
                    tool=step.tool,
                    args=sub_args,
                    deliverable_id=step.deliverable_id,
                    rationale=step.rationale,
                )
            )
        expanded_group_ids[step.id] = sub_ids

    return expanded


def _auto_insert_verify_speech(steps: list[PlanStep]) -> list[PlanStep]:
    """Guarantee every synthesize_speech step has a corresponding verify_speech step."""
    verified_audio_refs: set[str] = set()
    for s in steps:
        if s.tool == "verify_speech":
            ref = s.args.get("audio_path")
            if isinstance(ref, str):
                verified_audio_refs.add(ref)

    out: list[PlanStep] = []
    for s in steps:
        out.append(s)
        if s.tool == "synthesize_speech":
            expected_ref = f"${s.id}.audio_path"
            if expected_ref not in verified_audio_refs:
                out.append(
                    PlanStep(
                        id=f"verify_{s.id}",
                        tool="verify_speech",
                        args={
                            "audio_path": expected_ref,
                            "language": s.args.get("language", "en"),
                            "chunk_manifest_path": f"${s.id}.chunk_manifest_path",
                        },
                        deliverable_id=s.deliverable_id,
                        rationale="Auto-inserted mandatory speech back-transcription verification.",
                    )
                )
    return out


def _estimate_step_cost(step: PlanStep, file_facts_by_path: dict[str, FileFact]) -> float:
    path_arg = step.args.get("path")
    fact = file_facts_by_path.get(str(Path(path_arg).resolve())) if isinstance(path_arg, str) and not path_arg.startswith("$") else None
    if step.tool == "ocr_document":
        pages = fact.page_count if fact and fact.page_count else 1
        return cost_ocr(pages)
    if step.tool == "transcribe_audio":
        dur = fact.duration_s if fact and fact.duration_s else 30.0
        return cost_transcribe(dur)
    if step.tool == "detect_language" and fact and fact.kind in ("audio", "video"):
        return cost_transcribe(min(10.0, fact.duration_s or 10.0))
    if step.tool == "translate_text":
        chars = fact.char_count if fact and fact.char_count else 1000
        return cost_translate(max(100, chars // 2))
    if step.tool == "synthesize_speech":
        chars = len(step.args.get("text", "")) if isinstance(step.args.get("text"), str) else (fact.char_count if fact and fact.char_count else 1200)
        return cost_speak(chars)
    if step.tool == "verify_speech":
        return cost_transcribe(60.0)
    return 0.0


def compile_plan(
    raw_plan: RawPlan,
    *,
    task_spec: TaskSpec,
    file_facts: list[FileFact],
    probe_facts: list[ProbeFact],
    tool_catalog: list[dict[str, Any]],
    allowed_inputs: set[Path],
    prior_step_ids: set[str] | None = None,
) -> CompiledPlan:
    """Compile and statically type-check a flat RawPlan into parallel execution waves.

    Enforces:
      - Valid tool names & argument keys/required fields against MCP list_tools schemas
      - Valid $step.field references with no forward references or cycles
      - Task-scope lock: input files inside --files, output filenames plain basenames, languages inside TaskSpec (+ 'en')
      - Ground rule: planner never injects raw Indic text literals into tool arguments
      - Auto-insertion of verify_speech for every synthesize_speech step
      - Dependency DAG -> topological wave partitioning
    """
    errors: list[str] = []
    schema_by_tool = {t["name"]: t.get("input_schema") or {} for t in tool_catalog}
    known_prior = set(prior_step_ids or set())

    # Allowed target languages: those in TaskSpec + English pivot + detected probe/file languages.
    allowed_langs: set[str] = {"en", "auto"}
    if task_spec.source_language:
        allowed_langs.add(task_spec.source_language)
    for d in task_spec.deliverables:
        if d.language:
            allowed_langs.add(d.language)
        if d.constraints.filter_language:
            allowed_langs.add(d.constraints.filter_language)
    for pf in probe_facts:
        if pf.language:
            allowed_langs.add(pf.language)
    for ff in file_facts:
        if ff.detected_language_hint:
            allowed_langs.add(ff.detected_language_hint)

    steps = _expand_foreach(raw_plan.steps, file_facts, probe_facts)
    steps = _auto_insert_verify_speech(steps)

    seen_ids: set[str] = set()
    file_facts_by_path = {str(Path(f.path).resolve()): f for f in file_facts}
    compiled_map: dict[str, CompiledStep] = {}

    for step in steps:
        if not step.id or not re.fullmatch(r"[A-Za-z0-9_]+", step.id):
            errors.append(f"Invalid step id '{step.id}'. Use alphanumeric/underscore identifiers.")
            continue
        if step.id in seen_ids or step.id in known_prior:
            errors.append(f"Duplicate step id '{step.id}'.")
            continue
        seen_ids.add(step.id)

        if step.tool not in schema_by_tool:
            errors.append(f"Step '{step.id}': unknown tool '{step.tool}'. Available: {sorted(schema_by_tool.keys())}")
            continue

        schema = schema_by_tool[step.tool]
        props = schema.get("properties") or {}
        required = schema.get("required") or []

        # Validate required args and unknown arg names.
        for req_key in required:
            if req_key not in step.args or step.args[req_key] is None:
                errors.append(f"Step '{step.id}' ({step.tool}): missing required argument '{req_key}'.")
        for arg_k in step.args:
            if props and arg_k not in props:
                errors.append(
                    f"Step '{step.id}' ({step.tool}): unknown argument '{arg_k}'. Allowed: {sorted(props.keys())}"
                )

        # Ground rule: P-LLM must NEVER author Indic text literals in tool args.
        for arg_k, arg_v in step.args.items():
            if isinstance(arg_v, str) and not arg_v.startswith("$") and contains_indic_script(arg_v):
                errors.append(
                    f"Step '{step.id}' ({step.tool}): argument '{arg_k}' contains planner-authored Indic script text, "
                    "which is forbidden. Reference a Bodhan output or input file path instead."
                )

        # Language capability & scope lock.
        for lang_key in ("target_language", "language"):
            if lang_key in step.args and isinstance(step.args[lang_key], str):
                raw_lang = step.args[lang_key]
                if raw_lang.lower() == "auto" and step.tool == "transcribe_audio":
                    continue
                iso, known = resolve_language(raw_lang)
                if not iso or not known:
                    errors.append(f"Step '{step.id}' ({step.tool}): unsupported language '{raw_lang}'.")
                    continue
                model = TOOL_MODEL_MAP.get(step.tool)
                if model and not is_language_supported(model, iso):
                    errors.append(f"Step '{step.id}' ({step.tool}): language '{iso}' not supported by {model}.")
                # Scope-lock output target languages strictly to TaskSpec (+ 'en' pivot).
                # Input-audio language hints on transcribe_audio/verify_speech only need model support.
                if step.tool not in ("transcribe_audio", "verify_speech"):
                    if iso not in allowed_langs and iso.split("-")[0] not in allowed_langs:
                        errors.append(
                            f"Step '{step.id}' ({step.tool}): language '{iso}' is outside TaskSpec scope {sorted(allowed_langs)}."
                        )
                # Normalize language argument to canonical ISO code!
                step.args[lang_key] = iso

        # Static validation of synthesize_speech style and voice against vendored Bodhan rules.
        if step.tool == "synthesize_speech":
            raw_style = step.args.get("style")
            if isinstance(raw_style, str) and raw_style.strip():
                allowed_styles = supported_styles()
                lower_styles = {s.lower(): s for s in allowed_styles}
                if raw_style.strip().lower() not in lower_styles:
                    errors.append(
                        f"Step '{step.id}' (synthesize_speech): unsupported style '{raw_style}'. "
                        f"Omit 'style' for default narration, or choose one of: {allowed_styles}."
                    )
                else:
                    step.args["style"] = lower_styles[raw_style.strip().lower()]
            raw_voice = step.args.get("voice")
            lang_for_voice = step.args.get("language")
            if isinstance(raw_voice, str) and raw_voice.strip() and isinstance(lang_for_voice, str):
                _, v_err = select_voice(lang_for_voice, voice=raw_voice)
                if v_err:
                    errors.append(f"Step '{step.id}' (synthesize_speech): {v_err}")

        # Scope lock on file paths & output filenames.
        for arg_k, arg_v in step.args.items():
            if arg_k in PATH_ARG_KEYS and isinstance(arg_v, str) and not arg_v.startswith("$"):
                resolved = Path(arg_v).expanduser().resolve()
                if resolved not in allowed_inputs:
                    errors.append(
                        f"Step '{step.id}' ({step.tool}): path '{arg_v}' is outside allowed --files input scope."
                    )
            if arg_k == "filename" and isinstance(arg_v, str):
                if "/" in arg_v or "\\" in arg_v or ".." in arg_v or not arg_v.strip():
                    errors.append(
                        f"Step '{step.id}' ({step.tool}): filename '{arg_v}' must be a plain basename inside out/artifacts/."
                    )
            if arg_k == "output_path" and isinstance(arg_v, str):
                errors.append(
                    f"Step '{step.id}' ({step.tool}): do not pass arbitrary output_path; use publish_artifact with a plain filename."
                )

        # Dependency extraction from $step.field references.
        deps: list[str] = []
        for ref_step, _ref_field in _extract_refs(step.args):
            if ref_step == step.id:
                errors.append(f"Step '{step.id}': self-reference '${ref_step}'.")
            elif ref_step not in seen_ids and ref_step not in known_prior:
                # Could be defined later in the list; we'll double-check after loop for cycles / missing refs.
                deps.append(ref_step)
            elif ref_step in seen_ids:
                deps.append(ref_step)

        est_cost = _estimate_step_cost(step, file_facts_by_path)
        compiled_map[step.id] = CompiledStep(
            id=step.id,
            tool=step.tool,
            args=dict(step.args),
            depends_on=list(dict.fromkeys(deps)),
            deliverable_id=step.deliverable_id,
            estimated_cost_inr=round(est_cost, 6),
            rationale=step.rationale,
        )

    # Validate all references exist (either in this plan or prior steps) and refer to valid output fields.
    common_meta_fields = {"ok", "cost_inr", "cache_hit", "model"}
    for sid, cstep in compiled_map.items():
        for ref_step, ref_field in _extract_refs(cstep.args):
            if ref_step not in compiled_map and ref_step not in known_prior:
                errors.append(f"Step '{sid}': references unknown step '${ref_step}'.")
            elif ref_step in compiled_map:
                ref_tool = compiled_map[ref_step].tool
                allowed_fields = TOOL_OUTPUT_FIELDS.get(ref_tool)
                if allowed_fields and ref_field not in allowed_fields and ref_field not in common_meta_fields:
                    errors.append(
                        f"Step '{sid}': invalid field '${ref_step}.{ref_field}' for tool '{ref_tool}'. "
                        f"Available fields: {list(allowed_fields)}"
                    )

    if errors:
        raise PlanCompileError(errors)

    # Topological sort (Kahn's algorithm) into parallel execution waves.
    in_degree: dict[str, int] = {}
    dependents: dict[str, list[str]] = {sid: [] for sid in compiled_map}
    for sid, cstep in compiled_map.items():
        local_deps = [d for d in cstep.depends_on if d in compiled_map]
        in_degree[sid] = len(local_deps)
        for d in local_deps:
            dependents[d].append(sid)

    waves: list[list[CompiledStep]] = []
    ready = [sid for sid, deg in in_degree.items() if deg == 0]
    visited = 0

    while ready:
        wave = [compiled_map[sid] for sid in ready]
        waves.append(wave)
        visited += len(wave)
        next_ready: list[str] = []
        for sid in ready:
            for child in dependents[sid]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    next_ready.append(child)
        ready = next_ready

    if visited != len(compiled_map):
        raise PlanCompileError(["Plan contains a dependency cycle among steps."])

    ceiling = round(sum(s.estimated_cost_inr for s in compiled_map.values()), 6)
    return CompiledPlan(
        waves=waves,
        steps_by_id=compiled_map,
        estimated_cost_ceiling_inr=ceiling,
        final_answer_template=raw_plan.final_answer_template,
        assumptions=raw_plan.assumptions,
    )
