from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from time import monotonic
from typing import Any
from xml.sax.saxutils import unescape

from bodhan_agent import guards, prompts
from bodhan_agent.budget import FREE_TOOLS, BudgetManager, quote_tool
from bodhan_agent.config import RunSettings
from bodhan_agent.llm import LlmClient, LlmError
from bodhan_agent.mcp_session import McpSession
from bodhan_agent.observe import Observer
from bodhan_agent.schemas.budget import Reservation
from bodhan_agent.schemas.ledger import ActionRecord, Artifact, ArtifactKind, Ledger
from bodhan_agent.schemas.llm import LlmReply, ToolCall
from bodhan_agent.schemas.loop import FinishProposal, LoopResult
from bodhan_agent.schemas.mcp import McpError, McpResult
from bodhan_agent.schemas.observation import Observation
from bodhan_agent.schemas.plan import Deliverable, Plan
from bodhan_agent.schemas.report import StopReason
from bodhan_agent.schemas.tools import FinishInput, LocalResult
from bodhan_agent.schemas.trace import LlmTokens, TraceError
from bodhan_agent.schemas.verify import Verdict
from bodhan_agent.tools.base import (
    LocalTool,
    LocalToolError,
    ToolContext,
    function_tool,
    known_file,
    register,
    split_meta,
)
from bodhan_agent.tools.detect_language import DetectLanguageTool
from bodhan_agent.tools.finish import FinishTool
from bodhan_agent.tools.make_subtitles import MakeSubtitlesTool
from bodhan_agent.tools.read import ReadTool
from bodhan_agent.tools.write import WriteTool
from bodhan_agent.trace import TraceWriter
from bodhan_agent.verify import apply_step, step_verdict, verify_deliverable
from bodhan_mcp.utils.script import dominant_script

MCP_TOOLS: tuple[str, ...] = ("transcribe_audio", "translate_text", "synthesize_speech", "ocr_document")
HIDDEN_MCP_FIELDS: tuple[str, ...] = ("overwrite",)
INPUT_FIELDS: dict[str, str] = {
    "transcribe_audio": "audio_path",
    "translate_text": "text_path",
    "synthesize_speech": "text_path",
    "ocr_document": "file_path",
    "make_subtitles": "segments_path",
}
INLINE_TEXT_TOOLS: frozenset[str] = frozenset({"translate_text", "synthesize_speech"})
OUTPUT_KINDS: dict[str, tuple[tuple[str, str], ...]] = {
    "transcribe_audio": (("output_path", "text"), ("segments_path", "segments")),
    "translate_text": (("output_path", "text"),),
    "ocr_document": (("output_path", "markdown"), ("blocks_path", "blocks")),
    "synthesize_speech": (("output_path", "audio"),),
}
TEXT_KINDS: frozenset[str] = frozenset({"text", "markdown", "segments", "blocks"})
FINALIZE_RESERVE_S = 10.0
MAX_BAD_REPLIES = 2
MAX_TEXT_FACT_BYTES = 20 * 1024 * 1024
BLOCKED_CONTENT = "[reply text removed by the controller: it was not English]"


def local_tools() -> dict[str, LocalTool]:
    tools: list[LocalTool] = [ReadTool(), WriteTool(), DetectLanguageTool(), MakeSubtitlesTool(), FinishTool()]
    return {tool.name: tool for tool in tools}


def _mcp_parameters(schema: dict[str, Any]) -> dict[str, Any]:
    parameters = copy.deepcopy(schema)
    properties = parameters.get("properties", {})
    for name in HIDDEN_MCP_FIELDS:
        properties.pop(name, None)
    parameters["required"] = [name for name in parameters.get("required", []) if name not in HIDDEN_MCP_FIELDS]
    return parameters


def _unescape_paths(args: dict[str, Any]) -> dict[str, Any]:
    return {
        key: unescape(value) if key in guards.PATH_FIELDS and isinstance(value, str) else value
        for key, value in args.items()
    }


def _base(code: str) -> str:
    return code.split("-", 1)[0].lower()


def _add_tokens(total: LlmTokens, more: LlmTokens) -> LlmTokens:
    def add(left: int | None, right: int | None) -> int | None:
        return right if left is None else left if right is None else left + right

    return LlmTokens(prompt=add(total.prompt, more.prompt), completion=add(total.completion, more.completion))


def text_facts(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > MAX_TEXT_FACT_BYTES:
            return {}
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return {}
    return {"chars": len(text), "script": dominant_script(text), "indic_text": guards.contains_indic(text)}


def language_facts(tool: str, data: dict[str, Any]) -> dict[str, Any]:
    if tool == "translate_text":
        return {"language": data.get("target_language"), "language_source": "requested"}
    if tool == "synthesize_speech":
        return {"language": data.get("language"), "language_source": "requested"}
    language = data.get("language")
    source = data.get("language_source")
    if source not in ("requested", "detected", "undetermined"):
        source = "undetermined" if language is None else "detected"
    return {"language": language, "language_source": source}


def register_outputs(
    settings: RunSettings, ledger: Ledger, tool: str, data: dict[str, Any], source: Artifact | None
) -> list[str]:
    ids: list[str] = []
    ctx = ToolContext(settings=settings, ledger=ledger)
    for key, default_kind in OUTPUT_KINDS.get(tool, ()):
        raw = data.get(key)
        if not isinstance(raw, str) or not raw:
            continue
        path = Path(raw).resolve()
        if not path.is_file():
            raise ValueError(f"{key} {raw} does not exist")
        existing = ledger.by_path(path)
        if existing is not None:
            ids.append(existing.id)
            continue
        kind: ArtifactKind = default_kind  # type: ignore[assignment]
        if tool == "translate_text" and path.suffix.lower() == ".md":
            kind = "markdown"
        facts = text_facts(path) if kind in TEXT_KINDS else {}
        facts.update(language_facts(tool, data))
        if tool in ("transcribe_audio", "synthesize_speech") and isinstance(data.get("duration_s"), int | float):
            facts["duration_s"] = float(data["duration_s"])
        if tool == "ocr_document" and isinstance(data.get("pages_read"), list):
            facts["pages"] = len(data["pages_read"])
        sources = [source.id] if source is not None else []
        artifact = register(ctx, path, kind, "bodhan", sources=sources, tool=tool, **facts)
        ids.append(artifact.id)
    return ids


@dataclass
class PendingCall:
    call: ToolCall
    args: dict[str, Any]
    reason: str | None
    plan_step: str | None
    source: Artifact | None
    digest: str
    reservation: Reservation | None = None
    uncertain_retry: bool = False


@dataclass
class CallOutcome:
    mcp: McpResult | None = None
    local: LocalResult | None = None
    error: McpError | None = None
    latency_ms: float = 0.0


@dataclass
class Batch:
    assistant: dict[str, Any]
    results: list[tuple[str, Observation]] = field(default_factory=list)


class Controller:
    def __init__(
        self,
        settings: RunSettings,
        ledger: Ledger,
        plan: Plan,
        llm: LlmClient,
        budget: BudgetManager,
        trace: TraceWriter,
        observer: Observer,
        mcp: McpSession | None = None,
        input_ids: list[str] | None = None,
        literal_ids: list[str] | None = None,
        started: float | None = None,
    ) -> None:
        self.settings = settings
        self.ledger = ledger
        self.plan = plan
        self.llm = llm
        self.budget = budget
        self.trace = trace
        self.observer = observer
        self.mcp = mcp
        self.input_ids = input_ids or []
        self.literal_ids = literal_ids or []
        start = monotonic() if started is None else started
        self.deadline = start + settings.deadline_s - FINALIZE_RESERVE_S
        self.local = local_tools()
        self.ctx = ToolContext(
            settings=settings, ledger=ledger, mcp=mcp, budget=budget, deadline=self.deadline, plan=plan
        )
        self.history: list[Batch | str] = []
        self.issues: list[str] = []
        self.result = LoopResult(stop_reason="finished")
        self.no_tool_nudged = False
        self.bad_replies = 0
        self.budget_blocked = False

    def tool_definitions(self) -> list[dict[str, Any]]:
        definitions = []
        if self.mcp is not None:
            for spec in self.mcp.tools:
                if spec.name in MCP_TOOLS:
                    definitions.append(function_tool(spec.name, spec.description, _mcp_parameters(spec.input_schema)))
        definitions.extend(tool.definition() for tool in self.local.values())
        return definitions

    def time_left(self) -> float:
        return self.deadline - monotonic()

    def candidate(self, deliverable: Deliverable) -> Artifact | None:
        if deliverable.kind == "answer":
            return None
        for artifact in reversed(list(self.ledger.artifacts.values())):
            if not artifact.id.startswith("A") or artifact.kind != deliverable.kind or not artifact.usable:
                continue
            if deliverable.format and Path(artifact.path).suffix.lower().lstrip(".") != deliverable.format:
                continue
            if deliverable.language and artifact.language and _base(artifact.language) != _base(deliverable.language):
                continue
            language = deliverable.language or artifact.language
            if guards.deliverable_source_problem(self.ledger, artifact, language) is None:
                return artifact
        return None

    def deliverable_states(self) -> dict[str, str]:
        states = {}
        for item in self.plan.deliverables:
            if item.kind == "answer":
                states[item.id] = "give it in finish.answer_en"
                continue
            artifact = self.candidate(item)
            states[item.id] = "not started" if artifact is None else f"candidate {artifact.id} ({artifact.step_status})"
        return states

    def messages(self) -> list[dict[str, Any]]:
        board = prompts.status_board(
            self.ledger,
            self.plan,
            deliverables=self.deliverable_states(),
            steps_used=self.result.steps_used,
            steps_limit=self.settings.max_steps,
            spent_inr=self.budget.spent_inr,
            remaining_inr=self.budget.remaining_inr,
            limit_inr=self.budget.limit_inr,
            deadline_left_s=self.time_left(),
            issues=self.issues[-5:],
        )
        task = prompts.task_message(
            self.plan,
            self.ledger,
            self.input_ids,
            self.literal_ids,
            str(self.settings.work_dir),
            str(self.settings.artifacts_dir),
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": prompts.SYSTEM_PROMPT},
            {"role": "user", "content": task},
            {"role": "user", "content": board},
        ]
        batches = [index for index, item in enumerate(self.history) if isinstance(item, Batch)]
        latest = batches[-1] if batches else -1
        previous = batches[-2] if len(batches) > 1 else -1
        for index, item in enumerate(self.history):
            if isinstance(item, str):
                messages.append({"role": "user", "content": item})
                continue
            messages.append(item.assistant)
            for call_id, observation in item.results:
                full = index == latest or (index == previous and not observation.ok)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": observation.content if full else observation.short,
                    }
                )
        return messages

    async def run(self) -> LoopResult:
        tools = self.tool_definitions()
        while True:
            stop = self.limit_reached()
            if stop is not None:
                return await self.stop(stop)
            try:
                reply = await self.llm.complete(self.messages(), tools, deadline=self.deadline)
            except LlmError as error:
                stop_reason: StopReason = (
                    "deadline" if error.code == "llm_timeout" and self.time_left() <= 0 else "llm_unavailable"
                )
                return await self.stop(stop_reason, f"The LLM could not be reached: {error.message}")
            self.result.llm_turns += 1
            self.result.llm_tokens = _add_tokens(self.result.llm_tokens, reply.tokens)
            await self.trace.emit(
                "act", "llm", message=f"{len(reply.tool_calls)} tool call(s), finish_reason={reply.finish_reason}",
                llm_tokens=reply.tokens, latency_ms=reply.latency_ms,
            )  # fmt: skip
            content = await self.check_content(reply.content)
            if reply.finish_reason in ("length", "content_filter"):
                self.bad_replies += 1
                if self.bad_replies > MAX_BAD_REPLIES:
                    return await self.stop("llm_unavailable", "The LLM kept returning cut-off or filtered replies.")
                self.history.append(Batch(assistant={"role": "assistant", "content": content or "(reply cut off)"}))
                self.history.append(
                    prompts.TRUNCATED_NUDGE if reply.finish_reason == "length" else prompts.FILTERED_NUDGE
                )
                continue
            self.bad_replies = 0
            if not reply.tool_calls:
                if not self.no_tool_nudged:
                    self.no_tool_nudged = True
                    self.history.append(Batch(assistant={"role": "assistant", "content": content or ""}))
                    self.history.append(prompts.NO_TOOL_NUDGE)
                    continue
                proposal = FinishProposal(answer_en=content or "", deliverables=self.inferred(), implicit=True)
                done = await self.conclude(proposal)
                if done is not None:
                    return done
                continue
            proposal = await self.run_batch(reply, content)
            if proposal is not None:
                done = await self.conclude(proposal)
                if done is not None:
                    return done

    def limit_reached(self) -> StopReason | None:
        if self.time_left() <= 0:
            return "deadline"
        if self.result.steps_used >= self.settings.max_steps:
            return "step_limit"
        if self.result.llm_turns >= self.settings.llm_turn_limit:
            return "turn_limit"
        return None

    async def check_content(self, content: str | None) -> str | None:
        if not content or not content.strip():
            return content
        problem = guards.llm_text_problem(content)
        if problem is None:
            return content
        self.result.blocked_llm_outputs += 1
        await self.trace.emit(
            "act", "guard", message="Assistant text was not English and was removed.",
            error=TraceError(code=problem[0], message=problem[1]),
        )  # fmt: skip
        return BLOCKED_CONTENT

    async def run_batch(self, reply: LlmReply, content: str | None) -> FinishProposal | None:
        assistant = reply.assistant_message()
        assistant["content"] = content
        batch = Batch(assistant=assistant)
        self.history.append(batch)
        finish_calls = [call for call in reply.tool_calls if call.name == "finish"]
        others = [call for call in reply.tool_calls if call.name != "finish"]
        results: dict[str, Observation] = {}
        pending: list[PendingCall] = []
        for call in others:
            if len(pending) >= self.settings.max_parallel_calls:
                results[call.id] = await self.deny(
                    call, "not_executed", f"Only {self.settings.max_parallel_calls} calls run per turn.",
                    "Issue it again in the next turn.", retryable=True,
                )  # fmt: skip
                continue
            if self.result.steps_used + len(pending) >= self.settings.max_steps:
                results[call.id] = await self.deny(
                    call, "step_limit", "The step limit is reached; this call was not run.", None
                )
                continue
            prepared = await self.prepare(call)
            if isinstance(prepared, Observation):
                results[call.id] = prepared
            else:
                pending.append(prepared)
        outcomes = await asyncio.gather(*(self.execute(item) for item in pending))
        for item, outcome in zip(pending, outcomes, strict=True):
            results[item.call.id] = await self.complete(item, outcome)
        proposal = None
        for index, call in enumerate(finish_calls):
            if index > 0:
                results[call.id] = await self.deny(call, "duplicate_finish", "Only one finish call is used.", None)
                continue
            observation, proposal = await self.finish(call)
            results[call.id] = observation
        batch.results = [(call.id, results[call.id]) for call in reply.tool_calls]
        return proposal

    async def deny(
        self, call: ToolCall, code: str, message: str, hint: str | None, retryable: bool = False
    ) -> Observation:
        await self.trace.emit(
            "act", "guard", tool_call_id=call.id, tool=call.name, message=f"Call not run: {code}",
            error=TraceError(code=code, message=message, hint=hint, retryable=retryable),
        )  # fmt: skip
        observation = self.observer.denial(call.name, code, message, hint, retryable)
        self.issues.append(f"{call.name}: {code}")
        return observation

    def digest(self, tool: str, args: dict[str, Any]) -> str:
        canonical = {}
        for key, value in sorted(args.items()):
            artifact = self.ledger.by_path(value) if key in guards.PATH_FIELDS and isinstance(value, str) else None
            canonical[key] = f"sha256:{artifact.sha256}" if artifact is not None and artifact.sha256 else value
        payload = json.dumps({"tool": tool, "args": canonical}, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def prepare(self, call: ToolCall) -> PendingCall | Observation:
        if call.arguments is None:
            return await self.deny(
                call, "invalid_arguments", f"Arguments are not a JSON object: {call.parse_error}", "Send valid JSON."
            )
        if call.name not in self.local and (self.mcp is None or call.name not in MCP_TOOLS):
            known = ", ".join(definition["function"]["name"] for definition in self.tool_definitions())
            return await self.deny(call, "unknown_tool", f"There is no tool '{call.name}'.", f"Use one of: {known}.")
        arguments = _unescape_paths(call.arguments)
        known_paths = [artifact.path for artifact in self.ledger.artifacts.values()]
        found = guards.find_indic(arguments, known_paths)
        if found:
            self.result.blocked_llm_outputs += 1
            return await self.deny(
                call, "indic_text", f"Arguments contain Indian-language text at {', '.join(found[:3])}.",
                "Write English only; use translate_text for Indian-language output.",
            )  # fmt: skip
        args, reason, plan_step = split_meta(arguments)
        if reason is None:
            return await self.deny(call, "missing_reason", "Every tool call needs a short English 'reason'.", None)
        if call.name in INLINE_TEXT_TOOLS and isinstance(args.get("text"), str):
            problem = guards.llm_text_problem(args["text"])
            if problem is not None:
                self.result.blocked_llm_outputs += 1
                return await self.deny(call, problem[0], f"text {problem[1]}", None)
            if call.name == "synthesize_speech":
                speech = guards.inline_speech_problem(str(args.get("language", "")))
                if speech is not None:
                    return await self.deny(call, "inline_speech", speech, None)
        source: Artifact | None = None
        path_field = INPUT_FIELDS.get(call.name)
        if path_field and isinstance(args.get(path_field), str):
            try:
                source = known_file(self.ctx, args[path_field])
            except LocalToolError as error:
                return await self.deny(call, error.code, error.message, error.hint)
            args[path_field] = source.path
            if call.name == "synthesize_speech":
                problem_text = guards.speech_source_problem(source, str(args.get("language", "")))
                if problem_text is not None:
                    return await self.deny(call, "speech_source", problem_text, None)
        if call.name in MCP_TOOLS:
            args = guards.harden_tool_args(call.name, args)
        digest = self.digest(call.name, args)
        failed = self.ledger.failed_before(call.name, digest)
        if failed is not None:
            return await self.deny(
                call, "repeat_refused", f"The same call already failed with '{failed.error_code}'.",
                "Change the arguments or take a different approach.",
            )  # fmt: skip
        uncertain = self.ledger.uncertain_attempts(call.name, digest)
        if uncertain >= 2:
            return await self.deny(call, "repeat_refused", "This call already timed out twice.", None)
        pending = PendingCall(call, args, reason, plan_step, source, digest, uncertain_retry=uncertain == 1)
        if call.name not in FREE_TOOLS:
            try:
                quote = quote_tool(call.name, args, source)
            except ValueError as error:
                return await self.deny(call, "cannot_price", str(error), None)
            reservation = await self.budget.reserve(quote)
            if reservation is None:
                self.budget_blocked = True
                return await self.deny(
                    call, "budget_exceeded",
                    f"This call may cost up to Rs {quote.amount_inr:.2f}; "
                    f"only Rs {self.budget.remaining_inr:.2f} is left.",
                    "Finish with what is verified, or choose a cheaper step.",
                )  # fmt: skip
            pending.reservation = reservation
        return pending

    async def execute(self, item: PendingCall) -> CallOutcome:
        started = monotonic()
        name = item.call.name
        try:
            if name in self.local:
                result = await self.local[name].execute(item.args, replace(self.ctx, seq=None))
                return CallOutcome(local=result, latency_ms=(monotonic() - started) * 1000)
            assert self.mcp is not None
            mcp_result = await self.mcp.call(name, item.args, deadline=self.deadline)
            return CallOutcome(mcp=mcp_result, latency_ms=mcp_result.latency_ms)
        except LocalToolError as error:
            return CallOutcome(
                error=McpError(
                    code=error.code, message=error.message, hint=error.hint, retryable=error.retryable,
                    details=error.details, uncertain=bool(error.details.get("uncertain")),
                ),
                latency_ms=(monotonic() - started) * 1000,
            )  # fmt: skip
        except Exception as error:
            return CallOutcome(
                error=McpError(code="tool_error", message=f"{type(error).__name__}: {error}", uncertain=True),
                latency_ms=(monotonic() - started) * 1000,
            )

    async def settle(self, item: PendingCall, outcome: CallOutcome) -> float:
        if item.reservation is None:
            return 0.0
        if outcome.mcp is not None:
            usage = outcome.mcp.usage
            error = outcome.mcp.error
        elif outcome.local is not None:
            usage, error = outcome.local.usage, None
        else:
            error = outcome.error
            usage = error.details.get("usage") if error is not None else None
        uncertain = bool(error and error.uncertain) or bool(usage and usage.get("billing_uncertain"))
        charged = usage.get("estimated_cost_inr") if isinstance(usage, dict) else None
        if not isinstance(charged, int | float):
            charged = None
        return await self.budget.settle(
            item.reservation,
            charged,
            uncertain=uncertain,
            possibly_billed_failed_attempts=int((usage or {}).get("possibly_billed_failed_attempts") or 0),
            cache_hits=int((usage or {}).get("cache_hits") or 0),
        )

    async def complete(self, item: PendingCall, outcome: CallOutcome) -> Observation:
        cost = await self.settle(item, outcome)
        self.result.steps_used += 1
        name = item.call.name
        artifact_ids: list[str] = []
        error = outcome.error
        data: dict[str, Any] | None = None
        if outcome.mcp is not None:
            if outcome.mcp.ok and outcome.mcp.data is not None:
                data = outcome.mcp.data
                try:
                    artifact_ids = self.register_outputs(name, data, item.source)
                except (OSError, ValueError) as exc:
                    error = McpError(code="register_failed", message=f"The output could not be recorded: {exc}")
                else:
                    apply_step(self.ledger, artifact_ids, step_verdict(name, data))
            else:
                error = outcome.mcp.error
        elif outcome.local is not None:
            data = outcome.local.data
            artifact_ids = [item_id for item_id in outcome.local.artifact_ids if self.ledger.get(item_id)]
        kind = "uncertain_retry" if item.uncertain_retry else "tool"
        event = await self.trace.emit(
            "act", kind, tool_call_id=item.call.id, tool=name, reason=item.reason, plan_step=item.plan_step,
            args=item.args, artifacts=artifact_ids, latency_ms=outcome.latency_ms, cost_inr=cost,
            error=None if error is None else TraceError(
                code=error.code, message=error.message, hint=error.hint, retryable=error.retryable
            ),
        )  # fmt: skip
        if item.uncertain_retry:
            self.result.missed_targets.append(f"no_waste: {name} was retried after an uncertain timeout")
        for artifact_id in artifact_ids:
            artifact = self.ledger.get(artifact_id)
            if artifact is not None and artifact.created_seq is None:
                artifact.created_seq = event.seq
        self.ledger.record(
            ActionRecord(
                seq=event.seq, tool_call_id=item.call.id, tool=name, args_digest=item.digest,
                outcome="ok" if error is None else "error",
                error_code=None if error is None else error.code,
                retryable=None if error is None else error.retryable,
                uncertain=bool(error and error.uncertain),
                artifacts=artifact_ids, cost_inr=cost,
            )
        )  # fmt: skip
        if error is not None:
            observation = self.observer.error(name, error)
            self.issues.append(f"{name}: {error.code}")
        elif outcome.mcp is not None:
            observation = self.observer.mcp_result(outcome.mcp)
        else:
            observation = self.observer.local_result(name, data or {}, artifact_ids)
        self.result.redactions += observation.redactions
        return observation

    def register_outputs(self, tool: str, data: dict[str, Any], source: Artifact | None) -> list[str]:
        return register_outputs(self.settings, self.ledger, tool, data, source)

    async def finish(self, call: ToolCall) -> tuple[Observation, FinishProposal | None]:
        prepared = await self.prepare(call)
        if isinstance(prepared, Observation):
            return prepared, None
        outcome = await self.execute(prepared)
        observation = await self.complete(prepared, outcome)
        if outcome.local is None:
            return observation, None
        params = FinishInput.model_validate(prepared.args)
        mapping: dict[str, str | None] = {
            entry["id"]: entry.get("artifact") for entry in outcome.local.data.get("deliverables", [])
        }
        await self.trace.emit(
            "act", "finish", tool_call_id=call.id, tool="finish", message="Finish proposed.",
            result_summary={"deliverables": mapping, "missing": outcome.local.data.get("missing", [])},
        )  # fmt: skip
        return observation, FinishProposal(
            answer_en=params.answer_en, assumptions=params.assumptions, deliverables=mapping
        )

    def inferred(self) -> dict[str, str | None]:
        mapping: dict[str, str | None] = {}
        for item in self.plan.deliverables:
            artifact = self.candidate(item)
            if item.kind == "answer" or artifact is not None:
                mapping[item.id] = None if artifact is None else artifact.id
        return mapping

    async def verify_all(self, mapping: dict[str, str | None], answer_en: str | None) -> dict[str, Verdict]:
        verdicts: dict[str, Verdict] = {}
        for item in self.plan.deliverables:
            artifact_id = mapping.get(item.id)
            artifact = self.ledger.get(artifact_id) if artifact_id else None
            if item.kind != "answer" and item.id not in mapping:
                verdict = Verdict(status="missing")
            else:
                verdict = verify_deliverable(self.ledger, item, artifact, answer_en)
            verdicts[item.id] = verdict
            failed = [check.name for check in verdict.checks if check.status == "failed"]
            await self.trace.emit(
                "verify", "verify", message=f"{item.id}: {verdict.status}",
                artifacts=[artifact.id] if artifact is not None else [],
                result_summary={"status": verdict.status, "failed_checks": failed},
            )  # fmt: skip
        return verdicts

    @staticmethod
    def problems(verdicts: dict[str, Verdict]) -> list[str]:
        problems = []
        for deliverable_id, verdict in verdicts.items():
            if verdict.status == "missing":
                problems.append(f"{deliverable_id}: missing")
            elif verdict.status == "failed":
                failed = [f"{check.name} ({check.detail})" if check.detail else check.name
                          for check in verdict.checks if check.status == "failed"]  # fmt: skip
                problems.append(f"{deliverable_id}: failed {', '.join(failed)}")
        return problems

    def can_retry(self) -> bool:
        return (
            not self.result.verification_retry_used
            and self.time_left() > 0
            and self.result.steps_used < self.settings.max_steps
            and self.result.llm_turns < self.settings.llm_turn_limit
        )

    async def conclude(self, proposal: FinishProposal) -> LoopResult | None:
        verdicts = await self.verify_all(proposal.deliverables, proposal.answer_en)
        problems = self.problems(verdicts)
        if problems and self.can_retry():
            self.result.verification_retry_used = True
            self.history.append(prompts.verification_feedback(problems))
            return None
        self.result.finish = proposal
        self.result.deliverables = {item.id: proposal.deliverables.get(item.id) for item in self.plan.deliverables}
        self.result.verdicts = verdicts
        reason: StopReason = "budget_exhausted" if problems and self.budget_blocked else "finished"
        self.result.stop_reason = reason
        await self.trace.emit("act", "stop", message=f"stop_reason={reason}")
        return self.result

    async def stop(self, reason: StopReason, explanation: str | None = None) -> LoopResult:
        mapping = self.inferred()
        self.result.deliverables = {item.id: mapping.get(item.id) for item in self.plan.deliverables}
        self.result.verdicts = await self.verify_all(mapping, None)
        self.result.stop_reason = reason
        self.result.explanation = explanation
        await self.trace.emit(
            "act", "stop", message=f"stop_reason={reason}" + (f": {explanation}" if explanation else "")
        )
        return self.result
