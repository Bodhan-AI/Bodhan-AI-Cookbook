"""report.json says why a quality target was missed, from the run's own trace (brief: "Report in report.json
where you miss them and why")."""

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
        "missed by 30 s (330 s against 300 s); tool calls 91% of the time, mostly synthesize_speech (260 s); "
        "slowest step 3 (synthesize_speech, 250 s); planner turns 9% (2 calls, 30 s); "
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
    assert note.startswith("missed by ") and "against 0 s" in note and "planner turns" in note and "detect_language" in note
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
