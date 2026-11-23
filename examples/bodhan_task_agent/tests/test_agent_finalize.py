from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from bodhan_agent.core.budget import BudgetManager
from bodhan_agent.core.config import RunSettings
from bodhan_agent.core.trace import TraceWriter
from bodhan_agent.pipeline.report import Finalizer, copy_deliverable
from bodhan_agent.schemas.budget import Quote
from bodhan_agent.schemas.ledger import Artifact, Ledger
from bodhan_agent.schemas.loop import FinishProposal, LoopResult
from bodhan_agent.schemas.plan import GateResult, Plan, PlanOutcome
from bodhan_agent.schemas.preflight import InputIssue, PreflightResult
from bodhan_agent.schemas.report import RunReport, VerificationCheck
from bodhan_agent.schemas.trace import LlmTokens
from bodhan_agent.schemas.verify import Verdict
from bodhan_agent.tools.base import ToolContext, register, write_new

LABEL = "AI-generated speech (Bodhan Indic-Speak)"
TAMIL = "தாவரங்கள் உணவு தயாரிக்கின்றன."
STARTED = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)
PASSED = [VerificationCheck(name="file_intact", status="passed")]


def plan(*deliverables: dict[str, Any], assumptions: list[str] | None = None) -> Plan:
    items = list(deliverables) or [
        {"id": "D1", "kind": "markdown", "language": "ta", "description": "Tamil notes"},
        {"id": "D2", "kind": "answer", "language": "en", "description": "Summary"},
    ]
    return Plan.model_validate(
        {"task_en": "Translate the notes.", "goal": "x", "assumptions": assumptions or [],
         "deliverables": items, "feasibility": {"status": "feasible"}}
    )  # fmt: skip


class Setup:
    def __init__(self, tmp_path: Path, limit_inr: float = 20.0) -> None:
        self.settings = RunSettings.create("Translate my notes", [], str(tmp_path / "out"))
        self.settings.work_dir.mkdir(parents=True)
        self.ledger = Ledger()
        self.budget = BudgetManager(limit_inr)
        self.trace = TraceWriter(self.settings.out_dir / "trace.jsonl", cost_source=self.budget.cost_source)
        self.ctx = ToolContext(settings=self.settings, ledger=self.ledger)

    def artifact(self, name: str, data: str | bytes = TAMIL, kind: str = "markdown", **facts: Any) -> Artifact:
        raw = data.encode("utf-8") if isinstance(data, str) else data
        facts.setdefault("tool", "translate_text")
        artifact = register(self.ctx, write_new(self.settings.work_dir / name, raw), kind, "bodhan", **facts)
        self.ledger.set_status(artifact.id, "ok")
        return artifact

    def outcome(self, the_plan: Plan | None = None, **changes: Any) -> PlanOutcome:
        data: dict[str, Any] = {
            "task_en": "Translate the notes.", "plan": the_plan or plan(), "gate": GateResult(feasible=True),
            "llm_turns": 1, "llm_tokens": LlmTokens(prompt=100, completion=50),
        }  # fmt: skip
        data.update(changes)
        return PlanOutcome(**data)

    def finalizer(self, **options: Any) -> Finalizer:
        return Finalizer(self.settings, self.ledger, self.budget, self.trace, STARTED, options.pop("elapsed", 42.0),
                         **options)  # fmt: skip

    def read_report(self) -> dict[str, Any]:
        return json.loads((self.settings.out_dir / "report.json").read_text(encoding="utf-8"))

    def result_md(self) -> str:
        return (self.settings.out_dir / "result.md").read_text(encoding="utf-8")


def loop(**changes: Any) -> LoopResult:
    data: dict[str, Any] = {
        "stop_reason": "finished",
        "finish": FinishProposal(answer_en="Translated the notes to Tamil.", deliverables={"D1": "A1", "D2": None}),
        "deliverables": {"D1": "A1", "D2": None},
        "verdicts": {"D1": Verdict(status="verified", checks=PASSED), "D2": Verdict(status="verified")},
        "steps_used": 2,
        "llm_turns": 2,
        "llm_tokens": LlmTokens(prompt=300, completion=60),
    }
    data.update(changes)
    return LoopResult(**data)


async def test_completed_run(tmp_path: Path) -> None:
    setup = Setup(tmp_path)
    setup.artifact("notes.ta.md", language="ta", indic_text=True)
    report = await setup.finalizer(outcome=setup.outcome(), loop=loop()).write()
    assert (report.status, report.stop_reason, report.explanation) == ("completed", "finished", None)
    d1 = report.deliverables[0]
    assert (d1.status, d1.path, d1.artifact_id, d1.producer, d1.tool) == (
        "verified", "artifacts/notes.ta.md", "A1", "bodhan", "translate_text",
    )  # fmt: skip
    assert d1.checks[-1].name == "copied" and d1.checks[-1].status == "passed"
    copied = setup.settings.artifacts_dir / "notes.ta.md"
    assert copied.read_text(encoding="utf-8") == TAMIL
    assert Path(setup.ledger.artifacts["A1"].path).exists()
    assert RunReport.model_validate(setup.read_report()).status == "completed"


async def test_tokens_steps_and_budgets(tmp_path: Path) -> None:
    setup = Setup(tmp_path)
    setup.artifact("notes.ta.md", language="ta", indic_text=True)
    report = await setup.finalizer(outcome=setup.outcome(), loop=loop()).write()
    assert report.cost.llm_tokens == LlmTokens(prompt=400, completion=110)
    assert report.cost.llm_usage_complete is True
    assert (report.budgets.steps.used, report.budgets.steps.limit) == (2, 30)
    assert (report.budgets.llm_turns.used, report.budgets.llm_turns.limit) == (2, 60)
    assert (report.budgets.time_s.used, report.budgets.time_s.limit) == (42.0, 290.0)
    assert (report.budgets.cost_inr.used, report.budgets.cost_inr.limit) == (0.0, 20.0)
    assert report.started_at == STARTED and report.finished_at >= STARTED


async def test_missing_usage_marks_incomplete(tmp_path: Path) -> None:
    setup = Setup(tmp_path)
    setup.artifact("notes.ta.md", language="ta", indic_text=True)
    report = await setup.finalizer(outcome=setup.outcome(), loop=loop(llm_tokens=LlmTokens())).write()
    assert report.cost.llm_usage_complete is False
    assert "incomplete: some calls did not report usage" in setup.result_md()


async def test_partial_run_explains_missing(tmp_path: Path) -> None:
    setup = Setup(tmp_path)
    setup.artifact("notes.ta.md", language="ta", indic_text=True)
    the_plan = plan(
        {"id": "D1", "kind": "markdown", "language": "ta", "description": "Tamil notes"},
        {"id": "D2", "kind": "audio", "language": "ta", "description": "Tamil speech"},
    )
    result = loop(
        deliverables={"D1": "A1", "D2": None},
        verdicts={"D1": Verdict(status="verified", checks=PASSED), "D2": Verdict(status="missing")},
    )
    report = await setup.finalizer(outcome=setup.outcome(the_plan), loop=result).write()
    assert report.status == "partial"
    assert report.explanation == "D2 (audio) was not produced."
    assert [item.status for item in report.deliverables] == ["verified", "missing"]
    assert report.deliverables[1].path is None and report.deliverables[1].artifact_id is None


async def test_failed_deliverable_is_not_copied(tmp_path: Path) -> None:
    setup = Setup(tmp_path)
    setup.artifact("bad.ta.md", "Plain English.", language="ta")
    failed = Verdict(status="failed", checks=[VerificationCheck(name="target_script", status="failed")])
    result = loop(verdicts={"D1": failed, "D2": Verdict(status="verified")})
    report = await setup.finalizer(outcome=setup.outcome(), loop=result).write()
    assert report.deliverables[0].status == "failed" and report.deliverables[0].path is None
    assert not setup.settings.artifacts_dir.exists() or list(setup.settings.artifacts_dir.iterdir()) == []
    assert "D1 (markdown) failed verification: target_script." in (report.explanation or "")
    assert report.status == "partial"


async def test_warning_deliverable_is_copied(tmp_path: Path) -> None:
    setup = Setup(tmp_path)
    setup.artifact("notes.ta.md", language="ta", indic_text=True)
    warning = Verdict(status="warning", checks=[VerificationCheck(name="length_coverage", status="warning")])
    report = await setup.finalizer(outcome=setup.outcome(), loop=loop(verdicts={"D1": warning, "D2": Verdict(
        status="verified")})).write()  # fmt: skip
    assert (report.deliverables[0].status, report.deliverables[0].path) == ("warning", "artifacts/notes.ta.md")
    assert report.status == "completed"
    assert "  - warning: length_coverage" in setup.result_md()


async def test_audio_keeps_label(tmp_path: Path) -> None:
    setup = Setup(tmp_path)
    setup.artifact("speech.ta.mp3", b"ID3", kind="audio", tool="synthesize_speech", language="ta", duration_s=5.0)
    the_plan = plan({"id": "D1", "kind": "audio", "language": "ta", "description": "Tamil speech"})
    result = loop(deliverables={"D1": "A1"}, verdicts={"D1": Verdict(status="verified", checks=PASSED, label=LABEL)})
    report = await setup.finalizer(outcome=setup.outcome(the_plan), loop=result).write()
    assert report.deliverables[0].label == LABEL
    assert f"artifacts/speech.ta.mp3) — {LABEL}" in setup.result_md()


async def test_copy_never_overwrites(tmp_path: Path) -> None:
    setup = Setup(tmp_path)
    setup.settings.artifacts_dir.mkdir(parents=True)
    (setup.settings.artifacts_dir / "notes.ta.md").write_text("older file", encoding="utf-8")
    setup.artifact("notes.ta.md", language="ta", indic_text=True)
    report = await setup.finalizer(outcome=setup.outcome(), loop=loop()).write()
    assert report.deliverables[0].path == "artifacts/notes.ta-2.md"
    assert (setup.settings.artifacts_dir / "notes.ta.md").read_text(encoding="utf-8") == "older file"


def test_copy_keeps_file_already_in_artifacts(tmp_path: Path) -> None:
    setup = Setup(tmp_path)
    setup.settings.artifacts_dir.mkdir(parents=True)
    path = write_new(setup.settings.artifacts_dir / "answer.txt", b"Hello.")
    artifact = register(setup.ctx, path, "text", "llm_english", language="en")
    relative, check = copy_deliverable(setup.settings, artifact)
    assert (relative, check.status) == ("artifacts/answer.txt", "passed")
    assert sorted(item.name for item in setup.settings.artifacts_dir.iterdir()) == ["answer.txt"]


def test_copy_detects_changed_file(tmp_path: Path) -> None:
    setup = Setup(tmp_path)
    artifact = setup.artifact("notes.ta.md", language="ta", indic_text=True)
    Path(artifact.path).write_text("tampered", encoding="utf-8")
    relative, check = copy_deliverable(setup.settings, artifact)
    assert relative is None and check.status == "failed"


async def test_copy_failure_fails_deliverable(tmp_path: Path) -> None:
    setup = Setup(tmp_path)
    artifact = setup.artifact("notes.ta.md", language="ta", indic_text=True)
    Path(artifact.path).unlink()
    report = await setup.finalizer(outcome=setup.outcome(), loop=loop()).write()
    assert report.deliverables[0].status == "failed"
    assert report.deliverables[0].checks[-1].name == "copied"


async def test_usable_verdict_without_artifact_fails(tmp_path: Path) -> None:
    setup = Setup(tmp_path)
    report = await setup.finalizer(outcome=setup.outcome(), loop=loop(deliverables={"D1": None, "D2": None})).write()
    assert report.deliverables[0].status == "failed"


async def test_gate_refusal_reports_impossible(tmp_path: Path) -> None:
    setup = Setup(tmp_path)
    gate = GateResult(feasible=False, stop_reason="unsupported_language", explanation="D1 needs 'fr'.")
    report = await setup.finalizer(outcome=setup.outcome(gate=gate)).write()
    assert (report.status, report.stop_reason) == ("impossible", "unsupported_language")
    assert report.explanation is not None and report.explanation.startswith("D1 needs 'fr'.")
    assert [item.status for item in report.deliverables] == ["missing", "missing"]
    assert report.budgets.steps.used == 0


async def test_budget_shortfall_reports_failed(tmp_path: Path) -> None:
    setup = Setup(tmp_path)
    gate = GateResult(feasible=False, stop_reason="budget_insufficient", explanation="Needs Rs 30.")
    report = await setup.finalizer(outcome=setup.outcome(gate=gate)).write()
    assert (report.status, report.stop_reason) == ("failed", "budget_insufficient")


async def test_planner_error_without_plan(tmp_path: Path) -> None:
    setup = Setup(tmp_path)
    report = await setup.finalizer(stop_reason="planner_error", explanation="No usable plan.").write()
    assert (report.status, report.stop_reason, report.planned, report.deliverables) == (
        "failed", "planner_error", [], [],
    )  # fmt: skip
    assert report.explanation == "No usable plan."
    assert "None: the run stopped before a plan was made." in setup.result_md()
    assert "No English answer was produced." in setup.result_md()


async def test_explicit_stop_reason_wins(tmp_path: Path) -> None:
    setup = Setup(tmp_path)
    setup.artifact("notes.ta.md", language="ta", indic_text=True)
    report = await setup.finalizer(outcome=setup.outcome(), loop=loop(), stop_reason="deadline").write()
    assert report.stop_reason == "deadline"


async def test_loop_explanation_is_used(tmp_path: Path) -> None:
    setup = Setup(tmp_path)
    result = loop(stop_reason="llm_unavailable", explanation="The LLM could not be reached.", finish=None,
                  verdicts={"D1": Verdict(status="missing"), "D2": Verdict(status="failed", checks=[
                      VerificationCheck(name="answer_english", status="failed")])},
                  deliverables={"D1": None, "D2": None})  # fmt: skip
    report = await setup.finalizer(outcome=setup.outcome(), loop=result).write()
    assert report.status == "failed"
    assert report.explanation is not None and report.explanation.startswith("The LLM could not be reached.")


async def test_assumptions_and_warnings(tmp_path: Path) -> None:
    setup = Setup(tmp_path)
    artifact = setup.artifact("notes.ta.md", language="ta", indic_text=True)
    setup.ledger.set_status(artifact.id, "warning", ["Low confidence on page 2."])
    issue = InputIssue(path="/x/missing.pdf", code="missing_input", message="missing.pdf was not found.")
    preflight = PreflightResult(task="t", task_for_llm="t", needs_translation=False, issues=[issue])
    the_plan = plan(assumptions=["Notes are English.", "Formal Tamil."])
    finish = FinishProposal(answer_en="Done.", assumptions=["Formal Tamil.", "Kept headings."],
                            deliverables={"D1": "A1", "D2": None})  # fmt: skip
    report = await setup.finalizer(
        preflight=preflight, outcome=setup.outcome(the_plan), loop=loop(finish=finish)
    ).write()
    assert report.assumptions == ["Notes are English.", "Formal Tamil.", "Kept headings."]
    assert report.warnings == ["missing.pdf was not found.", "D1: Low confidence on page 2."]
    text = setup.result_md()
    assert "## Assumptions" in text and "- Kept headings." in text
    assert "- D1: Low confidence on page 2." in text


async def test_unread_ocr_pages_are_warned(tmp_path: Path) -> None:
    setup = Setup(tmp_path)
    setup.artifact("notes.ta.md", language="ta", indic_text=True)
    source = setup.artifact("book.pdf", b"%PDF-1.7")
    ocr = setup.artifact("book.md", tool="ocr_document", sources=[source.id])
    ocr.tool_checks = {"pages_read": 15, "unread_pages": [5, 8]}
    report = await setup.finalizer(outcome=setup.outcome(), loop=loop()).write()
    warning = "OCR could not read page(s) 5, 8 of book.pdf; results built from it leave them out."
    assert report.warnings == [warning]
    assert f"- {warning}" in setup.result_md()


async def test_compliance_and_missed_targets(tmp_path: Path) -> None:
    setup = Setup(tmp_path)
    setup.artifact("notes.ta.md", language="ta", indic_text=True)
    result = loop(redactions=3, blocked_llm_outputs=1, injection_flags=2,
                  missed_targets=["no_waste: translate_text was retried after an uncertain timeout"])  # fmt: skip
    report = await setup.finalizer(outcome=setup.outcome(blocked_llm_outputs=1), loop=result, elapsed=400.0).write()
    assert report.compliance.model_dump() == {
        "indic_redactions": 3, "blocked_llm_outputs": 2, "injection_flags": 2, "media_to_llm": 0,
        "non_bodhan_ml": 0, "uncertain_retries": 1,
    }  # fmt: skip
    assert [(item.target, item.detail) for item in report.missed_targets] == [
        ("no_waste", "translate_text was retried after an uncertain timeout"),
        ("latency", "This single-step task took 400 s; the target is under 30 s."),
    ]
    text = setup.result_md()
    assert "2 instruction-like line(s) were found in input content" in text
    assert "- latency: This single-step task took 400 s; the target is under 30 s." in text


async def test_cost_includes_open_reservations(tmp_path: Path) -> None:
    setup = Setup(tmp_path)
    setup.artifact("notes.ta.md", language="ta", indic_text=True)
    settled = await setup.budget.reserve(Quote(tool="translate_text", amount_inr=0.5, bound="upper"))
    assert settled is not None
    await setup.budget.settle(settled, 0.2)
    assert await setup.budget.reserve(Quote(tool="ocr_document", amount_inr=0.3, bound="upper")) is not None
    report = await setup.finalizer(outcome=setup.outcome(), loop=loop()).write()
    assert (report.cost.bodhan_inr, report.cost.reserved_unsettled_inr) == (0.2, 0.3)
    assert report.budgets.cost_inr.used == pytest.approx(0.5)
    assert report.cost.billing_uncertain is True
    assert "their full reservation is counted" in " ".join(report.warnings)
    assert "(plus Rs 0.30 unsettled) — some charges are uncertain" in setup.result_md()


async def test_budget_overrun_is_missed_target(tmp_path: Path) -> None:
    setup = Setup(tmp_path, limit_inr=0.1)
    setup.artifact("notes.ta.md", language="ta", indic_text=True)
    reservation = await setup.budget.reserve(Quote(tool="translate_text", amount_inr=0.1, bound="upper"))
    assert reservation is not None
    await setup.budget.settle(reservation, 0.25)
    report = await setup.finalizer(outcome=setup.outcome(), loop=loop()).write()
    assert any(item.target == "budget" for item in report.missed_targets)


async def test_answer_is_withheld_when_not_verified(tmp_path: Path) -> None:
    setup = Setup(tmp_path)
    setup.artifact("notes.ta.md", language="ta", indic_text=True)
    failed = Verdict(status="failed", checks=[VerificationCheck(name="answer_english", status="failed")])
    finish = FinishProposal(answer_en="हो गया", deliverables={"D1": "A1", "D2": None})
    result = loop(finish=finish, verdicts={"D1": Verdict(status="verified", checks=PASSED), "D2": failed})
    await setup.finalizer(outcome=setup.outcome(), loop=result).write()
    text = setup.result_md()
    assert "withheld because it failed verification" in text
    assert "हो गया" not in text


async def test_result_md_layout_and_no_indic(tmp_path: Path) -> None:
    setup = Setup(tmp_path)
    setup.artifact("notes.ta.md", language="ta", indic_text=True)
    await setup.finalizer(outcome=setup.outcome(), loop=loop()).write()
    text = setup.result_md()
    assert text.startswith("# Result: completed\n\nTranslated the notes to Tamil.\n")
    assert "- **D1** (markdown, ta): verified — [artifacts/notes.ta.md](artifacts/notes.ta.md)" in text
    assert "- LLM usage (not part of the Bodhan budget): 400 prompt + 110 completion tokens" in text
    assert "- Steps: 2 of 30; time: 42 s of 290 s" in text
    assert "Bodhan models" in text
    assert "## What did not work" not in text
    assert "தா" not in text


async def test_fallback_report_on_build_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup = Setup(tmp_path)
    finalizer = setup.finalizer(outcome=setup.outcome(), loop=loop())

    def broken() -> RunReport:
        raise ValueError("bad deliverable")

    monkeypatch.setattr(finalizer, "build", broken)
    report = await finalizer.write()
    assert (report.status, report.stop_reason) == ("failed", "internal_error")
    assert report.explanation == "The report could not be built: ValueError: bad deliverable"
    assert [(item.id, item.status) for item in report.deliverables] == [("D1", "missing"), ("D2", "missing")]
    assert setup.read_report()["stop_reason"] == "internal_error"
    events = [json.loads(line) for line in (setup.settings.out_dir / "trace.jsonl").read_text().splitlines()]
    assert [event["message"] for event in events][-2:] == [
        "Report fell back to internal_error.", "status=failed, stop_reason=internal_error",
    ]  # fmt: skip


async def test_rewrites_existing_report_files(tmp_path: Path) -> None:
    setup = Setup(tmp_path)
    (setup.settings.out_dir / "report.json").write_text("old", encoding="utf-8")
    (setup.settings.out_dir / "result.md").write_text("old", encoding="utf-8")
    setup.artifact("notes.ta.md", language="ta", indic_text=True)
    await setup.finalizer(outcome=setup.outcome(), loop=loop()).write()
    assert setup.read_report()["status"] == "completed"
    assert setup.result_md().startswith("# Result: completed")


async def test_final_trace_event(tmp_path: Path) -> None:
    setup = Setup(tmp_path)
    setup.artifact("notes.ta.md", language="ta", indic_text=True)
    await setup.finalizer(outcome=setup.outcome(), loop=loop()).write()
    event = json.loads((setup.settings.out_dir / "trace.jsonl").read_text().splitlines()[-1])
    assert (event["phase"], event["kind"]) == ("finalize", "stop")
    assert event["result_summary"] == {"status": "completed", "deliverables": {"D1": "verified", "D2": "verified"},
                                       "bodhan_inr": 0.0}  # fmt: skip
    assert event["artifacts"] == ["A1"]


def record(setup: Setup, tool: str, outcome: str = "ok", artifacts: list[str] | None = None, cost: float = 0.0,
           error_code: str | None = None) -> None:  # fmt: skip
    from bodhan_agent.schemas.ledger import ActionRecord

    setup.ledger.record(
        ActionRecord(seq=len(setup.ledger.actions), tool=tool, args_digest="0" * 64, outcome=outcome,  # type: ignore[arg-type]
                     error_code=error_code, artifacts=artifacts or [], cost_inr=cost)
    )  # fmt: skip


async def test_result_md_lists_what_was_done(tmp_path: Path) -> None:
    setup = Setup(tmp_path)
    setup.artifact("notes.ta.md", language="ta", indic_text=True)
    record(setup, "translate_text", artifacts=["A1"], cost=0.0012)
    record(setup, "synthesize_speech", outcome="error", error_code="path_not_allowed")
    record(setup, "finish")
    await setup.finalizer(outcome=setup.outcome(), loop=loop()).write()
    text = setup.result_md()
    section = text.split("## What was done\n\n", 1)[1].split("\n\n", 1)[0]
    assert section.splitlines() == [
        "1. translate_text: ok -> A1, Rs 0.0012",
        "2. synthesize_speech: error (path_not_allowed)",
    ]


async def test_no_steps_section_without_actions(tmp_path: Path) -> None:
    setup = Setup(tmp_path)
    await setup.finalizer(stop_reason="planner_error", explanation="No plan.").write()
    assert "## What was done" not in setup.result_md()


@pytest.mark.parametrize(
    ("steps", "elapsed", "detail"),
    [
        (1, 29.0, None),
        (1, 31.0, "This single-step task took 31 s; the target is under 30 s."),
        (3, 299.0, None),
        (3, 301.0, "This multi-step task took 301 s; the target is under 300 s."),
    ],
)
async def test_latency_targets(tmp_path: Path, steps: int, elapsed: float, detail: str | None) -> None:
    setup = Setup(tmp_path)
    setup.artifact("notes.ta.md", language="ta", indic_text=True)
    for _ in range(steps):
        record(setup, "translate_text")
    record(setup, "finish")
    report = await setup.finalizer(outcome=setup.outcome(), loop=loop(), elapsed=elapsed).write()
    latency = [item.detail for item in report.missed_targets if item.target == "latency"]
    assert latency == ([] if detail is None else [detail])


async def test_result_md_shows_plan_from_plan_json(tmp_path: Path) -> None:
    from bodhan_agent.agent.progress import PlanTracker

    setup = Setup(tmp_path)
    setup.artifact("notes.ta.md", language="ta", indic_text=True)
    stepped = Plan.model_validate(
        {**plan().model_dump(), "steps": [
            {"id": "S1", "intent": "Translate the notes", "why": "Bodhan translates.", "tool": "translate_text"},
            {"id": "S2", "intent": "Check the language", "why": "If unclear.", "tool": "detect_language"},
        ]}
    )  # fmt: skip
    tracker = PlanTracker(setup.settings, setup.ledger, stepped)
    tracker.record("translate_text", "S1", 3, True, ["A1"])
    tracker.record("read", None, 4, True)
    tracker.close()
    tracker.write()
    await setup.finalizer(outcome=setup.outcome(stepped), loop=loop()).write()
    text = setup.result_md()
    section = text.split("## Plan\n\n", 1)[1].split("\n## ", 1)[0]
    assert section.splitlines() == [
        "Goal: x",
        "",
        "- [x] S1 `translate_text`: Translate the notes",
        "- [-] S2 `detect_language`: Check the language — not run",
        "- 1 call(s) outside the plan",
        "",
        "Full plan with reasons, checks and risks: [plan.md](plan.md)",
    ]


async def test_result_md_without_plan_json_has_no_plan_section(tmp_path: Path) -> None:
    setup = Setup(tmp_path)
    setup.artifact("notes.ta.md", language="ta", indic_text=True)
    await setup.finalizer(outcome=setup.outcome(), loop=loop()).write()
    assert "## Plan" not in setup.result_md()


async def test_broken_plan_json_is_ignored(tmp_path: Path) -> None:
    setup = Setup(tmp_path)
    setup.artifact("notes.ta.md", language="ta", indic_text=True)
    (setup.settings.out_dir / "plan.json").write_text("{not json", encoding="utf-8")
    report = await setup.finalizer(outcome=setup.outcome(), loop=loop()).write()
    assert report.status == "completed" and "## Plan" not in setup.result_md()


async def test_finish_assumptions_repeating_the_plan_are_dropped(tmp_path: Path) -> None:
    setup = Setup(tmp_path)
    setup.artifact("notes.ta.md", language="ta", indic_text=True)
    the_plan = plan(assumptions=[
        "Since the user wants the audio 'in English', I will provide both an English audio version and an English "
        "text version for clarity.",
        "I will aim for a natural-sounding English synthesis for the audio output.",
    ])  # fmt: skip
    finish = FinishProposal(answer_en="Done.", deliverables={"D1": "A1", "D2": None}, assumptions=[
        "I assumed the user wanted both the English text and the English audio version of the content.",
        "I assumed a natural-sounding English female voice for the synthesis.",
    ])  # fmt: skip
    report = await setup.finalizer(outcome=setup.outcome(the_plan), loop=loop(finish=finish)).write()
    assert report.assumptions == [
        *the_plan.assumptions,
        "I assumed a natural-sounding English female voice for the synthesis.",
    ]


async def test_cache_hits_are_explained_next_to_cost(tmp_path: Path) -> None:
    setup = Setup(tmp_path)
    setup.artifact("notes.ta.md", language="ta", indic_text=True)
    setup.budget.cache_hits = 7
    await setup.finalizer(outcome=setup.outcome(), loop=loop()).write()
    assert "- Bodhan cost: Rs 0.00 of Rs 20.00 (7 result(s) reused from cache)" in setup.result_md()


async def test_no_cache_note_without_hits(tmp_path: Path) -> None:
    setup = Setup(tmp_path)
    setup.artifact("notes.ta.md", language="ta", indic_text=True)
    await setup.finalizer(outcome=setup.outcome(), loop=loop()).write()
    assert "reused from cache" not in setup.result_md()
