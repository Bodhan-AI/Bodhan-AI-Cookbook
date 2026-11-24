"""Plan (LLM → JSON, validated) and feasibility (code: languages, inputs, cost lower bound)."""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from bodhan_core import langs
from bodhan_core.errors import ToolError
from bodhan_core.ledger import cost_ocr, cost_speak, cost_transcribe

from .llm import LLM
from .prompts import PLAN_SYSTEM

SPEECH_CHARS_PER_S = 14.0  # rough Indic speech rate, for cost lower bounds only

# Widely requested languages that no Bodhan model supports. Used as a backstop when the task names one as a target.
FOREIGN = ["french", "spanish", "german", "italian", "portuguese", "russian", "chinese", "mandarin", "cantonese",
           "japanese", "korean", "arabic", "persian", "farsi", "turkish", "dutch", "swedish", "norwegian", "danish",
           "finnish", "greek", "hebrew", "thai", "vietnamese", "indonesian", "malay", "swahili", "polish",
           "ukrainian", "czech", "hungarian", "romanian", "sinhala", "sinhalese", "tibetan", "burmese", "khmer",
           "tagalog", "filipino"]
_TARGET_RE = re.compile(r"\b(?:into|to|in)\s+(" + "|".join(FOREIGN) + r")\b", re.I)


class Deliverable(BaseModel):
    kind: Literal["audio", "text", "markdown", "subtitles", "answer"] = "answer"
    language: str = "en"
    description: str = ""
    target_duration_s: float | None = None
    voice_gender: Literal["female", "male"] | None = None

    @field_validator("voice_gender", mode="before")
    @classmethod
    def _null(cls, v):
        return None if v in ("null", "", "none", None) else v

    @field_validator("kind", mode="before")
    @classmethod
    def _kind(cls, v):
        v = str(v or "answer").lower()
        aliases = {"mp3": "audio", "speech": "audio", "srt": "subtitles", "md": "markdown", "list": "answer",
                   "summary": "text", "document": "markdown", "translation": "text", "transcript": "text"}
        return aliases.get(v, v if v in {"audio", "text", "markdown", "subtitles", "answer"} else "answer")


class Plan(BaseModel):
    understanding: str = ""
    deliverables: list[Deliverable] = Field(default_factory=list)
    inputs_used: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    feasible: bool = True
    infeasible_reason: str | None = None


async def make_plan(llm: LLM, task: str, manifest: list[dict], missing: list[str], trace) -> Plan:
    user = json.dumps({"task": task, "manifest": manifest, "missing_inputs": missing}, ensure_ascii=False)
    messages = [{"role": "system", "content": PLAN_SYSTEM}, {"role": "user", "content": user}]
    for attempt in (1, 2):
        data, reply = await llm.json(messages, max_tokens=1500)
        error = None
        if data is None:
            error = "Your reply was not a JSON object."
        else:
            try:
                plan = Plan.model_validate(data)
                trace.log("plan", attempt=attempt, plan=plan.model_dump(), latency_ms=reply.latency_ms,
                          llm_tokens=reply.usage, llm_wait_s=reply.waited_s)
                return plan
            except ValidationError as e:
                error = f"The JSON did not match the schema: {e.errors()[:3]}"
        trace.log("plan_invalid", attempt=attempt, error=error, raw=reply.content[:500])
        messages += [{"role": "assistant", "content": reply.content or ""},
                     {"role": "user", "content": f"{error} Reply with ONLY the JSON object described."}]
    # Fallback plan: let the act loop work it out, and say so.
    plan = Plan(understanding=task, steps=["Work out the steps from the task"],
                assumptions=["The planner did not return a valid plan; the agent planned step by step instead."])
    trace.log("plan", attempt=3, plan=plan.model_dump(), fallback=True)
    return plan


def check_feasibility(task: str, plan: Plan, manifest: list[dict], missing: list[str],
                      max_cost_inr: float) -> tuple[str | None, str | None, list[str]]:
    """Return (status, reason, notes). status is None when feasible, else "impossible" / "budget_exhausted"."""
    notes: list[str] = []
    m = _TARGET_RE.search(task)
    if m:
        name = m.group(1).title()
        return "impossible", (f"{name} is not supported by any Bodhan model, so the task cannot be done as asked. "
                              f"Supported languages: {_supported_list()}."), notes

    for d in plan.deliverables:
        try:
            code = langs.normalize_language(d.language)
        except ToolError:
            return "impossible", (f"The requested output language '{d.language}' is not supported by Bodhan. "
                                  f"Supported languages: {_supported_list()}."), notes
        model = "indic-speak" if d.kind == "audio" else "indic-translate"
        if code != "en" or d.kind == "audio":
            try:
                langs.require_supported(model, code)
            except ToolError as e:
                return "impossible", f"{e.info.message} {e.info.hint or ''}".strip(), notes
        d.language = code

    if not plan.feasible:
        return "impossible", plan.infeasible_reason or "The planner judged the task impossible with the available tools.", notes

    if not manifest:
        why = f"input files not found: {', '.join(missing)}" if missing else "no input files were provided"
        return "impossible", f"Nothing to work on: {why}.", notes
    if missing:
        notes.append(f"Some input paths were missing and were ignored: {', '.join(missing)}")

    lower = cost_lower_bound(plan, manifest)
    if lower > max_cost_inr:
        work = "reading the inputs" + (" and generating the requested audio"
                                       if any(d.kind == "audio" for d in plan.deliverables) else "")
        return "budget_exhausted", (f"The task needs at least ₹{lower:.2f} of Bodhan calls ({work}), above the budget "
                                    f"of ₹{max_cost_inr:.2f}. Nothing was spent. "
                                    f"Rerun with --max-cost-inr {suggest_budget(lower)} or more."), notes
    notes.append(f"Estimated minimum Bodhan cost: ₹{lower:.2f} (budget ₹{max_cost_inr:.2f}).")
    return None, None, notes


def suggest_budget(lower: float) -> str:
    """A rerun budget with 50% headroom, readable at any scale: 0.3, 2, 15."""
    need = lower * 1.5
    return f"{need:.0f}" if need >= 10 else f"{need:.1f}".rstrip("0").rstrip(".") if need >= 0.1 else f"{need:.2f}"


def cost_lower_bound(plan: Plan, manifest: list[dict]) -> float:
    used = set(plan.inputs_used) or {f["id"] for f in manifest}
    total = 0.0
    for f in manifest:
        if f["id"] not in used:
            continue
        if f.get("kind") in {"pdf", "image"}:
            total += cost_ocr(int(f.get("pages") or 1))
        elif f.get("kind") in {"audio", "video"}:
            total += cost_transcribe(float(f.get("duration_s") or 0))
    for d in plan.deliverables:
        if d.kind == "audio" and d.target_duration_s:
            total += cost_speak(int(d.target_duration_s * SPEECH_CHARS_PER_S))
    return round(total, 4)


def _supported_list() -> str:
    names = langs.language_names()
    return ", ".join(sorted({names[c].split(" (")[0] for c in langs.supported_languages("indic-translate")}))
