"""The evaluation oracle must reject dishonest successes independently of the agent."""

import json
from bodhan_agent import evaluation


def evidence(tmp_path, **changes):
    (tmp_path / "answer.md").write_text("Beans 18; peas 24; total 42.")
    report = {
        "status": "completed",
        "total_cost_inr": "0",
        "total_seconds": 1,
        "verification": [],
        "explanation": "Saved the inventory.",
        "artifacts": [{"path": "answer.md", "selected": True, "ref": "answer"}],
        **changes,
    }
    (tmp_path / "report.json").write_text(json.dumps(report))
    return tmp_path


def test_oracle_requires_selected_artifacts_and_expected_content(tmp_path):
    out = evidence(tmp_path, artifacts=[])
    result = evaluation.assess(out, {"statuses": ["completed"], "required_text": ["42"]}, "0")
    assert not result["passed"]
    assert len(result["errors"]) == 2


def test_oracle_rejects_wrong_ref_verification_and_output_escape(tmp_path):
    out = evidence(
        tmp_path,
        artifacts=[
            {
                "path": "answer.md",
                "selected": True,
                "ref": "answer",
                "provenance": "bodhan_translation",
            }
        ],
        verification=[{"ref": "other", "checked": True, "passed": True}],
    )
    assert not evaluation.assess(out, {"statuses": ["completed"]}, "0")["passed"]
    evidence(tmp_path, artifacts=[{"path": "../escape.md", "selected": True}])
    assert not evaluation.assess(out, {"statuses": ["completed"]}, "0")["passed"]


def test_oracle_distinguishes_honest_failure_and_failed_verification(tmp_path):
    out = evidence(
        tmp_path, status="failed", artifacts=[], explanation="missing_files: Nothing supplied."
    )
    expected = {"statuses": ["failed"], "explanation_any": ["missing_files"]}
    assert evaluation.assess(out, expected, "0")["passed"]
    evidence(tmp_path, verification=[{"checked": True, "passed": False}])
    assert not evaluation.assess(out, {"statuses": ["completed"]}, "0")["passed"]


def test_oracle_rejects_billed_dispatch_during_cache_only_and_excess_cost(tmp_path):
    out = evidence(tmp_path, total_cost_inr="0.01")
    (out / "trace.jsonl").write_text(json.dumps({"kind": "api", "model": "indic-ocr"}) + "\n")
    result = evaluation.assess(out, {"statuses": ["completed"], "no_bodhan_dispatch": True}, "0")
    assert not result["passed"]
    assert len(result["errors"]) == 2
