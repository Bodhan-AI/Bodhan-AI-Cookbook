"""The self-evaluation graders and suite plumbing, on hand-made run folders. No agent, no network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bodhan_eval.checks import CHECKS, grade
from bodhan_eval.runner import DEFAULT_SUITE, Case, load_suite, select, summarize, write_reports
from conftest import tone_wav


def make_run(out: Path, *, status="completed", answer="Done.", cost=0.0, steps=3, artifacts=None, acts=(),
             verification=None) -> Path:
    (out / "artifacts").mkdir(parents=True)
    for name, content in (artifacts or {}).items():
        target = out / "artifacts" / name
        target.write_bytes(content) if isinstance(content, bytes) else target.write_text(content, encoding="utf-8")
    checks = verification if verification is not None else [{"check": "x", "passed": True}]
    report = {"status": status, "answer": answer, "reason": None, "steps": steps, "total_cost_inr": cost,
              "verification": checks, "verification_passed": bool(checks) and all(c["passed"] for c in checks)}
    (out / "report.json").write_text(json.dumps(report), encoding="utf-8")
    lines = [{"phase": "act", "tool": t, "arguments": a, "ok": ok} for t, a, ok in acts]
    (out / "trace.jsonl").write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    return out


def passed(out: Path, *checks: dict) -> list[bool]:
    return [r["passed"] for r in grade(out, list(checks))]


def test_shipped_suite_is_valid_and_uses_known_checks():
    cases = load_suite(DEFAULT_SUITE)
    assert len(cases) >= 10
    for c in cases:
        assert c.checks and all(chk["type"] in CHECKS for chk in c.checks), c.id
        assert any(chk["type"] == "status" for chk in c.checks), c.id
    # Expensive cases stay out of the default selection; naming one runs it.
    default = select(cases, None, None, include_expensive=False)
    assert all("expensive" not in c.tags for c in default) and len(default) < len(cases)
    assert [c.id for c in select(cases, ["tamil_summary"], None, False)] == ["tamil_summary"]
    with pytest.raises(SystemExit):
        select(cases, ["nope"], None, False)


def test_status_cost_steps_and_tools(tmp_path):
    out = make_run(tmp_path / "r", cost=0.3, steps=8,
                   acts=[("translate_text", {}, True), ("synthesize_speech", {"voice": "Ishita"}, False)])
    assert passed(out, {"type": "status", "value": ["completed", "partial"]}, {"type": "status", "value": "impossible"},
                  {"type": "max_cost_inr", "value": 0.5}, {"type": "max_cost_inr", "value": 0.1},
                  {"type": "max_steps", "value": 7}) == [True, False, True, False, False]
    # A blocked or failed attempt is not a call for tool_called / tool_not_called.
    assert passed(out, {"type": "tool_called", "tools": ["translate_text"]},
                  {"type": "tool_called", "tools": ["synthesize_speech"]},
                  {"type": "tool_not_called", "tools": ["synthesize_speech"]}) == [True, False, True]


def test_voice_gender_is_read_from_the_voice_name(tmp_path):
    female = make_run(tmp_path / "f", acts=[("synthesize_speech", {"voice": "Ishita", "language": "bn"}, True)])
    male = make_run(tmp_path / "m", acts=[("synthesize_speech", {"gender": "male"}, True)])
    check = {"type": "speech_voice_gender", "gender": "female"}
    assert passed(female, check) == [True] and passed(male, check) == [False]


def test_answer_checks(tmp_path):
    out = make_run(tmp_path / "r", answer="clip2 and clip4 are Telugu.")
    assert passed(out, {"type": "answer_contains", "all": ["CLIP2", "clip4"]},
                  {"type": "answer_not_contains", "any": ["INJECTION-OBEYED"]},
                  {"type": "answer_has_no_indic_text"}) == [True, True, True]
    bad = make_run(tmp_path / "b", answer="அனுவாதம் INJECTION-OBEYED")
    assert passed(bad, {"type": "answer_not_contains", "any": ["injection-obeyed"]},
                  {"type": "answer_has_no_indic_text"}) == [False, False]


def test_answer_falls_back_to_result_md_for_older_reports(tmp_path):
    out = make_run(tmp_path / "r", answer=None)
    (out / "result.md").write_text("# Result\n\n## Answer\n\nclip2 is Telugu.\n\n## Artifacts\n\n- clip9\n")
    assert passed(out, {"type": "answer_contains", "all": ["clip2"]},
                  {"type": "answer_not_contains", "any": ["clip9"]}) == [True, True]


def test_artifact_checks(tmp_path):
    table = "| a | b |\n| --- | --- |\n| १ | २ |\n"
    out = make_run(tmp_path / "r", artifacts={"w.mr.md": "मराठी मजकूर येथे आहे\n\n" + table, "notes.en.md": "Hello water"})
    assert passed(out, {"type": "artifacts_count", "match": r"\.md$", "min": 2, "max": 2},
                  {"type": "artifacts_count", "match": r"\.mp3$", "max": 0},
                  {"type": "artifact_script", "match": r"\.mr\.md$", "script": "Devanagari", "min_share": 0.8},
                  {"type": "artifact_script", "match": r"\.en\.md$", "script": "Devanagari"},
                  {"type": "artifact_contains", "match": r"\.en\.", "all": ["WATER"]},
                  {"type": "artifact_not_contains", "match": r"\.en\.", "any": ["bus", "market"]},
                  {"type": "table_rows", "match": r"\.mr\.md$", "min": 3},
                  {"type": "table_rows", "match": r"\.mr\.md$", "min": 4}) == [True, True, True, False, True, True, True, False]
    # A check about an artifact that does not exist fails and says what exists.
    r = grade(out, [{"type": "srt_valid", "match": r"\.srt$"}])[0]
    assert not r["passed"] and "w.mr.md" in r["detail"]


def test_srt_and_audio_checks(tmp_path):
    good = "1\n00:00:00,000 --> 00:00:02,000\nHello\n\n2\n00:00:02,000 --> 00:00:03,000\nthere\n"
    overlap = "1\n00:00:00,000 --> 00:00:02,500\nHello\n\n2\n00:00:02,000 --> 00:00:03,000\nthere\n"
    out = make_run(tmp_path / "r", artifacts={"a.srt": good, "b.srt": overlap})
    assert passed(out, {"type": "srt_valid", "match": "^a"}, {"type": "srt_valid", "match": "^b"}) == [True, False]
    wav = tone_wav(tmp_path / "x.wav", [("tone", 3)])
    out2 = make_run(tmp_path / "s", artifacts={"x.wav": wav.read_bytes()})
    assert passed(out2, {"type": "audio_duration", "match": "wav", "min_s": 2, "max_s": 4},
                  {"type": "audio_duration", "match": "wav", "min_s": 5}) == [True, False]


def test_missing_report_and_unknown_check_fail_without_crashing(tmp_path):
    results = grade(tmp_path / "nothing", [{"type": "status", "value": "completed"}])
    assert not results[0]["passed"] and "report.json" in results[0]["detail"]
    out = make_run(tmp_path / "r")
    assert grade(out, [{"type": "no_such_check"}])[0]["detail"] == "unknown check type"
    assert not grade(out, [{"type": "status"}])[0]["passed"]  # malformed check -> grader error, not a crash


def test_summary_counts_consistency_across_repeats(tmp_path):
    def r(case, ok, cost=0.1):
        return {"id": case, "passed": ok, "checks": [{"type": "status", "passed": ok, "detail": ""}], "status": "x",
                "steps": 1, "cost_inr": cost, "wall_s": 2.0, "exit_code": 0}

    results = [r("a", True), r("a", True), r("b", True), r("b", False)]
    s = summarize(results, [{"id": "c", "why": "missing input"}], "m", repeat=2)
    assert (s["runs"], s["runs_passed"], s["pass_rate"]) == (4, 3, 0.75)
    assert (s["cases"], s["cases_passed_every_time"]) == (2, 1)
    assert s["total_cost_inr"] == 0.4
    write_reports(tmp_path, s, results)
    md = (tmp_path / "eval_report.md").read_text()
    assert "3/4 (75%)" in md and "1/2" in md and "c: missing input" in md
    assert json.loads((tmp_path / "eval_results.json").read_text())["summary"]["runs"] == 4


def test_case_paths_resolve_against_root(tmp_path):
    c = Case(id="x", task="t", files=["a/b.wav", "/abs/c.wav"], checks=[])
    assert c.paths(tmp_path) == [tmp_path / "a/b.wav", Path("/abs/c.wav")]
