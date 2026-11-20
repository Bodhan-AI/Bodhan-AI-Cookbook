"""report.json says why a quality target was missed, from the run's own trace (brief: "Report in report.json
where you miss them and why")."""

import json

from fakes import FakeBodhan, ScriptedChat
from test_agent_e2e import args, read_outputs

from bodhan_agent import report
from bodhan_agent.cli import run
from bodhan_agent.llm import PlannerLLM
from bodhan_agent.report import budget_reason, latency_reason, verification_reason

TRACE = [
    {"type": "llm_call", "latency_s": 10.0},
    {"type": "llm_call", "latency_s": 20.0},
    {"type": "tool_call", "step": 2, "tool": "ocr_document", "latency_s": 40.0},
    {"type": "tool_call", "step": 3, "tool": "synthesize_speech", "latency_s": 250.0},
    {"type": "tool_call", "step": 4, "tool": "synthesize_speech", "latency_s": 10.0},
    {"type": "api_call", "model": "indic-speak", "status": 200, "cache": "miss"},
    {"type": "api_call", "model": "indic-speak", "status": 200, "cache": "miss"},
    {"type": "api_call", "model": "indic-speak", "status": 429, "cache": "miss"},
    {"type": "api_call", "model": "indic-ocr", "status": 200, "cache": "miss"},
    {"type": "api_call", "model": "indic-ocr", "status": 200, "cache": "this_run"},  # a cache hit is not a request
]


def test_a_missed_latency_target_names_where_the_time_went():
    assert latency_reason(TRACE, 330.0, 300) == (
        "missed by 30 s (330 s against 300 s); tool calls 300 s in total, mostly synthesize_speech (260 s); "
        "slowest step 3 (synthesize_speech, 250 s); planner replies 30 s in total (2 calls); "
        "3 Bodhan requests (indic-speak 2, indic-ocr 1); 1 attempt refused by rate limits and retried")


def test_a_budget_overrun_says_what_was_counted():
    ledger = {"max_inr": 1.0, "total_counted_inr": 1.25, "bodhan_billed_inr": 0.9, "bodhan_uncertain_inr": 0.35, "llm_inr": None}
    assert budget_reason(ledger) == ("over the ₹1.0 cap by ₹0.2500: ₹0.9000 billed by Bodhan, ₹0.3500 for failed or timed-out "
                                     "attempts that may have been billed (counted to be safe)")


def test_unchecked_and_failed_outputs_are_named():
    assert verification_reason([], []) is None
    assert verification_reason(["artifacts/a.mp3"], [("artifacts/b_ta.md", "numbers differ: 12 vs 21")]) == (
        "not checked: artifacts/a.mp3 (no check ran on them; see the tool calls in trace.jsonl); "
        "failed its check: artifacts/b_ta.md (numbers differ: 12 vs 21)")


async def test_a_real_run_that_misses_its_target_reports_why(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "SINGLE_STEP_TARGET_S", 0)  # every run misses a zero-second target
    txt = tmp_path / "p.txt"
    txt.write_text("Trees.", encoding="utf-8")
    chat = ScriptedChat([[("record_plan", {"steps": ["look"], "deliverables": [{"kind": "answer"}]}), ("detect_language", {"path": str(txt)})],
                         [("finish", {"status": "completed", "final_answer": "It is English."})]])
    await run(args(tmp_path, "What language is this?", [txt]), transport=FakeBodhan().transport, llm=PlannerLLM(client=chat, model="s"))
    out, rep, trace, md = read_outputs(tmp_path)
    note = rep["quality_targets"]["latency"]["note"]
    assert rep["quality_targets"]["latency"]["met"] is False
    assert note.startswith("missed by ") and "against 0 s" in note and "planner replies" in note and "detect_language" in note
    assert note in md
    assert rep["quality_targets"]["budget"]["note"] is None and rep["quality_targets"]["verification"]["note"] is None


def test_finished_runs_get_the_reason_from_their_own_trace(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path

    run = tmp_path / "runs" / "d9"
    run.mkdir(parents=True)
    (run / "trace.jsonl").write_text("\n".join(json.dumps(e) for e in TRACE) + "\n", encoding="utf-8")
    rep = {"elapsed_s": 330.0, "cost": {"max_inr": 20.0, "total_counted_inr": 0.5, "bodhan_billed_inr": 0.5, "bodhan_uncertain_inr": 0.0, "llm_inr": None},
           "verification": {"unverified_translations_or_audio": []}, "artifacts": [],
           "quality_targets": {"latency": {"target_s": 300, "met": False, "note": "missed: see trace.jsonl latency_s per step"},
                               "budget": {"within_budget": True}, "verification": {"all_generated_audio_and_translations_checked": True}}}
    (run / "report.json").write_text(json.dumps(rep), encoding="utf-8")
    (run / "result.md").write_text("- Time: 330.0 s (multi-step, target 300 s: missed)\n", encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "scripts" / "explain_missed_targets.py"
    first = subprocess.run([sys.executable, str(script), str(tmp_path / "runs")], capture_output=True, text=True, encoding="utf-8")
    assert "1 report(s) updated" in first.stdout, first.stderr
    got = json.loads((run / "report.json").read_text(encoding="utf-8"))["quality_targets"]
    assert got["latency"]["note"] == latency_reason(TRACE, 330.0, 300) and got["latency"]["note_source"].startswith("recomputed from trace.jsonl")
    assert got["budget"]["note"] is None and got["verification"]["note"] is None
    assert (run / "result.md").read_text(encoding="utf-8").startswith("- Time: 330.0 s (multi-step, target 300 s: missed); missed by 30 s")
    again = subprocess.run([sys.executable, str(script), str(tmp_path / "runs")], capture_output=True, text=True, encoding="utf-8")
    assert "0 report(s) updated" in again.stdout


def at(seconds: float) -> str:
    from datetime import UTC, datetime, timedelta
    return (datetime(2026, 11, 20, tzinfo=UTC) + timedelta(seconds=seconds)).isoformat(timespec="milliseconds")


def test_parallel_tool_calls_are_counted_once_in_the_wall_clock_share():
    # two 200 s tools side by side after a 10 s planner reply: 210 s elapsed, not "190% of the time"
    events = [{"type": "llm_call", "latency_s": 10.0, "ts": at(10)},
              {"type": "tool_call", "step": 2, "tool": "synthesize_speech", "latency_s": 200.0, "ts": at(210)},
              {"type": "tool_call", "step": 3, "tool": "synthesize_speech", "latency_s": 200.0, "ts": at(210)}]
    note = latency_reason(events, 210.0, 30)
    assert note == ("missed by 180 s (210 s against 30 s); tool calls 95% of the time (200 s), mostly synthesize_speech (200 s); "
                    "slowest step 2 (synthesize_speech, 200 s); planner replies 5% of the time (10 s) (1 calls)")


def test_time_no_event_accounts_for_is_reported_as_such():
    # a planner call that waited 60 s on retries before a 1 s reply: the wait is not hidden or given to a step
    events = [{"type": "llm_call", "latency_s": 1.0, "ts": at(61)}, {"type": "tool_call", "step": 2, "tool": "read_text", "latency_s": 1.0, "ts": at(62)}]
    note = latency_reason(events, 62.0, 30)
    assert "60 s) outside tool calls and planner replies (planner retries and their waits" in note and "97% of the time (60 s)" in note


def test_a_terminal_rate_limit_refusal_is_not_called_retried():
    events = [{"type": "api_call", "model": "indic-speak", "status": 429, "cache": "miss", "will_retry": True},
              {"type": "api_call", "model": "indic-speak", "status": 429, "cache": "miss", "will_retry": False}]
    note = latency_reason(events, 40.0, 30)
    assert note.endswith("1 attempt refused by rate limits and retried; 1 request refused by rate limits and not retried")


def test_a_trace_cut_off_inside_an_indic_character_keeps_its_complete_events(tmp_path):
    from bodhan_agent.report import _events
    trace = tmp_path / "trace.jsonl"
    trace.write_bytes(json.dumps({"type": "llm_call", "latency_s": 2.0}).encode() + b"\n" + b'{"rationale":"\xe0\xa4')
    assert _events(trace) == [{"type": "llm_call", "latency_s": 2.0}]


def test_backfill_names_a_failed_check_and_keeps_result_md_in_step(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    run = tmp_path / "runs" / "d9"
    run.mkdir(parents=True)
    (run / "trace.jsonl").write_text("\n".join(json.dumps(e) for e in TRACE) + "\n", encoding="utf-8")
    rep = {"elapsed_s": 330.0, "cost": {"max_inr": 20.0, "total_counted_inr": 0.5, "bodhan_billed_inr": 0.5, "bodhan_uncertain_inr": 0.0, "llm_inr": None},
           "verification": {"unverified_translations_or_audio": []},
           "artifacts": [{"path": "artifacts/b_ta.md", "kind": "translation",
                          "verification": {"status": "fail", "problems": ["numbers differ: 12 vs 21", "a", "b"]}}],
           "quality_targets": {"latency": {"target_s": 300, "met": False, "note": "missed: see trace.jsonl latency_s per step"},
                               "budget": {"within_budget": True}, "verification": {"all_generated_audio_and_translations_checked": False}}}
    (run / "report.json").write_text(json.dumps(rep), encoding="utf-8")
    (run / "result.md").write_text("- Time: 330.0 s (multi-step, target 300 s: missed)\n", encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "scripts" / "explain_missed_targets.py"
    cmd = [sys.executable, str(script), str(tmp_path / "runs")]
    assert "1 report(s) updated" in subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8").stdout
    q = json.loads((run / "report.json").read_text(encoding="utf-8"))["quality_targets"]
    assert q["verification"]["note"] == "failed its check: artifacts/b_ta.md (numbers differ: 12 vs 21; a)"
    # result.md lost its note (say, an older export): it is repaired although report.json is already current
    (run / "result.md").write_text("- Time: 330.0 s (multi-step, target 300 s: missed)\n", encoding="utf-8")
    assert "1 report(s) updated" in subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8").stdout
    assert q["latency"]["note"] in (run / "result.md").read_text(encoding="utf-8")
    assert "0 report(s) updated" in subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8").stdout


def test_the_finish_time_coverage_check_counts_as_planner_time():
    events = [{"type": "llm_call", "latency_s": 1.0, "ts": at(1)}, {"type": "tool_call", "step": 2, "tool": "write_text", "latency_s": 1.0, "ts": at(2)},
              {"type": "coverage_check", "latency_s": 60.0, "ts": at(62)}]
    note = latency_reason(events, 62.0, 30)
    assert "planner replies 98% of the time (61 s) (2 calls)" in note and "outside tool calls" not in note
