from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bodhan_agent.agent.planner import Planner, PlannerError, deliverable_language_problem, extract_json, parse_plan
from bodhan_agent.clients.llm import LlmError
from bodhan_agent.core.budget import BudgetManager
from bodhan_agent.core.config import RunSettings
from bodhan_agent.core.trace import TraceWriter
from bodhan_agent.schemas.ledger import Artifact, Ledger
from bodhan_agent.schemas.llm import LlmReply
from bodhan_agent.schemas.mcp import McpError, McpResult
from bodhan_agent.schemas.plan import Deliverable, Plan
from bodhan_agent.schemas.preflight import InputIssue, PreflightResult
from bodhan_agent.schemas.trace import LlmTokens
from bodhan_agent.tools.base import file_digest

USAGE = {"model_calls": 1, "http_attempts": 1, "estimated_cost_inr": 0.02, "billing_uncertain": False,
         "cache_hits": 0, "possibly_billed_failed_attempts": 0, "latency_ms": 1}  # fmt: skip


def plan_data(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "task_en": "Transcribe the talk and make English subtitles.",
        "goal": "English subtitles for the talk.",
        "deliverables": [{"id": "D1", "kind": "subtitles", "language": "en", "description": "English subtitles"}],
        "steps": [
            {"id": "S1", "intent": "Transcribe", "why": "Needed for the task.", "tool": "transcribe_audio"},
            {
                "id": "S2",
                "intent": "Subtitle",
                "why": "Needed for the task.",
                "tool": "make_subtitles",
                "depends_on": ["S1"],
            },
        ],
        "feasibility": {"status": "feasible"},
    }
    data.update(changes)
    return data


class ScriptedLlm:
    def __init__(self, replies: list[str | Exception]) -> None:
        self.replies = list(replies)
        self.seen: list[list[dict[str, Any]]] = []
        self.tools: list[Any] = []

    async def complete(self, messages: Any, tools: Any = None, *, deadline: float | None = None) -> LlmReply:
        self.seen.append(list(messages))
        self.tools.append(tools)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return LlmReply(content=reply, latency_ms=1, attempts=1, finish_reason="stop",
                        tokens=LlmTokens(prompt=50, completion=30))  # fmt: skip


class FakeMcp:
    def __init__(self, work: Path, text: str = "Translate this note into Tamil.", fail: bool = False) -> None:
        self.work = work
        self.text = text
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def call(self, name: str, args: dict[str, Any], *, deadline: float | None = None) -> McpResult:
        self.calls.append(args)
        if self.fail:
            return McpResult(
                tool=name, ok=False, latency_ms=1,
                error=McpError(code="upstream_error", message="down", details={"usage": USAGE}),
            )  # fmt: skip
        out = self.work / "L0.en-1.txt"
        out.write_text(self.text, encoding="utf-8")
        data = {"output_path": str(out), "target_language": "en", "translated_chars": len(self.text),
                "verification_status": "passed", "usage": USAGE}  # fmt: skip
        return McpResult(tool=name, ok=True, latency_ms=2, data=data)


class Setup:
    def __init__(self, tmp_path: Path, replies: list[str | Exception], limit_inr: float = 20.0, **mcp: Any) -> None:
        self.settings = RunSettings.create("task", [], str(tmp_path / "out"))
        self.settings.work_dir.mkdir(parents=True)
        self.ledger = Ledger()
        self.budget = BudgetManager(limit_inr)
        self.trace = TraceWriter(self.settings.out_dir / "trace.jsonl", cost_source=self.budget.cost_source)
        self.llm = ScriptedLlm(replies)
        self.mcp = FakeMcp(self.settings.work_dir, **mcp)
        self.planner = Planner(
            self.settings,
            self.ledger,
            self.llm,
            self.budget,
            self.trace,
            mcp=self.mcp,  # type: ignore[arg-type]
        )
        self.tmp = tmp_path

    def user_file(self, artifact_id: str, name: str, kind: str, data: bytes = b"x", **facts: Any) -> Artifact:
        path = self.tmp / name
        path.write_bytes(data)
        digest, size = file_digest(path)
        facts.setdefault("step_status", "ok")
        return self.ledger.add(
            Artifact(id=artifact_id, path=str(path), kind=kind, producer="user_input", sha256=digest,
                     size_bytes=size, **facts)
        )  # fmt: skip

    def preflight(self, **changes: Any) -> PreflightResult:
        data: dict[str, Any] = {"task": "task", "task_for_llm": "Make English subtitles for the talk.",
                                "needs_translation": False, "input_ids": [], "literal_ids": []}  # fmt: skip
        data.update(changes)
        return PreflightResult.model_validate(data)

    def events(self) -> list[dict[str, Any]]:
        text = (self.settings.out_dir / "trace.jsonl").read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines()]


def test_extract_json_handles_fences_and_prose() -> None:
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Here is the plan: {"a": {"b": 2}} done') == {"a": {"b": 2}}
    with pytest.raises(ValueError):
        extract_json("no json here")
    with pytest.raises(ValueError):
        extract_json("[1, 2]")


def test_parse_plan_accepts_valid_plan() -> None:
    plan = parse_plan(json.dumps(plan_data()))
    assert [item.id for item in plan.deliverables] == ["D1"]


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("not json", "invalid JSON"),
        ("{broken", "invalid JSON"),
        (json.dumps(plan_data(task_en="तमिल में अनुवाद")), "Indian-language text at task_en"),
        (json.dumps(plan_data(goal="aap kaise hain main theek hoon bahut accha hai")), "romanized"),
        (json.dumps(plan_data(deliverables=[])), "does not match the schema"),
        (json.dumps({"task_en": "x"}), "does not match the schema"),
    ],
)
def test_parse_plan_rejects(text: str, fragment: str) -> None:
    with pytest.raises(ValueError) as error:
        parse_plan(text)
    assert fragment in str(error.value)


@pytest.mark.parametrize(
    ("kind", "language", "supported"),
    [
        ("audio", "ta", True),
        ("audio", "ks-Deva", True),
        ("audio", "fr", False),
        ("subtitles", "bhb", True),
        ("subtitles", "fr", False),
        ("markdown", "mni-Beng", True),
        ("text", "de", False),
        ("answer", "fr", True),
    ],
)
def test_deliverable_language_support(kind: str, language: str, supported: bool) -> None:
    item = Deliverable(id="D1", kind=kind, language=language, description="x")
    assert (deliverable_language_problem(item) is None) is supported


async def test_plan_prompt_and_trace(tmp_path: Path) -> None:
    setup = Setup(tmp_path, [json.dumps(plan_data())])
    audio = setup.user_file("f1", "talk.mp3", "audio", duration_s=60.0)
    outcome = await setup.planner.run(setup.preflight(input_ids=[audio.id]))
    assert outcome.gate.feasible is True
    assert outcome.task_en == "Make English subtitles for the talk."
    assert (outcome.llm_turns, outcome.llm_tokens) == (1, LlmTokens(prompt=50, completion=30))
    system, user = setup.llm.seen[0]
    assert system["content"].startswith("<planner_instructions>")
    assert "<task>\nMake English subtitles for the talk.\n  </task>" in user["content"]
    assert f"- f1: {audio.path} (audio, 60.0 s)" in user["content"]
    assert setup.llm.tools == [None]
    assert [event["kind"] for event in setup.events()] == ["plan", "gate"]


async def test_one_repair_attempt(tmp_path: Path) -> None:
    setup = Setup(tmp_path, ["not json", json.dumps(plan_data())])
    setup.user_file("f1", "talk.mp3", "audio", duration_s=60.0)
    outcome = await setup.planner.run(setup.preflight(input_ids=["f1"]))
    assert outcome.gate.feasible and outcome.llm_turns == 2
    second = setup.llm.seen[1]
    assert second[-2] == {"role": "assistant", "content": "not json"}
    assert second[-1]["content"].startswith("<controller_note>") and "invalid JSON" in second[-1]["content"]
    assert [event["kind"] for event in setup.events()] == ["llm", "plan", "gate"]


async def test_indic_plan_is_redacted_before_repair(tmp_path: Path) -> None:
    bad = json.dumps(plan_data(task_en="तमिल में अनुवाद करो"), ensure_ascii=False)
    setup = Setup(tmp_path, [bad, json.dumps(plan_data())])
    setup.user_file("f1", "talk.mp3", "audio", duration_s=60.0)
    outcome = await setup.planner.run(setup.preflight(input_ids=["f1"]))
    assert outcome.blocked_llm_outputs == 1
    assert "तमिल" not in json.dumps(setup.llm.seen[1], ensure_ascii=False)


async def test_two_bad_plans_raise_planner_error(tmp_path: Path) -> None:
    setup = Setup(tmp_path, ["nope", "still nope"])
    with pytest.raises(PlannerError) as error:
        await setup.planner.run(setup.preflight())
    assert error.value.stop_reason == "planner_error"


async def test_llm_failure_raises_llm_unavailable(tmp_path: Path) -> None:
    setup = Setup(tmp_path, [LlmError("llm_unavailable", "no key")])
    with pytest.raises(PlannerError) as error:
        await setup.planner.run(setup.preflight())
    assert error.value.stop_reason == "llm_unavailable"


async def test_unsupported_language_is_impossible(tmp_path: Path) -> None:
    deliverables = [{"id": "D1", "kind": "audio", "language": "fr", "description": "French speech"}]
    setup = Setup(tmp_path, [json.dumps(plan_data(deliverables=deliverables, steps=[]))])
    outcome = await setup.planner.run(setup.preflight())
    assert (outcome.gate.feasible, outcome.gate.stop_reason) == (False, "unsupported_language")
    assert "'fr'" in (outcome.gate.explanation or "")


@pytest.mark.parametrize("declared", ["feasible", "infeasible"])
async def test_partly_unsupported_language_goes_ahead(tmp_path: Path, declared: str) -> None:
    feasibility = {"status": declared}
    if declared == "infeasible":
        feasibility.update(reason_code="unsupported_language", explanation="No French.")
    deliverables = [
        {"id": "D1", "kind": "text", "language": "ta", "produced_by": "translate_text", "description": "Tamil"},
        {"id": "D2", "kind": "text", "language": "fr", "produced_by": "translate_text", "description": "French"},
        {"id": "D3", "kind": "answer", "language": "en", "description": "What was done"},
    ]
    setup = Setup(tmp_path, [json.dumps(plan_data(feasibility=feasibility, deliverables=deliverables, steps=[]))])
    outcome = await setup.planner.run(setup.preflight())
    assert outcome.gate.feasible is True and outcome.plan is not None
    notes = [note for note in outcome.plan.assumptions if note.startswith("Not produced: D2 needs 'fr'")]
    assert len(notes) == 1
    assert not any("code checks found no such problem" in note for note in outcome.plan.assumptions)


async def test_missing_media_input_is_missing_input(tmp_path: Path) -> None:
    setup = Setup(tmp_path, [json.dumps(plan_data())])
    setup.user_file("f1", "doc.pdf", "pdf", pages=2)
    outcome = await setup.planner.run(setup.preflight(input_ids=["f1"]))
    assert outcome.gate.stop_reason == "missing_input"
    assert "audio or video with sound" in (outcome.gate.explanation or "")


async def test_video_without_audio_is_not_usable(tmp_path: Path) -> None:
    setup = Setup(tmp_path, [json.dumps(plan_data())])
    setup.user_file("f1", "silent.mp4", "video", step_status="failed", note="has no audio track")
    outcome = await setup.planner.run(setup.preflight(input_ids=["f1"]))
    assert outcome.gate.stop_reason == "missing_input"


async def test_missing_files_reported(tmp_path: Path) -> None:
    issue = InputIssue(path="/nope/talk.mp3", code="missing_input", message="not found")
    setup = Setup(tmp_path, [json.dumps(plan_data(steps=[]))])
    outcome = await setup.planner.run(setup.preflight(issues=[issue]))
    assert outcome.gate.stop_reason == "missing_input"
    assert "/nope/talk.mp3" in (outcome.gate.explanation or "")


async def test_budget_shortfall_is_budget_insufficient(tmp_path: Path) -> None:
    setup = Setup(tmp_path, [json.dumps(plan_data())], limit_inr=0.001)
    setup.user_file("f1", "talk.mp3", "audio", duration_s=3600.0)
    outcome = await setup.planner.run(setup.preflight(input_ids=["f1"]))
    assert (outcome.gate.feasible, outcome.gate.stop_reason) == (False, "budget_insufficient")
    assert outcome.gate.minimum_cost is not None and outcome.gate.minimum_cost.known_inr > 0.001


async def test_confirmed_infeasible_plan_is_kept(tmp_path: Path) -> None:
    feasibility = {"status": "infeasible", "reason_code": "beyond_tools", "explanation": "Video editing is needed."}
    setup = Setup(tmp_path, [json.dumps(plan_data(feasibility=feasibility))])
    outcome = await setup.planner.run(setup.preflight())
    assert (outcome.gate.stop_reason, outcome.gate.explanation) == ("beyond_tools", "Video editing is needed.")


async def test_unconfirmed_language_claim_is_overridden(tmp_path: Path) -> None:
    feasibility = {"status": "infeasible", "reason_code": "unsupported_language", "explanation": "Odia is rare."}
    deliverables = [
        {"id": "D1", "kind": "markdown", "language": "or", "produced_by": "translate_text", "description": "Odia notes"}
    ]
    setup = Setup(tmp_path, [json.dumps(plan_data(feasibility=feasibility, deliverables=deliverables, steps=[]))])
    outcome = await setup.planner.run(setup.preflight())
    assert outcome.gate.feasible is True
    assert outcome.plan is not None
    assert any("code checks found no such problem" in note for note in outcome.plan.assumptions)


async def test_confirmed_language_claim_is_impossible(tmp_path: Path) -> None:
    feasibility = {"status": "infeasible", "reason_code": "unsupported_language", "explanation": "No French."}
    deliverables = [{"id": "D1", "kind": "audio", "language": "fr", "description": "French speech"}]
    setup = Setup(tmp_path, [json.dumps(plan_data(feasibility=feasibility, deliverables=deliverables, steps=[]))])
    outcome = await setup.planner.run(setup.preflight())
    assert outcome.gate.stop_reason == "unsupported_language"
    assert "No French." in (outcome.gate.explanation or "") and "'fr'" in (outcome.gate.explanation or "")


async def test_infeasible_without_deliverables(tmp_path: Path) -> None:
    feasibility = {"status": "infeasible", "reason_code": "missing_input", "explanation": "No file was given."}
    setup = Setup(tmp_path, [json.dumps(plan_data(feasibility=feasibility, deliverables=[], steps=[]))])
    outcome = await setup.planner.run(setup.preflight())
    assert outcome.gate.stop_reason == "missing_input"


async def test_non_english_answer_gets_note(tmp_path: Path) -> None:
    deliverables = [{"id": "D1", "kind": "answer", "language": "hi", "description": "Answer"}]
    setup = Setup(tmp_path, [json.dumps(plan_data(deliverables=deliverables, steps=[]))])
    outcome = await setup.planner.run(setup.preflight())
    assert outcome.gate.feasible and outcome.plan is not None
    assert any("answered in English" in note for note in outcome.plan.assumptions)


async def test_missing_tools_become_assumption(tmp_path: Path) -> None:
    setup = Setup(tmp_path, [json.dumps(plan_data(steps=[]))])
    outcome = await setup.planner.run(setup.preflight(missing_tools=["ffprobe"]))
    assert outcome.plan is not None and any("ffprobe" in note for note in outcome.plan.assumptions)


async def test_indic_task_is_translated_by_bodhan(tmp_path: Path) -> None:
    setup = Setup(tmp_path, [json.dumps(plan_data(steps=[]))])
    original = setup.user_file("L0", "L0.txt", "text", "इस नोट का तमिल में अनुवाद करें".encode(), indic_text=True,
                               script="Devanagari", chars=30)  # fmt: skip
    setup.user_file("L1", "L1.txt", "text", "नमस्ते".encode(), indic_text=True)
    preflight = setup.preflight(
        task_for_llm="[L0: the task is written in Devanagari script]", needs_translation=True,
        instruction_id="L0", literal_ids=["L1"],
    )  # fmt: skip
    outcome = await setup.planner.run(preflight)
    assert setup.mcp.calls == [{"text_path": original.path, "target_language": "en", "overwrite": False}]
    assert outcome.task_en == "Translate this note into Tamil. Quoted text from the task is saved as L1."
    assert outcome.instruction_en_id == "A1"
    english = setup.ledger.get("A1")
    assert english is not None and (english.producer, english.sources, english.language) == ("bodhan", ["L0"], "en")
    assert setup.budget.spent_inr == 0.02
    assert [event["kind"] for event in setup.events()] == ["tool", "plan", "gate"]
    assert "Translate this note into Tamil." in setup.llm.seen[0][1]["content"]


async def test_indic_in_translated_task_is_redacted(tmp_path: Path) -> None:
    setup = Setup(tmp_path, [json.dumps(plan_data(steps=[]))], text="Say नमस्ते to everyone.")
    setup.user_file("L0", "L0.txt", "text", "सबको नमस्ते कहो".encode(), indic_text=True)
    preflight = setup.preflight(task_for_llm="[L0]", needs_translation=True, instruction_id="L0")
    outcome = await setup.planner.run(preflight)
    assert "नमस्ते" not in outcome.task_en


async def test_failed_task_translation_is_planner_error(tmp_path: Path) -> None:
    setup = Setup(tmp_path, [], fail=True)
    setup.user_file("L0", "L0.txt", "text", "सबको नमस्ते कहो".encode(), indic_text=True)
    with pytest.raises(PlannerError) as error:
        await setup.planner.run(setup.preflight(task_for_llm="[L0]", needs_translation=True, instruction_id="L0"))
    assert error.value.stop_reason == "planner_error"
    assert setup.budget.spent_inr == 0.02
    assert setup.budget.open_reservations == []


async def test_task_translation_over_budget(tmp_path: Path) -> None:
    setup = Setup(tmp_path, [], limit_inr=0.0)
    setup.user_file("L0", "L0.txt", "text", "सबको नमस्ते कहो".encode(), indic_text=True)
    with pytest.raises(PlannerError) as error:
        await setup.planner.run(setup.preflight(task_for_llm="[L0]", needs_translation=True, instruction_id="L0"))
    assert error.value.stop_reason == "budget_insufficient"
    assert setup.mcp.calls == []


async def test_indic_task_without_mcp(tmp_path: Path) -> None:
    setup = Setup(tmp_path, [])
    setup.planner.mcp = None
    setup.user_file("L0", "L0.txt", "text", "सबको नमस्ते कहो".encode(), indic_text=True)
    with pytest.raises(PlannerError) as error:
        await setup.planner.run(setup.preflight(task_for_llm="[L0]", needs_translation=True, instruction_id="L0"))
    assert error.value.stop_reason == "missing_input"


def test_plan_outcome_requires_plan_when_feasible() -> None:
    from bodhan_agent.schemas.plan import GateResult, PlanOutcome

    with pytest.raises(ValueError):
        PlanOutcome(task_en="x", gate=GateResult(feasible=True))
    with pytest.raises(ValueError):
        GateResult(feasible=False, stop_reason="missing_input")
    PlanOutcome(task_en="x", plan=Plan.model_validate(plan_data()), gate=GateResult(feasible=True))


async def test_plan_event_records_whole_plan(tmp_path: Path) -> None:
    setup = Setup(tmp_path, [json.dumps(plan_data(assumptions=["The talk is in Hindi."]))])
    setup.user_file("f1", "talk.mp3", "audio", duration_s=60.0)
    await setup.planner.run(setup.preflight(input_ids=["f1"]))
    recorded = setup.events()[0]["result_summary"]["plan"]
    assert recorded["assumptions"] == ["The talk is in Hindi."]
    assert [step["id"] for step in recorded["steps"]] == ["S1", "S2"]
    assert recorded["deliverables"][0]["kind"] == "subtitles"
    assert recorded["feasibility"]["status"] == "feasible"
    assert "depends_on" not in recorded["steps"][0] and recorded["steps"][1]["depends_on"] == ["S1"]
    assert "condition" not in recorded["steps"][0]
    assert "constraints" not in recorded["deliverables"][0]


def test_parse_plan_rejects_invented_tools() -> None:
    data = plan_data(steps=[{"id": "S1", "intent": "Summarise", "why": "x", "tool": "text_summarizer"}])
    with pytest.raises(ValueError) as error:
        parse_plan(json.dumps(data))
    assert "S1 uses 'text_summarizer'" in str(error.value) and "write" in str(error.value)


def test_parse_plan_accepts_local_tools_and_no_tool() -> None:
    steps = [
        {"id": "S1", "intent": "Read", "why": "x", "tool": "read"},
        {"id": "S2", "intent": "Write", "why": "x", "tool": "write"},
        {"id": "S3", "intent": "Think", "why": "x"},
        {"id": "S4", "intent": "Subtitle", "why": "x", "tool": "make_subtitles"},
    ]
    assert len(parse_plan(json.dumps(plan_data(steps=steps))).steps) == 4


async def test_invented_tool_gets_one_repair(tmp_path: Path) -> None:
    bad = plan_data(steps=[{"id": "S1", "intent": "Summarise", "why": "x", "tool": "text_summarizer"}])
    setup = Setup(tmp_path, [json.dumps(bad), json.dumps(plan_data(steps=[]))])
    outcome = await setup.planner.run(setup.preflight())
    assert outcome.llm_turns == 2
    assert "tools that do not exist" in setup.llm.seen[1][-1]["content"]


def test_parse_plan_requires_produced_by_for_text() -> None:
    data = plan_data(deliverables=[{"id": "D1", "kind": "text", "language": "en", "description": "Transcript"}])
    with pytest.raises(ValueError, match="D1 need produced_by"):
        parse_plan(json.dumps(data))


def test_parse_plan_accepts_string_input_notes() -> None:
    assert parse_plan(json.dumps(plan_data(input_notes="Language unknown."))).input_notes == ["Language unknown."]


def test_parse_plan_rejects_deliverable_without_a_step() -> None:
    deliverables = [
        {"id": "D1", "kind": "subtitles", "language": "en", "description": "English subtitles"},
        {"id": "D2", "kind": "audio", "language": "en", "description": "English speech"},
    ]
    with pytest.raises(ValueError, match=r"D2 needs a synthesize_speech step"):
        parse_plan(json.dumps(plan_data(deliverables=deliverables)))


def test_parse_plan_leaves_stepless_and_unsupported_plans_to_the_gate() -> None:
    assert parse_plan(json.dumps(plan_data(steps=[]))).steps == []
    french = [{"id": "D1", "kind": "audio", "language": "fr", "description": "French speech"}]
    assert parse_plan(json.dumps(plan_data(deliverables=french))).deliverables[0].language == "fr"


async def test_missing_step_gets_one_repair(tmp_path: Path) -> None:
    audio = [{"id": "D1", "kind": "audio", "language": "en", "description": "English speech"}]
    bad = plan_data(deliverables=audio)
    good = plan_data(
        deliverables=audio,
        steps=[
            *bad["steps"],
            {"id": "S3", "intent": "Speak", "why": "x", "tool": "synthesize_speech", "depends_on": ["S2"]},
        ],
    )
    setup = Setup(tmp_path, [json.dumps(bad), json.dumps(good)])
    outcome = await setup.planner.run(setup.preflight())
    assert outcome.plan is not None and outcome.llm_turns == 2
