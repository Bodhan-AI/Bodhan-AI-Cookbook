"""The eval CLI end to end: real agent subprocesses → real MCP server → fake Bodhan and a scripted fake LLM."""

from __future__ import annotations

import json

import pytest

from bodhan_eval.__main__ import main
from conftest import FakeBodhan, tone_wav
from fake_servers import FakeLLM, bodhan_server


@pytest.fixture
def env(tmp_path, monkeypatch):
    fake = FakeBodhan()
    with bodhan_server(fake) as srv:
        monkeypatch.setenv("BODHAN_BASE_URL", srv.url)
        for var in ("BODHAN_TRANSCRIBE_KEY", "BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY", "BODHAN_OCR_KEY"):
            monkeypatch.setenv(var, "test-key")
        monkeypatch.setenv("BODHAN_CACHE_PATH", str(tmp_path / "cache.sqlite"))
        monkeypatch.setenv("LLM_MODEL", "scripted")
        monkeypatch.setenv("LLM_API_KEY", "test")
        yield fake


def write_suite(path, cases):
    path.write_text(json.dumps({"cases": cases}), encoding="utf-8")
    return path


def test_eval_runs_grades_and_regrades(env, tmp_path, monkeypatch):
    tone_wav(tmp_path / "talk.wav", [("tone", 2)])

    def brain(req):
        return {"content": json.dumps({"understanding": "dub", "deliverables": [{"kind": "audio", "language": "French",
                                                                                   "description": "dub"}],
                                       "inputs_used": [], "steps": [], "assumptions": [], "feasible": True,
                                       "infeasible_reason": None})}

    suite = write_suite(tmp_path / "suite.json", [
        {"id": "french", "task": "Translate this into French", "files": ["talk.wav"],
         "checks": [{"type": "status", "value": "impossible"}, {"type": "max_cost_inr", "value": 0}]},
        {"id": "wrong_expectation", "task": "Translate this into French", "files": ["talk.wav"],
         "checks": [{"type": "status", "value": "completed"}]},
        {"id": "missing", "task": "Transcribe", "files": ["nope.wav"], "inputs_must_exist": False,
         "checks": [{"type": "status", "value": "impossible"}, {"type": "answer_contains", "all": ["nope.wav"]}]},
        {"id": "no_inputs_yet", "task": "x", "files": ["not_generated.png"], "checks": [{"type": "status", "value": "x"}]},
    ])
    out = tmp_path / "eval"
    with FakeLLM(brain) as llm:
        monkeypatch.setenv("LLM_BASE_URL", llm.url)
        code = main(["--suite", str(suite), "--root", str(tmp_path), "--out", str(out)])

    data = json.loads((out / "eval_results.json").read_text())
    by_id = {r["id"]: r for r in data["results"]}
    assert code == 1  # one case is expected to fail
    assert by_id["french"]["passed"] and by_id["missing"]["passed"] and not by_id["wrong_expectation"]["passed"]
    assert by_id["french"]["exit_code"] == 0 and (out / "french" / "report.json").exists()
    s = data["summary"]
    assert (s["runs"], s["runs_passed"]) == (3, 2)
    assert [k["id"] for k in s["skipped"]] == ["no_inputs_yet"] and "make_demo_inputs" in s["skipped"][0]["why"]
    assert sum(env.calls.values()) == 0
    assert "| wrong_expectation | ❌ | impossible |" in (out / "eval_report.md").read_text()

    # Re-grading a finished run with a corrected expectation costs nothing and calls no agent.
    write_suite(suite, [{"id": "wrong_expectation", "task": "t", "files": ["talk.wav"],
                         "checks": [{"type": "status", "value": "impossible"}]}])
    assert main(["--suite", str(suite), "--regrade", str(out)]) == 0


def test_suite_budget_skips_cases_that_would_overspend(env, tmp_path, monkeypatch):
    tone_wav(tmp_path / "a.wav", [("tone", 1)])
    suite = write_suite(tmp_path / "suite.json", [
        {"id": "pricey", "task": "t", "files": ["a.wav"], "est_cost_inr": 0.5, "checks": [{"type": "status", "value": "x"}]},
    ])
    code = main(["--suite", str(suite), "--root", str(tmp_path), "--out", str(tmp_path / "e"), "--max-total-inr", "0.2"])
    data = json.loads((tmp_path / "e" / "eval_results.json").read_text())
    assert code == 1 and data["results"] == [] and "suite budget" in data["summary"]["skipped"][0]["why"]
