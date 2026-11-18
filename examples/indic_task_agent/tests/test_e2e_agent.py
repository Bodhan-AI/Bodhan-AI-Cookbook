"""Whole agent runs through bodhan_agent.cli.main with a scripted planner LLM and the fake backend."""
from __future__ import annotations

import json
import re
from collections import Counter

import pytest

from bodhan_agent import cli, llm
from bodhan_mcp.core import costs

INDIC = re.compile("[\u0900-\u0dff]")
TAMIL = "\u0bb5\u0ba3\u0b95\u0bcd\u0b95\u0bae\u0bcd \u0b89\u0bb2\u0b95\u0bae\u0bcd. " * 6
ENGLISH = "The report says the water supply improved across the district this year."
DRAFT = "A short English summary of the report. The water supply improved across the district."


@pytest.fixture(autouse=True)
def workdir(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)  # the CLI reads ./.env; keep it away from any real one


class FakePlanner:
    def __init__(self, plan: dict) -> None:
        self.plan = plan
        self.seen: list[list[dict]] = []

    def __call__(self, messages, max_tokens=4096, temperature=0.2):
        self.seen.append(messages)
        prose = messages[0]["content"].startswith("You write English prose")
        text = DRAFT if prose else json.dumps(self.plan)
        return llm.LlmReply(text=text, finish_reason="stop", prompt_chars=sum(len(m["content"]) for m in messages), latency_ms=1)


def install(monkeypatch, plan: dict) -> FakePlanner:
    fake = FakePlanner(plan)
    monkeypatch.setattr(llm, "chat", fake)
    return fake


def wire_backend(bodhan):
    bodhan.mt_fn = lambda rec: TAMIL if rec.json["target_language_code"] == "ta" else ENGLISH
    bodhan.asr_fn = lambda rec: TAMIL


def pipeline(pdf) -> dict:
    return {
        "understanding": "OCR the report, summarise it in English and read the Tamil version aloud.",
        "assumptions": [{"text": "Tamil audio is wanted", "why": "task says so"}],
        "steps": [
            {"id": "s1", "intent": "OCR the scan", "tool": "ocr_document", "args": {"input_path": str(pdf)}},
            {"id": "s2", "intent": "translate to English", "tool": "translate_text", "depends_on": ["s1"],
             "args": {"source_path": "$s1.markdown_path", "target_language": "en"}, "expected_chars": 400},
            {"id": "s3", "intent": "write a summary", "tool": "write_text", "depends_on": ["s2"],
             "args": {"instruction": "Summarise", "inputs": ["$s2.text_path"], "target_chars": 300}},
            {"id": "s4", "intent": "translate to Tamil", "tool": "translate_text", "depends_on": ["s3"],
             "args": {"source_path": "$s3.text_path", "target_language": "ta"}, "expected_chars": 300},
            {"id": "s5", "intent": "speak it", "tool": "speak_text", "depends_on": ["s4"],
             "args": {"text_path": "$s4.text_path", "language": "ta"}, "expected_chars": 200},
        ],
        "final_outputs": [{"role": "audio", "from": "s5"}],
    }


def run(out, files, task="Make a Tamil audio summary", extra=()):
    return cli.main(["--task", task, "--files", str(files), "--out", str(out), *extra])


def report_of(out) -> dict:
    return json.loads((out / "report.json").read_text(encoding="utf-8"))


@pytest.fixture
def full_run(bodhan, monkeypatch, tmp_path, make_pdf):
    wire_backend(bodhan)
    pdf = make_pdf("report.pdf", 1)
    planner = install(monkeypatch, pipeline(pdf))
    out = tmp_path / "run1"
    code = run(out, pdf)
    return {"code": code, "out": out, "pdf": pdf, "planner": planner, "bodhan": bodhan}


def test_multi_step_run_completes_with_all_four_outputs(full_run):
    out = full_run["out"]
    assert full_run["code"] == 0
    for name in ("result.md", "report.json", "trace.jsonl"):
        assert (out / name).is_file()
    assert (out / "artifacts").is_dir() and any((out / "artifacts").glob("*.wav"))
    report = report_of(out)
    assert report["status"] == "completed", report["errors"]
    assert [s["tool"] for s in report["steps"]] == ["ocr_document", "translate_text", "write_text", "translate_text", "speak_text"]
    assert all(s["ok"] for s in report["steps"])
    assert "AI-generated" in (out / "result.md").read_text(encoding="utf-8")


def test_cost_is_the_sum_of_steps_and_within_budget(full_run):
    report = report_of(full_run["out"])
    total = sum(s["cost_inr"] for s in report["steps"])
    assert report["budget"]["cost_inr"] == pytest.approx(total + report["budget"]["verification_inr"], abs=1e-3)
    assert 0 < report["budget"]["cost_inr"] <= report["budget"]["max_cost_inr"]
    tokens = [len(TAMIL) // 2, len(ENGLISH) // 2]
    expected = costs.ocr_cost(1) + sum(costs.mt_cost(t) for t in tokens) + costs.tts_cost(len(TAMIL.strip()))
    assert total == pytest.approx(expected, abs=2e-3)
    assert report["quality_targets"]["within_budget"] is True


def test_trace_is_ordered_valid_json_and_free_of_indic_text(full_run):
    lines = (full_run["out"] / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines]
    kinds = [e["type"] for e in events]
    for needed in ("intake", "plan", "tool_call", "tool_result", "verify", "stop"):
        assert needed in kinds
    first = {k: kinds.index(k) for k in set(kinds)}
    assert first["intake"] < first["plan"] < first["tool_call"] < first["tool_result"] < first["verify"] < first["stop"]
    assert kinds[-1] == "stop"
    assert not INDIC.search("\n".join(lines))


def test_no_indic_text_ever_reaches_the_llm(full_run):
    planner = full_run["planner"]
    assert len(planner.seen) >= 2  # the plan and the English draft
    for messages in planner.seen:
        assert not INDIC.search(json.dumps(messages, ensure_ascii=False))


def test_the_same_input_is_never_sent_twice(full_run):
    sent = [(r.model, r.fingerprint) for r in full_run["bodhan"].requests]
    assert sent and [n for n, c in Counter(sent).items() if c > 1] == []
    assert Counter(m for m, _ in sent) == {"indic-ocr": 1, "indic-translate": 2, "indic-speak": 1, "indic-transcribe": 1}


def test_second_identical_run_makes_no_paid_requests(full_run, monkeypatch, tmp_path):
    bodhan = full_run["bodhan"]
    before = Counter(r.model for r in bodhan.requests)
    run(tmp_path / "run2", full_run["pdf"])
    after = Counter(r.model for r in bodhan.requests)
    for model in ("indic-ocr", "indic-translate", "indic-speak"):
        assert after[model] == before[model], model


def test_second_identical_run_costs_nothing(full_run, tmp_path):
    assert report_of(full_run["out"])["budget"]["cost_inr"] > 0
    run(tmp_path / "run2", full_run["pdf"])
    second = report_of(tmp_path / "run2")
    assert second["steps"] and second["budget"]["cost_inr"] == 0


def test_infeasible_plan_is_a_clean_refusal(bodhan, monkeypatch, tmp_path):
    src = tmp_path / "in.txt"
    src.write_text("Hello")
    refusal = "French is not a supported language."
    planner = install(monkeypatch, {"understanding": "Translate to French.", "feasible": False, "refusal": refusal, "steps": []})
    out = tmp_path / "run"
    assert run(out, src, task="Translate this to French") == 0
    assert report_of(out)["status"] == "failed"
    assert bodhan.requests == []
    assert list((out / "artifacts").iterdir()) == []
    assert refusal in (out / "result.md").read_text(encoding="utf-8")
    assert len(planner.seen) == 1


def test_missing_input_stops_before_the_llm_and_the_backend(bodhan, monkeypatch, tmp_path):
    planner = install(monkeypatch, {})
    out = tmp_path / "run"
    assert run(out, tmp_path / "does-not-exist.pdf") == 0
    report = report_of(out)
    assert report["status"] == "failed" and report["errors"][0]["code"] == "MISSING_FILE"
    assert bodhan.requests == [] and planner.seen == []


def test_a_plan_over_budget_is_refused_without_spending(bodhan, monkeypatch, tmp_path):
    src = tmp_path / "in.txt"
    src.write_text("A line of English text to read aloud.")
    plan = {
        "understanding": "Read it aloud.",
        "steps": [{"id": "s1", "intent": "speak", "tool": "speak_text", "args": {"text_path": str(src), "language": "ta"}, "expected_chars": 2000}],
        "final_outputs": [{"role": "audio", "from": "s1"}],
    }
    planner = install(monkeypatch, plan)
    out = tmp_path / "run"
    assert run(out, src, extra=["--max-cost-inr", "0.05"]) == 0
    report = report_of(out)
    assert report["status"] in ("failed", "partial")
    assert report["budget"]["cost_inr"] <= 0.05 and report["quality_targets"]["within_budget"] is True
    assert bodhan.requests == []
    assert report["replans"] == 1 and len(planner.seen) == 2
