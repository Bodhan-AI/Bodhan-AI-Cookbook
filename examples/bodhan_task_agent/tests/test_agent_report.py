from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from bodhan_agent.schemas.report import (
    CostSummary,
    CountUsage,
    DeliverableReport,
    RunReport,
    Usage,
    status_for,
)

START = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)
LABEL = "AI-generated speech (Bodhan Indic-Speak)"


def audio(**overrides: Any) -> dict[str, Any]:
    return {
        "id": "D1",
        "kind": "audio",
        "language": "ta",
        "format": "mp3",
        "status": "verified",
        "path": "artifacts/summary.ta.mp3",
        "label": LABEL,
        "checks": [{"name": "speech_word_recall", "status": "passed", "value": 0.93, "threshold": 0.8}],
        **overrides,
    }


def text(**overrides: Any) -> dict[str, Any]:
    return {"id": "D2", "kind": "text", "status": "verified", "path": "artifacts/summary.ta.txt", **overrides}


def missing(deliverable_id: str = "D1", kind: str = "audio") -> dict[str, Any]:
    return {"id": deliverable_id, "kind": kind, "status": "missing"}


def report(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "status": "completed",
        "stop_reason": "finished",
        "task": "Make a Tamil audio summary of this chapter",
        "planned": ["D1", "D2"],
        "deliverables": [audio(), text()],
        "budgets": {
            "cost_inr": {"used": 1.2, "limit": 20},
            "steps": {"used": 9, "limit": 30},
            "llm_turns": {"used": 11, "limit": 60},
            "time_s": {"used": 95.4, "limit": 290},
        },
        "cost": {"bodhan_inr": 1.2, "llm_usage_complete": True, "llm_tokens": {"prompt": 9000, "completion": 1200}},
        "started_at": START,
        "finished_at": START + timedelta(seconds=95),
    }
    fields.update(overrides)
    return fields


def deliverable(data: dict[str, Any]) -> DeliverableReport:
    return DeliverableReport.model_validate(data)


def test_completed_report_round_trips() -> None:
    result = RunReport.model_validate(report())
    assert RunReport.model_validate_json(result.to_json()) == result


def test_to_json_is_indented_and_skips_unset_fields() -> None:
    raw = RunReport.model_validate(report()).to_json()
    assert raw.startswith("{\n  ")
    assert "explanation" not in json.loads(raw)


def test_partial_when_a_planned_deliverable_is_missing() -> None:
    result = RunReport.model_validate(
        report(
            status="partial",
            stop_reason="step_limit",
            explanation="Audio was not produced.",
            deliverables=[missing(), text()],
        )
    )
    assert result.status == "partial"


def test_omitted_planned_deliverable_rejected() -> None:
    with pytest.raises(ValidationError, match="every planned deliverable"):
        RunReport.model_validate(report(deliverables=[text()]))


def test_unplanned_deliverable_rejected() -> None:
    with pytest.raises(ValidationError, match="every planned deliverable"):
        RunReport.model_validate(report(planned=["D2"]))


def test_duplicate_planned_ids_rejected() -> None:
    with pytest.raises(ValidationError, match="every planned deliverable"):
        RunReport.model_validate(report(planned=["D1", "D2", "D2"]))


def test_duplicate_deliverable_ids_rejected() -> None:
    with pytest.raises(ValidationError, match="unique"):
        RunReport.model_validate(report(planned=["D2"], deliverables=[text(), text()]))


def test_stop_before_planning_has_no_deliverables() -> None:
    result = RunReport.model_validate(
        report(status="failed", stop_reason="planner_error", explanation="No valid plan.", planned=[], deliverables=[])
    )
    assert result.deliverables == []


@pytest.mark.parametrize(
    ("claimed", "stop_reason", "deliverables"),
    [
        ("completed", "step_limit", [missing(), text()]),
        ("partial", "finished", [audio(), text()]),
        ("failed", "unsupported_language", []),
        ("impossible", "budget_insufficient", []),
    ],
)
def test_status_must_match_deliverables(claimed: str, stop_reason: str, deliverables: list[dict[str, Any]]) -> None:
    planned = [item["id"] for item in deliverables]
    with pytest.raises(ValidationError, match="does not match"):
        RunReport.model_validate(
            report(status=claimed, stop_reason=stop_reason, explanation="x", planned=planned, deliverables=deliverables)
        )


def test_incomplete_run_needs_explanation() -> None:
    with pytest.raises(ValidationError, match="explanation"):
        RunReport.model_validate(report(status="partial", stop_reason="step_limit", deliverables=[missing(), text()]))


def test_status_for_rules() -> None:
    usable = deliverable(text())
    warned = deliverable(text(id="D3", status="warning", checks=[{"name": "target_script", "status": "warning"}]))
    gone = deliverable(missing())
    broken = deliverable(audio(id="D4", status="failed", checks=[{"name": "speech_word_recall", "status": "failed"}]))
    assert status_for([usable, warned], "finished") == "completed"
    assert status_for([usable, gone], "step_limit") == "partial"
    assert status_for([gone, broken], "deadline") == "failed"
    assert status_for([], "unsupported_language") == "impossible"
    assert status_for([gone], "missing_input") == "impossible"
    assert status_for([], "budget_insufficient") == "failed"
    assert status_for([], "finished") == "failed"


@pytest.mark.parametrize(
    "path",
    [
        "/tmp/summary.mp3",
        "artifacts/../../secret.mp3",
        "work/summary.mp3",
        "artifacts",
        "summary.mp3",
        "../artifacts/x",
    ],
)
def test_bad_deliverable_paths_rejected(path: str) -> None:
    with pytest.raises(ValidationError, match="under artifacts/"):
        deliverable(text(path=path))


def test_nested_artifact_path_accepted() -> None:
    assert deliverable(text(path="artifacts/clips/clip1.en.txt")).path == "artifacts/clips/clip1.en.txt"


def test_answer_has_no_path() -> None:
    with pytest.raises(ValidationError, match="no path"):
        deliverable({"id": "D3", "kind": "answer", "status": "verified", "path": "artifacts/answer.md"})


def test_answer_without_path_is_usable() -> None:
    assert deliverable({"id": "D3", "kind": "answer", "status": "verified"}).path is None


def test_missing_deliverable_has_no_path() -> None:
    with pytest.raises(ValidationError, match="no path"):
        deliverable({**missing(), "path": "artifacts/summary.ta.mp3"})


def test_usable_file_needs_path() -> None:
    with pytest.raises(ValidationError, match="needs a path"):
        deliverable(text(path=None))


def test_failed_deliverable_may_keep_its_file() -> None:
    item = deliverable(audio(status="failed", checks=[{"name": "speech_word_recall", "status": "failed"}]))
    assert item.path == "artifacts/summary.ta.mp3"


def test_audio_needs_ai_label() -> None:
    with pytest.raises(ValidationError, match="AI-generated label"):
        deliverable(audio(label=None))


def test_failed_check_forces_failed_status() -> None:
    with pytest.raises(ValidationError, match="failed check"):
        deliverable(audio(checks=[{"name": "speech_word_recall", "status": "failed", "value": 0.35}]))


def test_failed_check_cannot_hide_behind_warning() -> None:
    checks = [{"name": "speech_word_recall", "status": "failed"}, {"name": "duration", "status": "warning"}]
    with pytest.raises(ValidationError, match="failed check"):
        deliverable(audio(status="warning", checks=checks))


def test_verified_cannot_have_warning_checks() -> None:
    with pytest.raises(ValidationError, match="'warning', not 'verified'"):
        deliverable(text(checks=[{"name": "target_script", "status": "warning"}]))


def test_warning_needs_a_warning_check() -> None:
    with pytest.raises(ValidationError, match="at least one warning check"):
        deliverable(text(status="warning"))


def test_skipped_checks_allow_verified() -> None:
    assert deliverable(text(checks=[{"name": "table_shape", "status": "skipped"}])).status == "verified"


def test_count_usage_rejects_fractions() -> None:
    with pytest.raises(ValidationError):
        CountUsage(used=3.5)


def test_usage_exceeded() -> None:
    assert Usage(used=20.5, limit=20).exceeded is True
    assert Usage(used=20.0, limit=20).exceeded is False
    assert Usage(used=5.0).exceeded is False
    assert CountUsage(used=31, limit=30).exceeded is True
    assert CountUsage(used=30, limit=30).exceeded is False


def test_cost_used_must_equal_exposure() -> None:
    with pytest.raises(ValidationError, match="bodhan_inr plus reserved_unsettled_inr"):
        RunReport.model_validate(report(cost={"bodhan_inr": 1.0, "llm_usage_complete": False}))


def test_open_reservations_count_toward_used_cost() -> None:
    result = RunReport.model_validate(
        report(cost={"bodhan_inr": 1.0, "reserved_unsettled_inr": 0.2, "llm_usage_complete": False})
    )
    assert result.cost.exposure_inr == pytest.approx(1.2)


def test_complete_llm_usage_needs_counts() -> None:
    with pytest.raises(ValidationError, match="llm_usage_complete"):
        CostSummary(bodhan_inr=0, llm_usage_complete=True, llm_tokens={"prompt": 10})


def test_unknown_llm_usage_allowed_when_marked_incomplete() -> None:
    assert CostSummary(bodhan_inr=0, llm_usage_complete=False).llm_tokens.total is None


def test_compliance_defaults_and_violations_are_reported() -> None:
    result = RunReport.model_validate(report(compliance={"non_bodhan_ml": 1, "media_to_llm": 2}))
    assert (result.compliance.non_bodhan_ml, result.compliance.media_to_llm) == (1, 2)
    assert RunReport.model_validate(report()).compliance.indic_redactions == 0


def test_missed_target_needs_detail() -> None:
    with pytest.raises(ValidationError):
        RunReport.model_validate(report(missed_targets=[{"target": "latency", "detail": ""}]))


def test_naive_timestamps_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        RunReport.model_validate(report(started_at=datetime(2026, 11, 20, 10, 0)))


def test_finish_before_start_rejected() -> None:
    with pytest.raises(ValidationError, match="before started_at"):
        RunReport.model_validate(report(finished_at=START - timedelta(seconds=1)))


def test_extra_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        RunReport.model_validate(report(success=True))
