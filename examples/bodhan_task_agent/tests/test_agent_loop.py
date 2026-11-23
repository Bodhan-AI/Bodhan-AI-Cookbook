from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from bodhan_agent.agent.loop import Controller
from bodhan_agent.agent.observe import Observer
from bodhan_agent.clients.llm import LlmError
from bodhan_agent.core.budget import BudgetManager
from bodhan_agent.core.config import RunSettings
from bodhan_agent.core.trace import TraceWriter
from bodhan_agent.schemas.ledger import Artifact, Ledger
from bodhan_agent.schemas.llm import LlmReply, ToolCall
from bodhan_agent.schemas.mcp import McpError, McpResult, McpToolSpec
from bodhan_agent.schemas.plan import Plan
from bodhan_agent.schemas.trace import LlmTokens
from bodhan_agent.tools.base import file_digest

TAMIL = "தாவரங்கள் சூரிய ஒளியில் உணவு தயாரிக்கின்றன."
USAGE = {
    "model_calls": 1,
    "http_attempts": 1,
    "estimated_cost_inr": 0.05,
    "billing_uncertain": False,
    "cache_hits": 0,
    "possibly_billed_failed_attempts": 0,
    "latency_ms": 1,
}
SPECS = [
    McpToolSpec(
        name=name,
        description=f"{name} tool.",
        input_schema={
            "type": "object",
            "properties": {
                "text_path": {"type": "string"},
                "target_language": {"type": "string"},
                "overwrite": {"type": "boolean"},
            },  # fmt: skip
            "required": ["overwrite"],
        },
    )
    for name in ("translate_text", "transcribe_audio", "synthesize_speech", "ocr_document")
]

Responder = Callable[[str, dict[str, Any]], McpResult]


class FakeMcp:
    tools = SPECS

    def __init__(self, work: Path, respond: Responder | None = None, delay: float = 0.0) -> None:
        self.work = work
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.respond = respond or self.translate
        self.delay = delay
        self.active = 0
        self.peak = 0
        self.counter = 0

    def translate(self, name: str, args: dict[str, Any]) -> McpResult:
        self.counter += 1
        out = self.work / f"notes.ta-{self.counter}.md"
        out.write_text(TAMIL, encoding="utf-8")
        data = {
            "output_path": str(out), "target_language": args.get("target_language", "ta"), "script": "native",
            "translation": TAMIL, "translation_is_preview": False, "source_chars": 30, "translated_chars": len(TAMIL),
            "chunks": 1, "recovery_splits": 0, "verification_status": "passed", "untrusted_content": True,
            "warnings": [], "usage": USAGE,
        }  # fmt: skip
        return McpResult(tool=name, ok=True, latency_ms=3, data=data)

    async def call(self, name: str, args: dict[str, Any], *, deadline: float | None = None) -> McpResult:
        self.calls.append((name, args))
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            return self.respond(name, args)
        finally:
            self.active -= 1


Step = list[tuple[str, dict[str, Any]]] | str | LlmReply | Exception | Callable[[list[dict[str, Any]]], Any]


class ScriptedLlm:
    def __init__(self, script: list[Step]) -> None:
        self.script = list(script)
        self.seen: list[list[dict[str, Any]]] = []
        self.tools: list[Any] = []
        self.turn = 0

    async def complete(self, messages: Any, tools: Any = None, *, deadline: float | None = None) -> LlmReply:
        self.seen.append(list(messages))
        self.tools = list(tools or [])
        self.turn += 1
        step = self.script.pop(0) if self.script else "Nothing more."
        if callable(step) and not isinstance(step, LlmReply):
            step = step(list(messages))
        if isinstance(step, Exception):
            raise step
        if isinstance(step, LlmReply):
            return step
        if isinstance(step, str):
            return LlmReply(content=step, latency_ms=1, attempts=1, finish_reason="stop")
        calls = [
            ToolCall(id=f"t{self.turn}c{index}", name=name, raw_arguments=json.dumps(args), arguments=args)
            for index, (name, args) in enumerate(step)
        ]
        return LlmReply(
            tool_calls=calls, latency_ms=1, attempts=1, finish_reason="tool_calls",
            tokens=LlmTokens(prompt=100, completion=20),
        )  # fmt: skip


PLAN = {
    "task_en": "Translate the notes to Tamil.",
    "goal": "Tamil notes and a short answer.",
    "deliverables": [
        {"id": "D1", "kind": "markdown", "language": "ta", "description": "Tamil notes"},
        {"id": "D2", "kind": "answer", "language": "en", "description": "What was done"},
    ],
    "feasibility": {"status": "feasible"},
}


MULTI_PLAN = {
    **PLAN,
    "deliverables": [
        *PLAN["deliverables"],
        {"id": "D3", "kind": "markdown", "language": "hi", "description": "Hindi notes"},
        {"id": "D4", "kind": "markdown", "language": "te", "description": "Telugu notes"},
    ],
}


class Run:
    def __init__(self, tmp_path: Path, script: list[Step], **options: Any) -> None:
        self.source = tmp_path / "notes.md"
        self.source.write_text("Plants make food from sunlight.", encoding="utf-8")
        settings = RunSettings.create("Translate my notes to Tamil", [str(self.source)], str(tmp_path / "out"))
        for key in ("max_steps", "max_parallel_calls", "max_llm_turns", "deadline_s"):
            if key in options:
                object.__setattr__(settings, key, options.pop(key))
        settings.work_dir.mkdir(parents=True)
        self.settings = settings
        self.ledger = Ledger()
        digest, size = file_digest(self.source)
        self.ledger.add(
            Artifact(
                id="f1", path=str(self.source), kind="markdown", producer="user_input", sha256=digest,
                size_bytes=size, step_status="ok", language="en", script="Latin",
            )
        )  # fmt: skip
        self.budget = BudgetManager(options.pop("limit_inr", 20.0))
        self.trace = TraceWriter(settings.out_dir / "trace.jsonl", cost_source=self.budget.cost_source)
        self.mcp = FakeMcp(settings.work_dir, options.pop("respond", None), options.pop("delay", 0.0))
        self.llm = ScriptedLlm(script)
        self.controller = Controller(
            settings, self.ledger, Plan.model_validate(options.pop("plan", PLAN)), self.llm,  # type: ignore[arg-type]
            self.budget, self.trace, Observer(self.ledger), mcp=self.mcp,  # type: ignore[arg-type]
            input_ids=["f1"], started=options.pop("started", None),
        )  # fmt: skip

    async def run(self) -> Any:
        return await self.controller.run()

    def events(self) -> list[dict[str, Any]]:
        lines = (self.settings.out_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines]

    def kinds(self) -> list[str]:
        return [event["kind"] for event in self.events()]

    def guard_codes(self) -> list[str]:
        return [event["error"]["code"] for event in self.events() if event["kind"] == "guard" and "error" in event]

    def translate_call(self, **extra: Any) -> tuple[str, dict[str, Any]]:
        return ("translate_text", {"reason": "translate", "text_path": str(self.source), "target_language": "ta",
                                   **extra})  # fmt: skip

    def finish_call(self, artifact: str | None = "A1", answer: str = "Translated the notes to Tamil.") -> Any:
        def build(messages: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
            deliverables: list[dict[str, Any]] = [{"id": "D2"}]
            if artifact is not None:
                found = self.ledger.get(artifact)
                assert found is not None
                deliverables.insert(0, {"id": "D1", "path": found.path})
            return [("finish", {"reason": "done", "answer_en": answer, "deliverables": deliverables})]

        return build


def tool_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [message for message in messages if message["role"] == "tool"]


async def test_happy_path_finishes_verified(tmp_path: Path) -> None:
    run = Run(tmp_path, [])
    run.llm.script = [[run.translate_call()], run.finish_call()]
    result = await run.run()
    assert result.stop_reason == "finished"
    assert result.deliverables == {"D1": "A1", "D2": None}
    assert {key: verdict.status for key, verdict in result.verdicts.items()} == {"D1": "verified", "D2": "verified"}
    assert result.finish is not None and result.finish.answer_en == "Translated the notes to Tamil."
    assert (result.steps_used, result.llm_turns) == (2, 2)
    assert result.llm_tokens == LlmTokens(prompt=200, completion=40)
    assert [kind for kind in run.kinds() if kind != "plan_update"] == [
        "llm", "tool", "llm", "tool", "finish", "verify", "verify", "stop",
    ]  # fmt: skip
    assert run.kinds()[0] == "plan_update"


async def test_mcp_call_is_hardened_and_meta_stripped(tmp_path: Path) -> None:
    run = Run(tmp_path, [])
    run.llm.script = [[run.translate_call(plan_step="S1")], run.finish_call()]
    await run.run()
    name, args = run.mcp.calls[0]
    assert name == "translate_text"
    assert args == {"text_path": str(run.source), "target_language": "ta", "overwrite": False}
    tool_event = next(event for event in run.events() if event["kind"] == "tool")
    assert (tool_event["reason"], tool_event["plan_step"]) == ("translate", "S1")


async def test_tool_definitions_hide_overwrite_and_add_reason(tmp_path: Path) -> None:
    run = Run(tmp_path, ["done", "done"])
    await run.run()
    names = [tool["function"]["name"] for tool in run.llm.tools]
    assert names == [
        "translate_text", "transcribe_audio", "synthesize_speech", "ocr_document",
        "read", "write", "detect_language", "make_subtitles", "finish",
    ]  # fmt: skip
    translate = run.llm.tools[0]["function"]["parameters"]
    assert "overwrite" not in translate["properties"] and translate["required"] == ["reason"]


async def test_outputs_are_registered_with_provenance(tmp_path: Path) -> None:
    run = Run(tmp_path, [])
    run.llm.script = [[run.translate_call()], run.finish_call()]
    await run.run()
    artifact = run.ledger.get("A1")
    assert artifact is not None
    assert (artifact.kind, artifact.producer, artifact.tool, artifact.sources) == (
        "markdown", "bodhan", "translate_text", ["f1"],
    )  # fmt: skip
    assert (artifact.language, artifact.language_source, artifact.script, artifact.indic_text) == (
        "ta", "requested", "Tamil", True,
    )  # fmt: skip
    assert artifact.step_status == "ok" and artifact.tool_checks["verification_status"] == "passed"
    tool_seq = next(event["seq"] for event in run.events() if event["kind"] == "tool")
    assert artifact.created_seq == tool_seq
    assert [(action.tool, action.outcome) for action in run.ledger.actions] == [
        ("translate_text", "ok"), ("finish", "ok"),
    ]  # fmt: skip


async def test_budget_is_settled_before_trace(tmp_path: Path) -> None:
    run = Run(tmp_path, [])
    run.llm.script = [[run.translate_call()], run.finish_call()]
    await run.run()
    tool_event = next(event for event in run.events() if event["kind"] == "tool")
    assert (tool_event["cost_inr"], tool_event["cum_cost_inr"]) == (0.05, 0.05)
    assert run.budget.spent_inr == 0.05 and run.budget.open_reservations == []
    assert run.ledger.actions[0].cost_inr == 0.05


async def test_translation_is_hidden_from_llm(tmp_path: Path) -> None:
    run = Run(tmp_path, [])
    run.llm.script = [[run.translate_call()], run.finish_call()]
    result = await run.run()
    second = run.llm.seen[1]
    assert all("தமிழ்" not in str(message["content"]) for message in second)
    assert not any("சூரிய" in str(message["content"]) for message in second)
    assert result.redactions >= 1


async def test_every_tool_call_gets_a_tool_message(tmp_path: Path) -> None:
    run = Run(tmp_path, [])
    run.llm.script = [
        [run.translate_call(), ("bogus", {"reason": "x"}), ("read", {"reason": "x", "path": "relative.md"})],
        run.finish_call(),
    ]
    await run.run()
    second = run.llm.seen[1]
    assistant = next(message for message in second if message["role"] == "assistant")
    ids = [call["id"] for call in assistant["tool_calls"]]
    assert [message["tool_call_id"] for message in tool_messages(second)] == ids


@pytest.mark.parametrize(
    ("call", "code"),
    [
        (("bogus", {"reason": "x"}), "unknown_tool"),
        (("write", {"reason": "x", "path": "a.md", "content": "नमस्ते दुनिया"}), "indic_text"),
        (("write", {"reason": "नमस्ते", "path": "a.md", "content": "Hello."}), "indic_text"),
        (("write", {"path": "a.md", "content": "Hello."}), "missing_reason"),
        (
            (
                "translate_text",
                {"reason": "x", "text": "aap kaise hain main theek hoon bahut accha", "target_language": "hi"},
            ),
            "romanized_indic",
        ),  # fmt: skip
        (("synthesize_speech", {"reason": "x", "text": "Hello there.", "language": "ta"}), "inline_speech"),
        (
            ("translate_text", {"reason": "x", "text_path": "/etc/hosts", "target_language": "ta"}),
            "path_not_allowed",
        ),  # fmt: skip
    ],
)
async def test_guards_deny_without_running(tmp_path: Path, call: tuple[str, dict[str, Any]], code: str) -> None:
    run = Run(tmp_path, [[call], "done", "done"])
    result = await run.run()
    assert run.guard_codes()[0] == code
    assert run.mcp.calls == []
    assert result.steps_used == 0


async def test_unparsable_arguments_are_denied(tmp_path: Path) -> None:
    reply = LlmReply(
        tool_calls=[ToolCall(id="bad", name="write", raw_arguments="{oops", parse_error="Expecting property name")],
        latency_ms=1, attempts=1, finish_reason="tool_calls",
    )  # fmt: skip
    run = Run(tmp_path, [reply, "done", "done"])
    await run.run()
    assert run.guard_codes() == ["invalid_arguments"]
    assert tool_messages(run.llm.seen[1])[0]["tool_call_id"] == "bad"


async def test_indic_blocks_count_as_blocked_outputs(tmp_path: Path) -> None:
    run = Run(tmp_path, [[("write", {"reason": "x", "path": "a.md", "content": "नमस्ते"})], "done", "done"])
    result = await run.run()
    assert result.blocked_llm_outputs == 1


async def test_escaped_path_is_unescaped(tmp_path: Path) -> None:
    run = Run(tmp_path, [])
    amp = tmp_path / "a&b.md"
    amp.write_text("Hello there friend.", encoding="utf-8")
    digest, size = file_digest(amp)
    run.ledger.add(
        Artifact(id="f2", path=str(amp), kind="markdown", producer="user_input", sha256=digest, size_bytes=size,
                 step_status="ok")
    )  # fmt: skip
    run.llm.script = [[("detect_language", {"reason": "x", "path": str(amp).replace("&", "&amp;")})], "done", "done"]
    await run.run()
    assert run.guard_codes() == []
    tool_event = next(event for event in run.events() if event["kind"] == "tool")
    assert tool_event["args"]["path"] == str(amp)


async def test_speech_from_llm_file_is_denied(tmp_path: Path) -> None:
    run = Run(tmp_path, [])
    run.llm.script = [
        [("write", {"reason": "x", "path": "script.md", "content": "Hello there."})],
        [("synthesize_speech", {"reason": "x", "text_path": str(run.settings.work_dir / "script.md"),
                                "language": "ta"})],  # fmt: skip
        "done", "done",
    ]  # fmt: skip
    await run.run()
    assert run.guard_codes() == ["speech_source"]


async def test_parallel_cap_and_concurrency(tmp_path: Path) -> None:
    run = Run(tmp_path, [], max_parallel_calls=2, delay=0.02, plan=MULTI_PLAN)
    run.llm.script = [
        [run.translate_call(target_language="ta"), run.translate_call(target_language="hi"),
         run.translate_call(target_language="te")],
        "done", "done",
    ]  # fmt: skip
    await run.run()
    assert len(run.mcp.calls) == 2
    assert run.mcp.peak == 2
    assert run.guard_codes() == ["not_executed"]


async def test_step_limit_stops_calls_and_run(tmp_path: Path) -> None:
    run = Run(tmp_path, [], max_steps=1)
    run.llm.script = [[run.translate_call(target_language="ta"), run.translate_call(target_language="hi")]]
    result = await run.run()
    assert len(run.mcp.calls) == 1
    assert run.guard_codes() == ["step_limit"]
    assert result.stop_reason == "step_limit"
    assert result.deliverables == {"D1": "A1", "D2": None}
    assert result.verdicts["D1"].status == "verified"
    assert result.verdicts["D2"].status == "failed"


async def test_turn_limit(tmp_path: Path) -> None:
    run = Run(tmp_path, [[("read", {"reason": "x", "path": "nope"})]] * 5, max_llm_turns=2)
    result = await run.run()
    assert result.stop_reason == "turn_limit"
    assert result.llm_turns == 2


async def test_deadline_stops_before_calling_llm(tmp_path: Path) -> None:
    run = Run(tmp_path, ["done"], deadline_s=5.0)
    result = await run.run()
    assert result.stop_reason == "deadline"
    assert run.llm.seen == []


async def test_llm_failure_stops(tmp_path: Path) -> None:
    run = Run(tmp_path, [LlmError("llm_unavailable", "no key")])
    result = await run.run()
    assert result.stop_reason == "llm_unavailable"
    assert "no key" in (result.explanation or "")


async def test_budget_refusal(tmp_path: Path) -> None:
    run = Run(tmp_path, [], limit_inr=0.0)
    run.llm.script = [[run.translate_call()], run.finish_call(artifact=None, answer="Could not translate.")]
    result = await run.run()
    assert run.guard_codes() == ["budget_exceeded"]
    assert run.mcp.calls == []
    assert result.stop_reason == "budget_exhausted"
    assert result.verdicts["D1"].status == "missing"
    assert result.verification_retry_used is False and len(run.llm.seen) == 2
    assert result.finish is not None and result.finish.answer_en == "Could not translate."


async def test_unsupported_deliverable_does_not_trigger_retry(tmp_path: Path) -> None:
    french = {"id": "D3", "kind": "markdown", "language": "fr", "description": "French notes"}
    plan = {**PLAN, "deliverables": [*PLAN["deliverables"], french]}
    run = Run(tmp_path, [], plan=plan)
    run.llm.script = [[run.translate_call()], run.finish_call(answer="Tamil done; French is not supported.")]
    result = await run.run()
    assert result.stop_reason == "finished" and result.verification_retry_used is False
    assert result.verdicts["D1"].status == "verified" and result.verdicts["D3"].status == "missing"
    assert len(run.llm.seen) == 2


async def test_translation_into_unplanned_language_is_refused(tmp_path: Path) -> None:
    french = {"id": "D3", "kind": "markdown", "language": "fr", "description": "French notes"}
    run = Run(tmp_path, [], plan={**PLAN, "deliverables": [*PLAN["deliverables"], french]})
    run.llm.script = [[run.translate_call(target_language="as")], "done", "done"]
    await run.run()
    assert run.mcp.calls == []
    assert run.guard_codes() == ["target_not_planned"]


async def test_unsupported_deliverable_is_never_filled(tmp_path: Path) -> None:
    french = {"id": "D3", "kind": "markdown", "language": "fr", "description": "French notes"}
    run = Run(tmp_path, [], plan={**PLAN, "deliverables": [*PLAN["deliverables"], french]})
    path = run.settings.work_dir / "scan.md"
    path.write_text("উদ্ভিদ খাদ্য তৈরি করে।", encoding="utf-8")
    digest, size = file_digest(path)
    run.ledger.add(
        Artifact(
            id="A9", path=str(path), kind="markdown", producer="bodhan", tool="translate_text", sha256=digest,
            size_bytes=size, step_status="ok", script="Bengali", indic_text=True,
        )
    )  # fmt: skip
    assert "D3" not in run.controller.inferred()
    verdicts = await run.controller.verify_all({"D3": "A9"}, None)
    assert verdicts["D3"].status == "missing"


async def test_failed_call_is_not_repeated(tmp_path: Path) -> None:
    def fail(name: str, args: dict[str, Any]) -> McpResult:
        return McpResult(
            tool=name, ok=False, latency_ms=1,
            error=McpError(code="unsupported_language", message="no", retryable=False, details={"usage": USAGE}),
        )  # fmt: skip

    run = Run(tmp_path, [], respond=fail)
    run.llm.script = [[run.translate_call()], [run.translate_call()], "done", "done"]
    await run.run()
    assert len(run.mcp.calls) == 1
    assert run.guard_codes()[0] == "repeat_refused"
    assert run.ledger.actions[0].outcome == "error"
    assert run.budget.spent_inr == 0.05


async def test_uncertain_timeout_allows_one_retry(tmp_path: Path) -> None:
    def timeout(name: str, args: dict[str, Any]) -> McpResult:
        return McpResult(
            tool=name, ok=False, latency_ms=1,
            error=McpError(code="tool_timeout", message="slow", retryable=True, uncertain=True),
        )  # fmt: skip

    run = Run(tmp_path, [], respond=timeout)
    run.llm.script = [[run.translate_call()], [run.translate_call()], [run.translate_call()], "done", "done"]
    result = await run.run()
    assert len(run.mcp.calls) == 2
    assert "uncertain_retry" in run.kinds()
    assert run.guard_codes()[0] == "repeat_refused"
    assert result.missed_targets == ["no_waste: translate_text was retried after an uncertain timeout"]
    assert run.budget.billing_uncertain is True
    assert run.budget.spent_inr > 0


async def test_truncated_reply_runs_nothing_and_nudges(tmp_path: Path) -> None:
    cut = LlmReply(
        tool_calls=[ToolCall(id="x", name="write", raw_arguments="{}", arguments={"reason": "x"})],
        latency_ms=1, attempts=1, finish_reason="length",
    )  # fmt: skip
    run = Run(tmp_path, [cut, "done", "done"])
    await run.run()
    second = run.llm.seen[1]
    assert tool_messages(second) == []
    assert "cut off" in second[-1]["content"]
    assert not any(message.get("tool_calls") for message in second)


async def test_repeated_bad_replies_stop(tmp_path: Path) -> None:
    cut = LlmReply(content="partial", latency_ms=1, attempts=1, finish_reason="content_filter")
    run = Run(tmp_path, [cut, cut, cut])
    result = await run.run()
    assert result.stop_reason == "llm_unavailable"
    assert "content filter" in run.llm.seen[1][-1]["content"]


async def test_no_tool_reply_is_nudged_then_implicit_finish(tmp_path: Path) -> None:
    run = Run(tmp_path, [])
    run.llm.script = [[run.translate_call()], "I am done.", "The notes are translated to Tamil."]
    result = await run.run()
    assert "without a tool call" in run.llm.seen[2][-1]["content"]
    assert result.finish is not None and result.finish.implicit is True
    assert result.finish.answer_en == "The notes are translated to Tamil."
    assert result.deliverables == {"D1": "A1", "D2": None}
    assert result.stop_reason == "finished"


async def test_non_english_reply_text_is_removed(tmp_path: Path) -> None:
    run = Run(tmp_path, ["यह हो गया", "done"])
    result = await run.run()
    assert result.blocked_llm_outputs >= 1
    assistant = next(message for message in run.llm.seen[1] if message["role"] == "assistant")
    assert "removed by the controller" in assistant["content"]


async def test_verification_feedback_gives_one_retry(tmp_path: Path) -> None:
    run = Run(tmp_path, [])
    run.llm.script = [
        [run.translate_call()],
        run.finish_call(artifact=None),
        run.finish_call(),
    ]
    result = await run.run()
    third = run.llm.seen[2]
    assert "<verification_feedback>" in third[-1]["content"] and "D1: missing" in third[-1]["content"]
    assert result.verification_retry_used is True
    assert result.verdicts["D1"].status == "verified"
    assert result.stop_reason == "finished"


async def test_verification_retry_only_once(tmp_path: Path) -> None:
    run = Run(tmp_path, [])
    run.llm.script = [[run.translate_call()], run.finish_call(artifact=None), run.finish_call(artifact=None)]
    result = await run.run()
    assert result.verification_retry_used is True
    assert result.verdicts["D1"].status == "missing"
    assert result.stop_reason == "finished"
    assert len(run.llm.seen) == 3


async def test_rejected_finish_lets_llm_fix(tmp_path: Path) -> None:
    run = Run(tmp_path, [])
    run.llm.script = [
        [run.translate_call()],
        [("finish", {"reason": "done", "answer_en": "Done.", "deliverables": [{"id": "D9"}]})],
        run.finish_call(),
    ]
    result = await run.run()
    assert "finish_rejected" in tool_messages(run.llm.seen[2])[-1]["content"]
    assert result.stop_reason == "finished"
    assert result.verdicts["D1"].status == "verified"


async def test_finish_runs_after_other_calls_in_turn(tmp_path: Path) -> None:
    run = Run(tmp_path, [])

    def both(messages: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
        path = str(run.settings.work_dir / "notes.ta-1.md")
        return [
            ("finish", {"reason": "done", "answer_en": "Done.", "deliverables": [{"id": "D1", "path": path},
                                                                                {"id": "D2"}]}),
            run.translate_call(),
        ]  # fmt: skip

    run.llm.script = [both]
    result = await run.run()
    assert result.stop_reason == "finished"
    assert result.verdicts["D1"].status == "verified"


async def test_second_finish_in_turn_is_refused(tmp_path: Path) -> None:
    finish = ("finish", {"reason": "done", "answer_en": "Nothing could be done.", "deliverables": []})
    run = Run(tmp_path, [[finish, finish], "done"])
    await run.run()
    assert "duplicate_finish" in run.guard_codes()


async def test_old_observations_are_compacted(tmp_path: Path) -> None:
    run = Run(tmp_path, [])
    run.llm.script = [
        [run.translate_call(target_language="ta")],
        [("read", {"reason": "x", "path": "nope"})],
        [("detect_language", {"reason": "x", "path": str(run.source)})],
        "done",
        "done",
    ]
    await run.run()
    fourth = tool_messages(run.llm.seen[3])
    assert fourth[0]["content"].startswith("translate_text ok -> A1")
    assert fourth[1]["content"].startswith("{")
    assert fourth[2]["content"].startswith("{")
    fifth = tool_messages(run.llm.seen[4])
    assert fifth[1]["content"] == "read error invalid_input (fixable)"


async def test_status_board_shows_progress(tmp_path: Path) -> None:
    run = Run(tmp_path, [])
    run.llm.script = [[run.translate_call()], run.finish_call()]
    await run.run()
    board = run.llm.seen[1][2]["content"]
    assert board.startswith("<status_board>")
    assert "- D1 (markdown, ta): candidate A1 (ok)" in board
    assert "Steps: 1 of 30 used." in board
    assert "Rs 0.05 of Rs 20.00" in board


async def test_local_tool_error_is_observation(tmp_path: Path) -> None:
    run = Run(tmp_path, [[("write", {"reason": "x", "path": "a.srt", "content": "Hello."})], "done", "done"])
    result = await run.run()
    assert result.steps_used == 1
    assert run.ledger.actions[0].outcome == "error" and run.ledger.actions[0].error_code == "invalid_input"
    assert '"code":"invalid_input"' in tool_messages(run.llm.seen[1])[0]["content"]


async def test_unexpected_tool_exception_does_not_crash(tmp_path: Path) -> None:
    def boom(name: str, args: dict[str, Any]) -> McpResult:
        raise RuntimeError("kaput")

    run = Run(tmp_path, [], respond=boom)
    run.llm.script = [[run.translate_call()], "done", "done"]
    await run.run()
    assert run.ledger.actions[0].error_code == "tool_error"
    assert run.ledger.actions[0].uncertain is True


async def test_missing_output_file_is_register_failure(tmp_path: Path) -> None:
    def ghost(name: str, args: dict[str, Any]) -> McpResult:
        data = {"output_path": str(tmp_path / "ghost.md"), "translated_chars": 3, "usage": USAGE}
        return McpResult(tool=name, ok=True, latency_ms=1, data=data)

    run = Run(tmp_path, [], respond=ghost)
    run.llm.script = [[run.translate_call()], "done", "done"]
    await run.run()
    assert run.ledger.actions[0].error_code == "register_failed"
    assert [artifact.id for artifact in run.ledger.artifacts.values()] == ["f1"]


async def test_trace_is_valid_jsonl_without_indic(tmp_path: Path) -> None:
    run = Run(tmp_path, [])
    run.llm.script = [[run.translate_call()], run.finish_call()]
    await run.run()
    text = (run.settings.out_dir / "trace.jsonl").read_text(encoding="utf-8")
    events = [json.loads(line) for line in text.splitlines()]
    assert [event["seq"] for event in events] == list(range(len(events)))
    assert "சூரிய" not in text


async def test_translating_english_to_english_is_refused(tmp_path: Path) -> None:
    run = Run(tmp_path, [])
    run.llm.script = [[run.translate_call(target_language="en")], "done", "done"]
    await run.run()
    assert run.guard_codes() == ["same_language"]
    assert run.mcp.calls == []


async def test_romanized_source_may_be_translated_to_english(tmp_path: Path) -> None:
    run = Run(tmp_path, [])
    run.source.write_text("aap kaise hain main theek hoon bahut accha hai", encoding="utf-8")
    digest, size = file_digest(run.source)
    run.ledger.artifacts["f1"].sha256, run.ledger.artifacts["f1"].size_bytes = digest, size
    run.llm.script = [[run.translate_call(target_language="en")], "done", "done"]
    await run.run()
    assert run.guard_codes() == []
    assert len(run.mcp.calls) == 1


async def test_translating_into_known_same_language_is_refused(tmp_path: Path) -> None:
    run = Run(tmp_path, [])
    run.llm.script = [
        [run.translate_call()],
        lambda messages: [
            (
                "translate_text",
                {"reason": "again", "text_path": run.ledger.artifacts["A1"].path, "target_language": "ta"},
            )
        ],  # fmt: skip
        "done",
        "done",
    ]
    await run.run()
    assert run.guard_codes() == ["same_language"]
    assert len(run.mcp.calls) == 1


async def test_mcp_output_outside_work_is_refused(tmp_path: Path) -> None:
    run = Run(tmp_path, [])
    target = str(run.settings.artifacts_dir / "notes.ta.md")
    run.llm.script = [[run.translate_call(output_path=target)], "done", "done"]
    await run.run()
    assert run.guard_codes() == ["path_not_allowed"]
    assert run.mcp.calls == []
    assert "plain file name" in tool_messages(run.llm.seen[1])[0]["content"]


@pytest.mark.parametrize("name", ["notes.ta.md", "WORK"])
async def test_mcp_output_inside_work_is_allowed(tmp_path: Path, name: str) -> None:
    run = Run(tmp_path, [])
    output = name if name != "WORK" else str(run.settings.work_dir / "sub" / "notes.ta.md")
    run.llm.script = [[run.translate_call(output_path=output)], "done", "done"]
    await run.run()
    assert run.guard_codes() == []
    assert run.mcp.calls[0][1]["output_path"] == output


async def test_tool_trace_has_result_summary(tmp_path: Path) -> None:
    run = Run(tmp_path, [])
    run.llm.script = [[run.translate_call(), ("read", {"reason": "x", "path": "relative.md"})], run.finish_call()]
    await run.run()
    tools = [event for event in run.events() if event["kind"] == "tool"]
    assert tools[0]["result_summary"] == {"result": tools[0]["result_summary"]["result"], "outputs": ["A1"]}
    assert tools[0]["result_summary"]["result"].startswith("translate_text ok -> A1")
    assert tools[1]["result_summary"] == {"result": "read error invalid_input (fixable)", "outputs": []}


STEPPED_PLAN = {
    **PLAN,
    "steps": [
        {"id": "S1", "intent": "Translate the notes", "why": "Tamil must come from Bodhan.", "tool": "translate_text"},
        {"id": "S2", "intent": "Check the language", "why": "Only if unclear.", "tool": "detect_language",
         "condition": "only if the language is unclear"},
    ],
}  # fmt: skip


def plan_events(run: Run) -> list[dict[str, Any]]:
    return [event for event in run.events() if event["kind"] == "plan_update"]


async def test_plan_progress_is_tracked_and_published(tmp_path: Path) -> None:
    run = Run(tmp_path, [], plan=STEPPED_PLAN)
    run.llm.script = [[run.translate_call(plan_step="S1"), ("read", {"reason": "peek", "path": str(run.source)})],
                      run.finish_call()]  # fmt: skip
    await run.run()
    events = plan_events(run)
    assert events[0]["message"] == "Plan ready."
    assert events[0]["result_summary"]["steps"] == {"S1": "pending", "S2": "pending"}
    assert any(event["result_summary"]["steps"]["S1"] == "in_progress" for event in events)
    assert events[-1]["message"] == "Plan closed."
    assert events[-1]["result_summary"]["steps"] == {"S1": "done", "S2": "skipped"}
    assert events[-1]["result_summary"]["finished"] is True
    assert events[-1]["result_summary"]["deviations"] == 1
    plan = json.loads((run.settings.out_dir / "plan.json").read_text())
    assert plan["finished"] is True and plan["steps"][0]["outputs"] == ["A1"]
    assert plan["deviations"][0]["tool"] == "read" and plan["deviations"][0]["reason"] == "peek"
    assert [item["state"] for item in plan["deliverables"]] == ["verified", "verified"]
    text = (run.settings.out_dir / "plan.md").read_text(encoding="utf-8")
    assert text.startswith("# Plan (final)")
    assert "- [x] **S1** `translate_text`" in text and "- [-] **S2**" in text


async def test_status_board_shows_checklist(tmp_path: Path) -> None:
    run = Run(tmp_path, [], plan=STEPPED_PLAN)
    run.llm.script = [[run.translate_call()], run.finish_call()]
    await run.run()
    first = run.llm.seen[0][2]["content"]
    second = run.llm.seen[1][2]["content"]
    assert "- [ ] S1 translate_text: Translate the notes" in first
    assert "- [x] S1 translate_text: Translate the notes (produced A1)" in second


async def test_finish_is_not_a_deviation(tmp_path: Path) -> None:
    run = Run(tmp_path, [], plan=STEPPED_PLAN)
    run.llm.script = [[run.translate_call()], run.finish_call()]
    await run.run()
    plan = json.loads((run.settings.out_dir / "plan.json").read_text())
    assert plan["deviations"] == []


async def test_failed_step_is_shown(tmp_path: Path) -> None:
    def fail(name: str, args: dict[str, Any]) -> McpResult:
        return McpResult(tool=name, ok=False, latency_ms=1,
                         error=McpError(code="rate_limited", message="slow", retryable=True))  # fmt: skip

    run = Run(tmp_path, [], plan=STEPPED_PLAN, respond=fail)
    run.llm.script = [[run.translate_call()], "done", "done"]
    await run.run()
    plan = json.loads((run.settings.out_dir / "plan.json").read_text())
    assert (plan["steps"][0]["status"], plan["steps"][0]["note"]) == ("failed", "translate_text failed: rate_limited")


async def test_plan_is_closed_on_limit_stop(tmp_path: Path) -> None:
    run = Run(tmp_path, ["done"], plan=STEPPED_PLAN, deadline_s=5.0)
    await run.run()
    events = plan_events(run)
    assert [event["message"] for event in events] == ["Plan ready.", "Plan closed."]
    assert events[-1]["result_summary"]["steps"] == {"S1": "skipped", "S2": "skipped"}


async def test_bare_work_file_name_is_resolved(tmp_path: Path) -> None:
    run = Run(tmp_path, [])
    run.llm.script = [
        [run.translate_call()],
        [("detect_language", {"reason": "check", "path": "notes.ta-1.md"})],
        lambda messages: [("finish", {"reason": "done", "answer_en": "Done.",
                                      "deliverables": [{"id": "D1", "path": "notes.ta-1.md"}, {"id": "D2"}]})],
    ]  # fmt: skip
    result = await run.run()
    assert run.guard_codes() == []
    detect = [event for event in run.events() if event.get("tool") == "detect_language" and event["kind"] == "tool"]
    assert detect[0]["args"]["path"] == str((run.settings.work_dir / "notes.ta-1.md").resolve())
    assert result.verdicts["D1"].status == "verified"


async def test_unknown_bare_name_is_still_refused(tmp_path: Path) -> None:
    run = Run(tmp_path, [[("detect_language", {"reason": "x", "path": "ghost.md"})], "done", "done"])
    await run.run()
    assert run.ledger.actions[0].error_code == "invalid_input"


@pytest.mark.parametrize(("output", "expected"), [("summary.ta.mp3", "mp3"), ("summary.ta.WAV", "wav")])
async def test_speech_format_follows_output_suffix(tmp_path: Path, output: str, expected: str) -> None:
    run = Run(tmp_path, [])
    run.llm.script = [
        [run.translate_call()],
        lambda messages: [("synthesize_speech", {"reason": "speak", "text_path": run.ledger.artifacts["A1"].path,
                                                 "language": "ta", "output_path": output})],
        "done", "done",
    ]  # fmt: skip
    await run.run()
    speech = [args for name, args in run.mcp.calls if name == "synthesize_speech"]
    assert speech[0]["format"] == expected


async def test_explicit_speech_format_is_kept(tmp_path: Path) -> None:
    run = Run(tmp_path, [])
    run.llm.script = [
        [run.translate_call()],
        lambda messages: [("synthesize_speech", {"reason": "speak", "text_path": run.ledger.artifacts["A1"].path,
                                                 "language": "ta", "output_path": "a.mp3", "format": "wav"})],
        "done", "done",
    ]  # fmt: skip
    await run.run()
    speech = [args for name, args in run.mcp.calls if name == "synthesize_speech"]
    assert speech[0]["format"] == "wav"


async def test_finish_without_reason_is_accepted(tmp_path: Path) -> None:
    run = Run(tmp_path, [])

    def finish(messages: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
        path = run.ledger.artifacts["A1"].path
        return [("finish", {"answer_en": "Done.", "deliverables": [{"id": "D1", "path": path}, {"id": "D2"}]})]

    run.llm.script = [[run.translate_call()], finish]
    result = await run.run()
    assert "missing_reason" not in run.guard_codes()
    assert result.stop_reason == "finished" and result.verdicts["D1"].status == "verified"


async def test_other_tools_still_need_reason(tmp_path: Path) -> None:
    run = Run(tmp_path, [[("read", {"path": str(tmp_path / "x.md")})], "done", "done"])
    await run.run()
    assert run.guard_codes() == ["missing_reason"]


async def test_refused_call_records_arguments(tmp_path: Path) -> None:
    run = Run(tmp_path, [[("bogus", {"reason": "try", "x": 1})], "done", "done"])
    await run.run()
    guard = next(event for event in run.events() if event["kind"] == "guard")
    assert guard["args"] == {"reason": "try", "x": 1}


async def test_repeated_refused_calls_stop_the_loop(tmp_path: Path) -> None:
    def fail(name: str, args: dict[str, Any]) -> McpResult:
        return McpResult(
            tool=name, ok=False, latency_ms=1,
            error=McpError(code="unsupported_language", message="no", retryable=False, details={"usage": USAGE}),
        )  # fmt: skip

    run = Run(tmp_path, [], respond=fail)
    run.llm.script = [[run.translate_call()]] * 10
    result = await run.run()
    assert result.stop_reason == "turn_limit"
    assert "3 turns in a row" in (result.explanation or "")
    assert result.llm_turns == 4
    assert run.guard_codes() == ["repeat_refused"] * 3


def add_text(run: Run, artifact_id: str, name: str, text: str, **facts: Any) -> Artifact:
    path = run.settings.work_dir / name
    path.write_text(text, encoding="utf-8")
    digest, size = file_digest(path)
    facts.setdefault("producer", "bodhan")
    return run.ledger.add(
        Artifact(id=artifact_id, path=str(path), kind="text", sha256=digest, size_bytes=size, step_status="ok", **facts)
    )


async def test_missing_source_with_several_files_lists_them(tmp_path: Path) -> None:
    run = Run(tmp_path, [])
    other = add_text(run, "A9", "other.txt", "More notes.", tool="translate_text", language="en")
    run.llm.script = [[("translate_text", {"reason": "translate", "target_language": "ta"})], "done", "done"]
    await run.run()
    guard = next(event for event in run.events() if event["kind"] == "guard")
    assert guard["error"]["code"] == "missing_source"
    assert guard["error"]["message"] == "translate_text needs text_path."
    assert guard["error"]["hint"] == f"Set text_path to one of: A9 {other.path} (text); f1 {run.source} (markdown)."
    assert run.mcp.calls == []


async def test_missing_source_uses_the_only_matching_file(tmp_path: Path) -> None:
    run = Run(tmp_path, [])
    run.llm.script = [[("translate_text", {"reason": "translate", "target_language": "ta"})], "done", "done"]
    await run.run()
    assert run.mcp.calls[0][1]["text_path"] == str(run.source)
    note = next(event for event in run.events() if event["kind"] == "guard")
    assert note["message"] == "text_path was missing; used f1, the only matching file." and "error" not in note


async def test_missing_source_prefers_the_dependency_output(tmp_path: Path) -> None:
    plan = dict(PLAN, steps=[
        {"id": "S1", "intent": "Translate", "why": "x", "tool": "translate_text"},
        {"id": "S2", "intent": "Speak", "why": "x", "tool": "synthesize_speech", "depends_on": ["S1"]},
    ])  # fmt: skip
    run = Run(tmp_path, [], plan=plan)
    run.llm.script = [
        [run.translate_call(plan_step="S1")],
        [("synthesize_speech", {"reason": "speak", "language": "ta", "plan_step": "S2"})],
        "done", "done",
    ]  # fmt: skip
    await run.run()
    translated = run.ledger.get("A1")
    assert translated is not None
    speech = [args for name, args in run.mcp.calls if name == "synthesize_speech"]
    assert speech and speech[0]["text_path"] == translated.path


async def test_inline_text_needs_no_source_path(tmp_path: Path) -> None:
    run = Run(tmp_path, [])
    run.llm.script = [[("translate_text", {"reason": "x", "text": "Hello.", "target_language": "ta"})], "done", "done"]
    await run.run()
    assert "missing_source" not in run.guard_codes()


async def test_english_deliverable_skips_indic_candidates(tmp_path: Path) -> None:
    plan = dict(PLAN, deliverables=[
        {"id": "D1", "kind": "text", "language": "en", "produced_by": "translate_text", "description": "English"},
    ])  # fmt: skip
    run = Run(tmp_path, [], plan=plan)
    hindi = run.settings.work_dir / "talk.txt"
    hindi.write_text("यह एक वाक्य है।", encoding="utf-8")
    digest, size = file_digest(hindi)
    run.ledger.add(Artifact(
        id="A1", path=str(hindi), kind="text", producer="bodhan", tool="transcribe_audio", sha256=digest,
        size_bytes=size, step_status="ok", indic_text=True,
    ))  # fmt: skip
    assert run.controller.candidate(run.controller.plan.deliverables[0]) is None


async def test_repeated_successful_paid_call_is_refused(tmp_path: Path) -> None:
    run = Run(tmp_path, [])
    run.llm.script = [
        [run.translate_call()], [run.translate_call(output_path="again.ta.md")], run.finish_call(),
    ]  # fmt: skip
    result = await run.run()
    assert len(run.mcp.calls) == 1
    guard = next(event for event in run.events() if event["kind"] == "guard")
    assert guard["error"]["code"] == "repeat_refused"
    assert guard["error"]["message"].startswith("The same call already succeeded and made A1 ")
    assert result.stop_reason == "finished"


async def test_changed_arguments_after_success_are_allowed(tmp_path: Path) -> None:
    run = Run(tmp_path, [], plan=MULTI_PLAN)
    run.llm.script = [[run.translate_call()], [run.translate_call(target_language="hi")], run.finish_call()]
    await run.run()
    assert len(run.mcp.calls) == 2


async def test_repeat_allowed_when_earlier_output_failed(tmp_path: Path) -> None:
    run = Run(tmp_path, [])

    def fail_first(messages: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
        run.ledger.set_status("A1", "failed")
        return [run.translate_call()]

    run.llm.script = [[run.translate_call()], fail_first, run.finish_call("A2")]
    await run.run()
    assert len(run.mcp.calls) == 2


async def test_speech_format_follows_the_plan(tmp_path: Path) -> None:
    plan = dict(PLAN, deliverables=[{"id": "D1", "kind": "audio", "language": "en", "description": "English speech"}])
    run = Run(tmp_path, [], plan=plan)
    run.llm.script = [[("synthesize_speech", {"reason": "speak", "text_path": str(run.source), "language": "en"})],
                      "done", "done"]  # fmt: skip
    await run.run()
    assert run.mcp.calls[0][1]["format"] == "mp3"


async def test_speech_format_from_output_path_wins(tmp_path: Path) -> None:
    plan = dict(PLAN, deliverables=[{"id": "D1", "kind": "audio", "language": "en", "description": "English speech"}])
    run = Run(tmp_path, [], plan=plan)
    run.llm.script = [[("synthesize_speech", {"reason": "speak", "text_path": str(run.source), "language": "en",
                                              "output_path": "s.wav"})], "done", "done"]  # fmt: skip
    await run.run()
    assert run.mcp.calls[0][1]["format"] == "wav"


def test_transcription_language_hint_is_hidden() -> None:
    from bodhan_agent.agent.loop import _mcp_parameters

    schema = {"type": "object", "properties": {"audio_path": {}, "language": {}, "overwrite": {}},
              "required": ["audio_path", "language"]}  # fmt: skip
    assert _mcp_parameters("transcribe_audio", schema) == {
        "type": "object", "properties": {"audio_path": {}}, "required": ["audio_path"],
    }  # fmt: skip
    assert "language" in _mcp_parameters("synthesize_speech", schema)["properties"]


async def test_transcription_language_hint_is_dropped(tmp_path: Path) -> None:
    run = Run(tmp_path, [])
    audio = run.settings.work_dir / "talk.wav"
    audio.write_bytes(b"RIFF0000WAVE")
    digest, size = file_digest(audio)
    run.ledger.add(Artifact(id="f2", path=str(audio), kind="audio", producer="user_input", sha256=digest,
                            size_bytes=size, step_status="ok", duration_s=30.0))  # fmt: skip
    call = ("transcribe_audio", {"reason": "x", "audio_path": str(audio), "language": "te"})
    run.llm.script = [[call], "done", "done"]
    await run.run()
    assert run.mcp.calls and "language" not in run.mcp.calls[0][1]


async def test_english_pivot_refused_when_nothing_reads_it(tmp_path: Path) -> None:
    plan = dict(PLAN, deliverables=[{"id": "D1", "kind": "audio", "language": "ta", "description": "Tamil speech"}],
                steps=[{"id": "S1", "intent": "Translate", "why": "x", "tool": "translate_text"}])  # fmt: skip
    run = Run(tmp_path, [], plan=plan)
    hindi = add_text(run, "A9", "talk.txt", "यह एक वाक्य है।", tool="transcribe_audio", indic_text=True)
    run.llm.script = [[run.translate_call(target_language="en", text_path=hindi.path)], "done", "done"]
    await run.run()
    assert run.guard_codes()[0] == "pivot_not_needed" and run.mcp.calls == []
    guard = next(event for event in run.events() if event["kind"] == "guard")
    assert guard["error"]["hint"] == "Call translate_text with target_language 'ta'."


async def test_english_pivot_allowed_when_a_step_reads_it(tmp_path: Path) -> None:
    plan = dict(PLAN, deliverables=[{"id": "D1", "kind": "audio", "language": "ta", "description": "Tamil speech"}],
                steps=[{"id": "S1", "intent": "Read", "why": "x", "tool": "read"}])  # fmt: skip
    run = Run(tmp_path, [], plan=plan)
    run.llm.script = [[run.translate_call(target_language="en")], "done", "done"]
    await run.run()
    assert "pivot_not_needed" not in run.guard_codes()


async def test_artifact_ids_are_accepted_as_paths(tmp_path: Path) -> None:
    run = Run(tmp_path, [])
    finish = (
        "finish",
        {"reason": "done", "answer_en": "Done.", "deliverables": [{"id": "D1", "path": "A1"}, {"id": "D2"}]},
    )
    run.llm.script = [[run.translate_call(text_path="f1")], [finish]]
    result = await run.run()
    assert run.mcp.calls[0][1]["text_path"] == str(run.source)
    assert result.stop_reason == "finished" and result.deliverables["D1"] == "A1"


async def test_speech_source_matches_the_speech_language(tmp_path: Path) -> None:
    plan = dict(PLAN, deliverables=[{"id": "D1", "kind": "audio", "language": "ta", "description": "Tamil speech"}])
    run = Run(tmp_path, [], plan=plan)
    add_text(run, "A8", "talk.txt", "यह एक वाक्य है।", tool="transcribe_audio", indic_text=True, language="hi")
    tamil = add_text(run, "A9", "talk.ta.txt", "இது ஒரு வாக்கியம்.", tool="translate_text", indic_text=True, language="ta")
    run.llm.script = [[("synthesize_speech", {"reason": "speak", "language": "ta"})], "done", "done"]
    await run.run()
    assert run.mcp.calls[0][1]["text_path"] == tamil.path
