"""End-to-end runs of the CLI code path with a scripted planner and the offline
Bodhan fake. These prove the loop mechanics (plan gate, verification gate,
budgets, honest failure, output contract); live runs are in docs/evaluation.md."""

import json
import sys
from argparse import Namespace
from pathlib import Path

import pytest
from conftest import make_image, make_pdf
from fakes import FakeBodhan, ScriptedChat

from bodhan_agent.cli import run
from bodhan_agent.llm import PlannerLLM


@pytest.fixture(autouse=True)
def dummy_keys(monkeypatch):
    for k in ("BODHAN_TRANSCRIBE_KEY", "BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY", "BODHAN_OCR_KEY"):
        monkeypatch.setenv(k, "test-key")


def args(tmp_path, task, files, **kw):
    base = dict(task=task, files=[str(f) for f in files], out=str(tmp_path / "out"), max_steps=30, max_cost_inr=20.0, max_seconds=120.0,
                call_timeout=5.0, llm_timeout=5.0, concurrency=4, cache_dir=str(tmp_path / "cache"), no_cache=True, env_file=str(tmp_path / "none.env"),
                llm_input_inr_per_1m=None, llm_output_inr_per_1m=None)
    base.update(kw)
    return Namespace(**base)


def tool_results(messages):
    return [json.loads(m["content"]) for m in messages if m.get("role") == "tool"]


def last_ok(messages, key):
    for r in reversed(tool_results(messages)):
        if r.get("ok") and key in r:
            return r[key]
    raise AssertionError(f"no successful tool result with {key}")


def read_outputs(tmp_path):
    out = tmp_path / "out"
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    trace = [json.loads(line) for line in (out / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    return out, report, trace, (out / "result.md").read_text(encoding="utf-8")


async def test_multistep_worksheet_to_marathi_keeps_table(tmp_path):
    img = make_image(tmp_path / "worksheet.jpg")
    fake = FakeBodhan(ocr_pages=["Worksheet 4: Planets\n\n| Planet | Moons |\n| --- | --- |\n| Earth | 1 |\n| Mars | 2 |\n\nFill in the blanks."])
    chat = ScriptedChat([
        [("record_plan", {"steps": ["ocr_document", "translate_text to mr", "finish"],
                           "assumptions": ["source is English"], "deliverables": [{"kind": "translation", "language": "mr", "description": "Marathi worksheet"}]})],
        [("ocr_document", {"path": str(img)})],
        lambda m: [("translate_text", {"path": last_ok(m, "path"), "target_language": "mr", "output_name": "worksheet_mr"})],
        lambda m: [("finish", {"status": "completed", "final_answer": "Translated the worksheet into Marathi; the table is intact.",
                               "deliverables": [last_ok(m, "path")], "assumptions": ["source is English"]})],
    ])
    report = await run(args(tmp_path, "Translate this worksheet into Marathi and keep the table", [img]), transport=fake.transport,
                       llm=PlannerLLM(client=chat, model="scripted"))
    out, rep, trace, md = read_outputs(tmp_path)
    assert report["status"] == rep["status"] == "completed"
    deliverable = out / rep["deliverables"][0]["path"]
    assert deliverable.exists() and rep["deliverables"][0]["verification"]["status"] == "pass"
    assert rep["verification"]["all_checked"] and rep["cost"]["bodhan_billed_inr"] > 0
    steps = [e for e in trace if e["type"] == "tool_call"]
    for e in steps:  # the trace contract: plan, tool, arguments, result summary, latency, cost
        assert {"plan_version", "rationale", "tool", "arguments", "result_summary", "latency_s", "cost_inr"} <= set(e)
    assert any(e["type"] == "api_call" and e["step"] == steps[0]["step"] for e in trace)  # nested calls attributed
    assert "## Deliverables" in md and "worksheet_mr" in md


async def test_chapter_to_tamil_audio_summary_chain(tmp_path):
    pdf = make_pdf(tmp_path / "chapter.pdf", 3)
    fake = FakeBodhan(ocr_pages=["நீர் சுழற்சி பாடம் ஒன்று", "மழை மேகங்கள் பாடம் இரண்டு", "ஆறுகள் கடல் பாடம் மூன்று"])
    summary = "The chapter explains the water cycle: evaporation, clouds, rain and rivers returning water to the sea."
    chat = ScriptedChat([
        [("record_plan", {"steps": ["ocr", "translate en", "summarise", "translate ta", "speak"],
                           "assumptions": ["~110 words per minute"], "deliverables": [{"kind": "audio", "language": "ta"}, {"kind": "text", "language": "ta"}]})],
        [("ocr_document", {"path": str(pdf)})],
        lambda m: [("translate_text", {"path": last_ok(m, "path"), "target_language": "en"})],
        [("write_text", {"name": "summary_en.md", "content": summary})],
        lambda m: [("translate_text", {"path": last_ok(m, "path"), "target_language": "ta", "output_name": "summary_ta"})],
        lambda m: [("synthesize_speech", {"path": last_ok(m, "path"), "language": "ta", "target_duration_seconds": 120, "output_name": "summary_ta"})],
        lambda m: [("finish", {"status": "completed", "final_answer": "Made a Tamil audio summary.",
                               "deliverables": [last_ok(m, "path"), str(tmp_path / "out" / "artifacts" / "summary_ta.md")]})],
    ])
    await run(args(tmp_path, "Make a two-minute Tamil audio summary of this chapter", [pdf]), transport=fake.transport,
              llm=PlannerLLM(client=chat, model="scripted"))
    out, rep, trace, md = read_outputs(tmp_path)
    kinds = sorted(d["kind"] for d in rep["deliverables"])
    assert kinds == ["audio", "translation"]
    audio = next(d for d in rep["deliverables"] if d["kind"] == "audio")
    assert audio["path"].endswith(".mp3") and (out / audio["path"]).stat().st_size > 1000
    # the fake audio is far shorter than 120 s, so the duration check must warn rather than pass silently
    assert audio["verification"]["duration"]["target_s"] == 120 and rep["status"] == "completed_with_warnings"
    assert "AI-generated" in md
    assert [s["tool"] for s in rep["steps"]["tool_calls"]] == ["ocr_document", "translate_text", "write_text", "translate_text", "synthesize_speech"]


async def test_unsupported_language_ends_honestly_without_artifacts(tmp_path):
    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42")  # never decoded: the planner checks support first
    chat = ScriptedChat([
        [("record_plan", {"steps": ["check French support"], "deliverables": [{"kind": "translation", "language": "fr"}]})],
        [("translate_text", {"text": "test", "target_language": "fr"})],
        [("finish", {"status": "unsupported", "final_answer": "French is not supported by Bodhan's translation model, so no translation was made.",
                     "limitations": ["Indic-Translate covers English and 22 Indian languages only."]})],
    ])
    await run(args(tmp_path, "Translate this video into French", [video]), transport=FakeBodhan().transport, llm=PlannerLLM(client=chat, model="s"))
    out, rep, trace, md = read_outputs(tmp_path)
    assert rep["status"] == "unsupported" and rep["deliverables"] == []
    assert list((out / "artifacts").iterdir()) == []
    assert "No output files are presented" in md


async def test_gates_plan_provenance_and_unknown_deliverables(tmp_path):
    txt = tmp_path / "p.txt"
    txt.write_text("A short English paragraph about trees.", encoding="utf-8")
    chat = ScriptedChat([
        [("translate_text", {"path": str(txt), "target_language": "bn"})],  # refused: no plan yet
        [("record_plan", {"steps": ["translate", "speak"], "deliverables": [{"kind": "translation", "language": "bn"}]})],
        [("synthesize_speech", {"text": "আমি নিজে লিখেছি", "language": "bn"})],  # refused: LLM-authored Bengali
        [("translate_text", {"path": str(txt), "target_language": "bn"})],
        [("finish", {"status": "completed", "final_answer": "Done", "deliverables": ["artifacts/made_up.mp3"]})],  # refused
        lambda m: [("finish", {"status": "completed", "final_answer": "Translated into Bengali.", "deliverables": [last_ok(m, "path")]})],
    ])
    await run(args(tmp_path, "Translate to Bengali", [txt]), transport=FakeBodhan().transport, llm=PlannerLLM(client=chat, model="s"))
    out, rep, trace, md = read_outputs(tmp_path)
    errors = [s["error"] for s in rep["steps"]["tool_calls"] if s["error"]]
    assert errors[:2] == ["plan_required", "provenance_violation"]
    assert any(e["type"] == "finish_rejected" and "made_up.mp3" in str(e["reasons"]) for e in trace)
    assert rep["status"] == "completed"


async def test_tiny_budget_stops_cleanly(tmp_path):
    txt = tmp_path / "long.txt"
    txt.write_text("यह एक बहुत लंबा अनुच्छेद है जिसे बोलना है। " * 60, encoding="utf-8")
    chat = ScriptedChat([
        [("record_plan", {"steps": ["speak"], "deliverables": [{"kind": "audio", "language": "hi"}]})],
        [("synthesize_speech", {"path": str(txt), "language": "hi"})],
        lambda m: [("finish", {"status": "failed", "final_answer": "The budget of ₹0.05 is too small to synthesise this text (needs about ₹1.6)."
                               .replace("₹", "INR "), "limitations": ["budget too small"]})],
    ])
    await run(args(tmp_path, "Read this aloud", [txt], max_cost_inr=0.05), transport=FakeBodhan().transport, llm=PlannerLLM(client=chat, model="s"))
    out, rep, trace, md = read_outputs(tmp_path)
    assert rep["status"] == "failed" and rep["cost"]["total_counted_inr"] <= 0.05
    assert rep["steps"]["tool_calls"][0]["error"] == "budget_exceeded"
    assert rep["quality_targets"]["budget"]["within_budget"]


async def test_step_limit_forces_a_truthful_partial_report(tmp_path):
    txt = tmp_path / "p.txt"
    txt.write_text("Trees give shade.", encoding="utf-8")
    loop = [[("detect_language", {"path": str(txt)})]] * 20
    chat = ScriptedChat([[("record_plan", {"steps": ["loop forever"], "deliverables": [{"kind": "answer"}]})], *loop])
    await run(args(tmp_path, "Do something", [txt], max_steps=4), transport=FakeBodhan().transport, llm=PlannerLLM(client=chat, model="s"))
    out, rep, trace, md = read_outputs(tmp_path)
    assert rep["stop_reason"] == "step_limit" and rep["status"] == "failed" and rep["steps"]["used"] <= 5


async def test_missing_llm_configuration_still_writes_report(tmp_path, monkeypatch):
    for k in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    await run(args(tmp_path, "Anything", [tmp_path / "missing.wav"]), transport=FakeBodhan().transport)
    out, rep, trace, md = read_outputs(tmp_path)
    assert rep["status"] == "failed" and rep["stop_reason"] == "llm_not_configured"
    assert rep["inputs"]["missing"] == [str(tmp_path / "missing.wav")]


def test_cli_entrypoint_help_runs():
    import subprocess

    r = subprocess.run([sys.executable, "-m", "bodhan_agent", "--help"], capture_output=True, text=True)
    assert r.returncode == 0 and "--max-cost-inr" in r.stdout and "--files" in r.stdout
    assert Path(__file__).exists()
