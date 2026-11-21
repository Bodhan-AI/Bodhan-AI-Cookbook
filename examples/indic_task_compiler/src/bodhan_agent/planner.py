"""Privileged Planner (P-LLM) for Epoch A (TaskSpec), Epoch B (Plan), and Epoch C (Targeted Re-plan).

HARD CaMeL INVARIANT: P-LLM ONLY ever receives:
  - the user's --task string
  - FileFacts (metadata: paths, kinds, durations, page counts, script histograms)
  - ProbeFacts (typed language ISO enums, confidence numbers, ambiguous booleans)
  - MCP tool schemas and structured error/verification metadata
P-LLM NEVER receives raw text from input files, OCR outputs, transcripts, or translations.
"""

from __future__ import annotations

import json
from typing import Any

from bodhan_mcp.rules import PRICING_INR, resolve_language, supported_languages
from bodhan_mcp.schemas import FileFact

from bodhan_agent.llm import LLMProtocol
from bodhan_agent.models import (
    DeliverableVerification,
    ProbeFact,
    RawPlan,
    TaskSpec,
)

EPOCH_A_SYSTEM = """You are the Privileged Planner (Epoch A) for the Bodhan Indic Task Agent.
Your job is to convert the user's task and input file metadata into a structured JSON TaskSpec.

STRICT RULES:
1. You see ONLY file metadata (never file contents). Do not guess or fabricate content.
2. All ASR, translation, TTS, and OCR go exclusively to Bodhan models (English + 22+ Indian languages).
3. If the task asks for something impossible (e.g. an unsupported language like French/Spanish/Japanese, voice cloning, video editing, or missing required files), set "infeasible_reason" to a clear, concise explanation.
4. If audio/video clips have an unstated language or the task asks to filter clips by language (e.g. "Which clips are in Telugu?"), add a probe request {"kind": "detect_language", "path": "<exact path>"} in "needs_probe" for each such clip.
5. For duration targets like "two-minute audio summary", set constraints.duration_s = 120 and constraints.target_words = 170 (Indic narration speaks at roughly 85 English-source words per minute).
6. Make reasonable non-interactive assumptions when wording is ambiguous and list them in "assumptions".
7. Never write Indian-language text. Keep language names as English names or ISO codes in "language_raw" (code maps them to ISO).

Return ONLY a JSON object with this exact schema:
{
  "summary": "short English summary of the goal",
  "deliverables": [
    {
      "id": "d1",
      "kind": "audio | subtitles | markdown | text | answer",
      "language_raw": "Tamil | English | Marathi | French | null",
      "output_filename": "summary_ta.mp3 (basename only, no directories)",
      "description": "what this deliverable contains",
      "constraints": {
        "duration_s": null,
        "target_words": null,
        "voice": null,
        "voice_gender": "female | male | null",
        "style": null,
        "keep_tables": false,
        "format": "mp3 | wav | srt | vtt | md | txt | null",
        "script": "native | roman | codemix",
        "filter_language": null
      }
    }
  ],
  "source_language_raw": "Hindi | unknown | null",
  "inputs_used": ["exact file path from FileFacts"],
  "assumptions": ["list any assumptions made"],
  "needs_probe": [{"kind": "detect_language", "path": "exact file path"}],
  "infeasible_reason": null
}"""

EPOCH_B_SYSTEM = """You are the Privileged Planner (Epoch B) for the Bodhan Indic Task Agent.
Emit a flat ordered list of tool steps (JSON) that fulfills every deliverable in the TaskSpec.

STRICT ARCHITECTURAL RULES (enforced by the deterministic Plan Compiler):
1. NEVER write Indian-language text in any argument. All Indic text MUST flow from Bodhan tools via $step_id.field handles!
2. Pass file paths and $step_id.field handles between steps — never inline text from files.
3. For summarization or English content extraction on Indic documents:
   - First OCR/transcribe -> then translate_text(path="$s1.text_path", target_language="en") -> then q_summarize_en(text_ref="$s2.text_path", target_words=N) -> then translate_text(path="$s3.text_path", target_language="<target>") -> then synthesize_speech(...) -> then verify_speech(...).
   - q_summarize_en ONLY accepts English text!
4. Whenever you call synthesize_speech, you MUST follow it with verify_speech using its audio_path, language, and chunk_manifest_path.
5. Every file deliverable MUST end with a publish_artifact step:
   {"id": "pub1", "tool": "publish_artifact", "args": {"deliverable_id": "d1", "source_ref": "$s5.audio_path", "filename": "tamil_summary.mp3"}}
   Filename MUST be a plain basename (no slashes, no '..').
6. For multi-file filtering tasks (e.g. "Which clips are Telugu? Translate those to English"):
   - Inspect ProbeFacts carefully! Only emit transcribe_audio + translate_text steps for the matching clips (or use foreach over probe_facts with filter).
   - You can combine multiple text artifacts with merge_text_artifacts(inputs=["$t1.text_path", ...], labels=["clip1.wav", ...], filename="combined.md").
7. Prefer minimal, non-redundant steps. Independent steps (with no $ref between them) automatically run in parallel waves.
8. Only use tools listed in the Tool Catalog below, plus Q-LLM pseudo-tools (q_summarize_en, q_classify, q_extract_number) and built-ins (publish_artifact, merge_text_artifacts).

Return ONLY a JSON object:
{
  "steps": [
    {"id": "s1", "tool": "...", "args": {...}, "deliverable_id": "d1", "rationale": "why"}
  ],
  "final_answer_template": "Concise English summary of what was accomplished (NO Indic text, NO fabricated content).",
  "assumptions": ["any additional assumptions"]
}"""

EPOCH_C_SYSTEM = """You are the Privileged Planner (Epoch C — Targeted Recovery).
One or more deliverable verification contracts failed. You see ONLY structured failure metadata (never raw content).
Emit a SMALL repair step list (only the steps needed to fix the failed deliverable(s), reusing existing handles like $s1.text_path wherever valid!).

Recovery heuristics:
- If audio duration was too long/short (>20% off target_s), re-run q_summarize_en with adjusted target_words, re-translate, re-synthesize, re-verify, and re-publish.
- If verify_speech failed (high CER or repetition), re-run synthesize_speech (with style omitted or alternative valid voice if helpful), re-verify, and re-publish.
- If translation coverage/tables failed, re-run translate_text and re-publish.

Return ONLY a JSON object with the same {"steps": [...], "final_answer_template": "...", "assumptions": [...]} schema.
Use fresh step IDs prefixed with "r1_" or "r2_" so they never collide with prior step IDs."""


def _normalize_task_spec_languages(spec: TaskSpec) -> TaskSpec:
    """Resolve human language names -> ISO codes deterministically via rules.py."""
    if spec.source_language_raw and spec.source_language_raw.lower() not in ("auto", "unknown", "none", ""):
        iso, known = resolve_language(spec.source_language_raw)
        if known:
            spec.source_language = iso
    for d in spec.deliverables:
        if d.language_raw:
            iso, known = resolve_language(d.language_raw)
            d.language = iso
            d.language_supported = known
        if d.constraints.filter_language:
            f_iso, f_known = resolve_language(d.constraints.filter_language)
            if f_known and f_iso:
                d.constraints.filter_language = f_iso
    return spec


async def run_epoch_a(
    llm: LLMProtocol,
    *,
    task: str,
    file_facts: list[FileFact],
    max_cost_inr: float,
) -> TaskSpec:
    """Epoch A: Parse task + FileFacts (metadata only) into a typed TaskSpec."""
    facts_payload = [f.model_dump(exclude_none=True) for f in file_facts]
    user_prompt = json.dumps(
        {
            "task": task,
            "max_cost_inr": max_cost_inr,
            "file_facts": facts_payload,
            "supported_languages_by_model": {
                "indic-transcribe": supported_languages("indic-transcribe"),
                "indic-translate": supported_languages("indic-translate"),
                "indic-speak": supported_languages("indic-speak"),
            },
            "pricing_inr": PRICING_INR,
        },
        ensure_ascii=False,
        indent=2,
    )
    raw = await llm.complete_json(
        role_tag="p_llm_epoch_a",
        system_prompt=EPOCH_A_SYSTEM,
        user_prompt=user_prompt,
    )
    spec = TaskSpec.model_validate(raw)
    return _normalize_task_spec_languages(spec)


def _compact_tool_catalog(tool_catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Produce a compact parameter & return-field summary for P-LLM prompts while keeping full schemas on the MCP server."""
    from bodhan_agent.compiler import TOOL_OUTPUT_FIELDS

    compact: list[dict[str, Any]] = []
    for t in tool_catalog:
        schema = t.get("input_schema") or {}
        props = schema.get("properties") or {}
        req = set(schema.get("required") or [])
        args_summary = {
            k: ("required" if k in req else "optional")
            for k in props
        }
        desc = str(t.get("description") or "").split(".")[0].strip()
        entry: dict[str, Any] = {"name": t["name"], "summary": desc, "args": args_summary}
        if t["name"] in TOOL_OUTPUT_FIELDS:
            entry["returns"] = list(TOOL_OUTPUT_FIELDS[t["name"]])
        compact.append(entry)
    return compact


async def run_epoch_b(
    llm: LLMProtocol,
    *,
    task: str,
    task_spec: TaskSpec,
    file_facts: list[FileFact],
    probe_facts: list[ProbeFact],
    tool_catalog: list[dict[str, Any]],
    max_cost_inr: float,
    compile_errors: list[str] | None = None,
) -> RawPlan:
    """Epoch B: Emit flat step list referencing FileFacts, ProbeFacts, and MCP tool schemas."""
    payload: dict[str, Any] = {
        "task": task,
        "max_cost_inr": max_cost_inr,
        "task_spec": task_spec.model_dump(exclude_none=True),
        "file_facts": [f.model_dump(exclude_none=True) for f in file_facts],
        "probe_facts": [p.model_dump(exclude_none=True) for p in probe_facts],
        "tool_catalog": _compact_tool_catalog(tool_catalog),
    }
    if compile_errors:
        payload["previous_compile_errors_to_fix"] = compile_errors

    raw = await llm.complete_json(
        role_tag="p_llm_epoch_b_repair" if compile_errors else "p_llm_epoch_b",
        system_prompt=EPOCH_B_SYSTEM,
        user_prompt=json.dumps(payload, ensure_ascii=False),
    )
    return RawPlan.model_validate(raw)


async def run_epoch_c(
    llm: LLMProtocol,
    *,
    task: str,
    task_spec: TaskSpec,
    existing_step_handles: dict[str, list[str]],
    failed_verifications: list[DeliverableVerification],
    tool_catalog: list[dict[str, Any]],
    replan_round: int,
) -> RawPlan:
    """Epoch C: Targeted subgraph re-plan using ONLY verification metadata (no untrusted content)."""
    payload = {
        "task": task,
        "replan_round": replan_round,
        "task_spec": task_spec.model_dump(exclude_none=True),
        "available_step_handles": existing_step_handles,
        "failed_verifications": [v.model_dump() for v in failed_verifications],
        "tool_catalog": _compact_tool_catalog(tool_catalog),
    }
    raw = await llm.complete_json(
        role_tag=f"p_llm_epoch_c_round_{replan_round}",
        system_prompt=EPOCH_C_SYSTEM,
        user_prompt=json.dumps(payload, ensure_ascii=False),
    )
    return RawPlan.model_validate(raw)
