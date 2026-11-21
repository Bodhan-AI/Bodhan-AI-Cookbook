from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from bodhan_agent import guards, prompts
from bodhan_agent.budget import DOCUMENT_KINDS, MEDIA_KINDS, BudgetManager, minimum_cost, quote_tool
from bodhan_agent.config import RunSettings
from bodhan_agent.llm import LlmClient, LlmError
from bodhan_agent.loop import register_outputs
from bodhan_agent.mcp_session import McpSession
from bodhan_agent.schemas.ledger import Artifact, Ledger
from bodhan_agent.schemas.plan import Deliverable, Plan
from bodhan_agent.schemas.planner import GateResult, PlanOutcome
from bodhan_agent.schemas.preflight import PreflightResult
from bodhan_agent.schemas.report import StopReason
from bodhan_agent.schemas.trace import LlmTokens, TraceError
from bodhan_agent.trace import TraceWriter
from bodhan_agent.verify import apply_step, step_verdict
from bodhan_mcp.core.languages import SPEAK_LANGUAGES, TRANSCRIBE_LANGUAGES, TRANSLATE_TARGETS

MAX_PLAN_ATTEMPTS = 2
CAPABILITY_REASONS: dict[str, StopReason] = {
    "unsupported_language": "unsupported_language",
    "missing_input": "missing_input",
    "unsupported_input": "unsupported_input",
    "beyond_tools": "beyond_tools",
}
TOOL_INPUTS: dict[str, frozenset[str]] = {"transcribe_audio": MEDIA_KINDS, "ocr_document": DOCUMENT_KINDS}
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class PlannerError(Exception):
    def __init__(self, stop_reason: StopReason, message: str) -> None:
        super().__init__(message)
        self.stop_reason = stop_reason
        self.message = message


def _supported(code: str, table: dict[str, str]) -> bool:
    return code in table or code.split("-", 1)[0].lower() in table


def deliverable_language_problem(item: Deliverable) -> str | None:
    if item.language is None or item.kind == "answer":
        return None
    language = item.language
    if item.kind == "audio":
        supported = _supported(language, SPEAK_LANGUAGES)
        model = "Bodhan speech synthesis"
    elif item.kind == "subtitles":
        supported = _supported(language, TRANSLATE_TARGETS) or _supported(language, TRANSCRIBE_LANGUAGES)
        model = "Bodhan translation or transcription"
    else:
        supported = _supported(language, TRANSLATE_TARGETS)
        model = "Bodhan translation"
    return None if supported else f"{item.id} needs '{language}', which {model} does not support."


def extract_json(text: str) -> dict[str, Any]:
    cleaned = _FENCE_RE.sub("", text.strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("the reply contains no JSON object")
    value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("the reply is not a JSON object")
    return value


def parse_plan(text: str) -> Plan:
    try:
        data = extract_json(text)
    except ValueError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    found = guards.find_indic(data)
    if found:
        raise ValueError(f"the plan contains Indian-language text at {', '.join(found[:3])}; write English only")
    problem = guards.llm_text_problem(json.dumps(data, ensure_ascii=False))
    if problem is not None:
        raise ValueError(f"the plan {problem[1]}")
    try:
        return Plan.model_validate(data)
    except ValidationError as exc:
        errors = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'plan'}: {error['msg']}" for error in exc.errors()[:5]
        )
        raise ValueError(f"the plan does not match the schema: {errors}") from exc


def _add_tokens(total: LlmTokens, more: LlmTokens) -> LlmTokens:
    def add(left: int | None, right: int | None) -> int | None:
        return right if left is None else left if right is None else left + right

    return LlmTokens(prompt=add(total.prompt, more.prompt), completion=add(total.completion, more.completion))


class Planner:
    def __init__(
        self,
        settings: RunSettings,
        ledger: Ledger,
        llm: LlmClient,
        budget: BudgetManager,
        trace: TraceWriter,
        mcp: McpSession | None = None,
        deadline: float | None = None,
    ) -> None:
        self.settings = settings
        self.ledger = ledger
        self.llm = llm
        self.budget = budget
        self.trace = trace
        self.mcp = mcp
        self.deadline = deadline
        self.llm_turns = 0
        self.llm_tokens = LlmTokens()
        self.blocked = 0

    async def english_instruction(self, preflight: PreflightResult) -> tuple[str, str | None]:
        if not preflight.needs_translation or preflight.instruction_id is None:
            return preflight.task_for_llm, None
        original = self.ledger.get(preflight.instruction_id)
        if original is None:
            raise PlannerError("internal_error", "The original task file L0 is missing from the ledger.")
        if self.mcp is None:
            raise PlannerError("missing_input", "The task is in an Indian language and Bodhan is not available.")
        args = {"text_path": original.path, "target_language": "en", "overwrite": False}
        quote = quote_tool("translate_text", args, original)
        reservation = await self.budget.reserve(quote)
        if reservation is None:
            raise PlannerError(
                "budget_insufficient",
                f"Translating the task to English may cost Rs {quote.amount_inr:.2f}, more than the budget allows.",
            )
        result = await self.mcp.call("translate_text", args, deadline=self.deadline)
        usage = result.usage or {}
        charged = usage.get("estimated_cost_inr")
        cost = await self.budget.settle(
            reservation,
            float(charged) if isinstance(charged, int | float) else None,
            uncertain=bool(result.error and result.error.uncertain) or bool(usage.get("billing_uncertain")),
            possibly_billed_failed_attempts=int(usage.get("possibly_billed_failed_attempts") or 0),
            cache_hits=int(usage.get("cache_hits") or 0),
        )
        error = result.error
        ids: list[str] = []
        if result.ok and result.data is not None:
            ids = register_outputs(self.settings, self.ledger, "translate_text", result.data, original)
            apply_step(self.ledger, ids, step_verdict("translate_text", result.data))
        await self.trace.emit(
            "plan", "tool", tool="translate_text", reason="Translate the task into English before planning.",
            args=args, artifacts=ids, latency_ms=result.latency_ms, cost_inr=cost,
            error=None if error is None else TraceError(code=error.code, message=error.message, hint=error.hint),
        )  # fmt: skip
        english = self.ledger.get(ids[0]) if ids else None
        if english is None or not english.usable:
            message = error.message if error is not None else "the translation was empty"
            raise PlannerError("planner_error", f"The task could not be translated to English: {message}")
        text = Path(english.path).read_text(encoding="utf-8-sig").strip()
        redacted, _ = guards.redact_text(text)
        literals = [item for item in preflight.literal_ids if item != "L0"]
        if literals:
            redacted += " Quoted text from the task is saved as " + ", ".join(literals) + "."
        return redacted, english.id

    async def plan(self, task_en: str, preflight: PreflightResult) -> Plan:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": prompts.planner_prompt()},
            {
                "role": "user",
                "content": prompts.plan_request(task_en, self.ledger, preflight.input_ids, preflight.literal_ids),
            },
        ]
        last_error = "no reply"
        for attempt in range(1, MAX_PLAN_ATTEMPTS + 1):
            try:
                reply = await self.llm.complete(messages, None, deadline=self.deadline)
            except LlmError as exc:
                raise PlannerError("llm_unavailable", f"The LLM could not be reached for planning: {exc}") from exc
            self.llm_turns += 1
            self.llm_tokens = _add_tokens(self.llm_tokens, reply.tokens)
            content = reply.content or ""
            try:
                plan = parse_plan(content)
            except ValueError as exc:
                last_error = str(exc)
                if guards.contains_indic(content) or "romanized" in last_error:
                    self.blocked += 1
                await self.trace.emit(
                    "plan", "llm", message=f"Plan attempt {attempt} rejected.", llm_tokens=reply.tokens,
                    latency_ms=reply.latency_ms, error=TraceError(code="invalid_plan", message=last_error),
                )  # fmt: skip
                safe, _ = guards.redact_text(content)
                messages.append({"role": "assistant", "content": safe or "(empty reply)"})
                messages.append({"role": "user", "content": prompts.plan_repair(last_error)})
                continue
            await self.trace.emit(
                "plan", "plan", message=f"Plan with {len(plan.deliverables)} deliverable(s).",
                llm_tokens=reply.tokens, latency_ms=reply.latency_ms,
                result_summary={"plan": plan.model_dump(mode="json", exclude_none=True)},
            )  # fmt: skip
            return plan
        raise PlannerError("planner_error", f"No usable plan after {MAX_PLAN_ATTEMPTS} attempts: {last_error}")

    def gate(self, plan: Plan, preflight: PreflightResult) -> GateResult:
        notes: list[str] = []
        language_problems = [
            problem for item in plan.deliverables if (problem := deliverable_language_problem(item)) is not None
        ]
        for item in plan.deliverables:
            if item.kind == "answer" and item.language and item.language.split("-", 1)[0] != "en":
                notes.append(f"{item.id} is answered in English; an Indian-language version needs a file deliverable.")
        inputs = [artifact for artifact_id in preflight.input_ids if (artifact := self.ledger.get(artifact_id))]
        usable = [artifact for artifact in inputs if artifact.usable]
        input_problems = self._input_problems(plan, usable, preflight)
        declared = plan.feasibility
        if declared.status == "infeasible":
            reason = declared.reason_code or "beyond_tools"
            confirmed = (
                reason in ("beyond_tools", "unsupported_input")
                or (reason == "unsupported_language" and language_problems)
                or (reason == "missing_input" and (input_problems or not plan.deliverables))
            )
            if confirmed or not plan.deliverables:
                details = {"unsupported_language": language_problems, "missing_input": input_problems}.get(reason, [])
                explanation = " ".join([declared.explanation or "The planner marked the task infeasible.", *details])
                return GateResult(feasible=False, stop_reason=CAPABILITY_REASONS[reason], explanation=explanation)
            notes.append(
                f"The planner judged the task infeasible ({reason}), but code checks found no such problem; "
                "the run continues."
            )
        if language_problems:
            return GateResult(
                feasible=False, stop_reason="unsupported_language", explanation=" ".join(language_problems)
            )
        if input_problems:
            return GateResult(feasible=False, stop_reason="missing_input", explanation=" ".join(input_problems))
        planned_tools = {step.tool for step in plan.steps if step.tool}
        cost = minimum_cost(usable, planned_tools)
        if cost.known_inr > self.budget.remaining_inr + 1e-9:
            return GateResult(
                feasible=False,
                stop_reason="budget_insufficient",
                explanation=(
                    f"The plan needs at least Rs {cost.known_inr:.2f} ({', '.join(cost.items)}), but only "
                    f"Rs {self.budget.remaining_inr:.2f} of the Rs {self.budget.limit_inr:.2f} budget is available."
                ),
                minimum_cost=cost,
            )
        if preflight.missing_tools:
            notes.append(f"System tools not found: {', '.join(preflight.missing_tools)}; media steps may fail.")
        return GateResult(feasible=True, assumptions=notes, minimum_cost=cost)

    def _input_problems(self, plan: Plan, usable: list[Artifact], preflight: PreflightResult) -> list[str]:
        problems = []
        for step in plan.steps:
            kinds = TOOL_INPUTS.get(step.tool or "")
            if kinds is not None and not any(artifact.kind in kinds for artifact in usable):
                wanted = "audio or video with sound" if step.tool == "transcribe_audio" else "a PDF or image"
                problems.append(f"Step {step.id} needs {wanted}, but no usable input of that kind was given.")
        missing = [issue for issue in preflight.issues if issue.code == "missing_input"]
        if missing and not usable:
            problems.append("Input files are missing: " + ", ".join(issue.path for issue in missing[:5]) + ".")
        return list(dict.fromkeys(problems))

    async def run(self, preflight: PreflightResult) -> PlanOutcome:
        task_en, instruction_id = await self.english_instruction(preflight)
        plan = await self.plan(task_en, preflight)
        gate = self.gate(plan, preflight)
        if gate.assumptions:
            plan.assumptions = [*plan.assumptions, *gate.assumptions]
        await self.trace.emit(
            "gate", "gate", message="feasible" if gate.feasible else f"not feasible: {gate.stop_reason}",
            result_summary={
                "feasible": gate.feasible, "stop_reason": gate.stop_reason, "explanation": gate.explanation,
                "minimum_cost_inr": gate.minimum_cost.known_inr if gate.minimum_cost else None,
            },
        )  # fmt: skip
        return PlanOutcome(
            task_en=task_en,
            plan=plan,
            gate=gate,
            instruction_en_id=instruction_id,
            llm_turns=self.llm_turns,
            llm_tokens=self.llm_tokens,
            blocked_llm_outputs=self.blocked,
        )
