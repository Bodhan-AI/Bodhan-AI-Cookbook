"""Tests for generic impossible task handling and budget stop enforcement.

Verifies that impossibility is decided by general code (supported language list,
file existence, file type/media probing, tool availability, budget estimates),
with zero paid Bodhan calls, zero fabricated artifacts, and diverse phrasing:
(a) MP4 video file with "translate this video into French"
(b) Unsupported foreign language (e.g. Spanish)
(c) Missing input file
(d) Capability not supported by available tools (email, web browsing)
(e) Budget too small to finish (--max-cost-inr 0.01 on a 2-page PDF)
"""

from __future__ import annotations

import json
from pathlib import Path
import pymupdf
import pytest

from bodhan_agent.llm import LLMResponse, ScriptedFakeLLMClient, ToolCallItem
from bodhan_agent.loop import AgentLoop


def create_dummy_pdf(path: Path, num_pages: int = 2) -> None:
    """Creates a minimal valid PDF with num_pages using pymupdf."""
    doc = pymupdf.open()
    for _ in range(num_pages):
        page = doc.new_page()
        page.insert_text((50, 50), "Document page text content for OCR.")
    doc.save(str(path))
    doc.close()


@pytest.mark.asyncio
async def test_case_a_unsupported_video_mp4(tmp_path: Path):
    """Case (a): Video file with task 'translate this video into French' -> impossible."""
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    out_dir = tmp_path / "out_case_a"

    mp4_file = files_dir / "lecture_recording.mp4"
    mp4_file.write_bytes(b"\x00\x00\x00\x20ftypisom")

    task = "Please translate this video lecture in lecture_recording.mp4 into French subtitles."
    agent = AgentLoop(task=task, files_dir=files_dir, out_dir=out_dir, verify=False)
    res = await agent.run()

    assert res["status"] == "impossible"
    assert res["total_cost_inr"] == 0.0
    assert res["http_requests_count"] == 0

    report_path = out_dir / "report.json"
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "impossible"
    assert report["total_cost_inr"] == 0.0
    assert report["artifacts"] == []
    print("\n--- Case (a) report.json ---")
    print(json.dumps(report, indent=2))


@pytest.mark.asyncio
async def test_case_b_unsupported_language_spanish(tmp_path: Path):
    """Case (b): Task asking for a language not in the 23 (Spanish) -> impossible via finish tool."""
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    out_dir = tmp_path / "out_case_b"

    doc_file = files_dir / "statement.txt"
    doc_file.write_text("All students must report to room 101 tomorrow.", encoding="utf-8")

    # LLM autonomously calls finish(status='impossible')
    fake_llm = ScriptedFakeLLMClient(
        [
            LLMResponse(
                content="Plan:\n1. Check language support for Spanish.\n2. Spanish is not among the 22 Indian languages or English supported by Bodhan AI.\n3. Conclude impossible.",
                prompt_tokens=40,
                completion_tokens=30,
                total_tokens=70,
            ),
            LLMResponse(
                tool_calls=[
                    ToolCallItem(
                        id="call_finish_1",
                        name="finish",
                        arguments={
                            "status": "impossible",
                            "explanation": "Spanish is an unsupported language. Bodhan AI Indic models only support 22 scheduled Indian languages and English.",
                        },
                    )
                ],
                prompt_tokens=100,
                completion_tokens=40,
                total_tokens=140,
            ),
        ]
    )

    task = (
        "Kindly examine statement.txt and translate the announcement into Spanish for our visitors."
    )
    agent = AgentLoop(
        task=task, files_dir=files_dir, out_dir=out_dir, llm_client=fake_llm, verify=False
    )
    res = await agent.run()

    assert res["status"] == "impossible"
    assert res["total_cost_inr"] == 0.0
    assert res["http_requests_count"] == 0

    report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "impossible"
    assert report["total_cost_inr"] == 0.0
    assert report["artifacts"] == []
    print("\n--- Case (b) report.json ---")
    print(json.dumps(report, indent=2))


@pytest.mark.asyncio
async def test_case_c_missing_input_file(tmp_path: Path):
    """Case (c): Missing input file -> impossible."""
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    out_dir = tmp_path / "out_case_c"

    task = "Transcribe the audio interview in non_existent_interview.wav and save to transcript.txt"
    agent = AgentLoop(task=task, files_dir=files_dir, out_dir=out_dir, verify=False)
    res = await agent.run()

    assert res["status"] == "impossible"
    assert res["total_cost_inr"] == 0.0
    assert res["http_requests_count"] == 0

    report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "impossible"
    assert "not found" in report["explanation"].lower()
    print("\n--- Case (c) report.json ---")
    print(json.dumps(report, indent=2))


@pytest.mark.asyncio
async def test_case_d_missing_capability_email_or_web(tmp_path: Path):
    """Case (d): Task requiring unavailable capability (email/web) -> impossible via finish tool."""
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    out_dir = tmp_path / "out_case_d"

    notes_file = files_dir / "minutes.txt"
    notes_file.write_text("Meeting minutes: Project is on track.", encoding="utf-8")

    fake_llm = ScriptedFakeLLMClient(
        [
            LLMResponse(
                content="Plan:\n1. Check available tools for emailing capabilities.\n2. Server has no email dispatch tool.\n3. Conclude impossible.",
                prompt_tokens=45,
                completion_tokens=30,
                total_tokens=75,
            ),
            LLMResponse(
                tool_calls=[
                    ToolCallItem(
                        id="call_finish_d",
                        name="finish",
                        arguments={
                            "status": "impossible",
                            "explanation": "Email dispatch capability is not available on the Bodhan MCP server. Only text translation, OCR, transcription, and speech synthesis are supported.",
                        },
                    )
                ],
                prompt_tokens=110,
                completion_tokens=40,
                total_tokens=150,
            ),
        ]
    )

    task = "Summarize minutes.txt and email the executive summary to board@company.com immediately."
    agent = AgentLoop(
        task=task, files_dir=files_dir, out_dir=out_dir, llm_client=fake_llm, verify=False
    )
    res = await agent.run()

    assert res["status"] == "impossible"
    assert res["total_cost_inr"] == 0.0
    assert res["http_requests_count"] == 0

    report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "impossible"
    assert "email" in report["explanation"].lower()
    print("\n--- Case (d) report.json ---")
    print(json.dumps(report, indent=2))


@pytest.mark.asyncio
async def test_case_e_budget_too_small_on_2page_pdf(tmp_path: Path):
    """Case (e): Budget too small (--max-cost-inr 0.01 on a 2-page PDF where OCR costs Rs 0.40) -> budget_stopped."""
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    out_dir = tmp_path / "out_case_e"

    pdf_file = files_dir / "document_2page.pdf"
    create_dummy_pdf(pdf_file, num_pages=2)

    # LLM attempts to call indic_ocr on the 2-page PDF
    fake_llm = ScriptedFakeLLMClient(
        [
            LLMResponse(
                content="Plan:\n1. Extract text from document_2page.pdf using indic_ocr.\n2. Translate extracted text.",
                prompt_tokens=50,
                completion_tokens=25,
                total_tokens=75,
            ),
            LLMResponse(
                tool_calls=[
                    ToolCallItem(
                        id="call_ocr_budget",
                        name="indic_ocr",
                        arguments={"file_path": str(pdf_file), "max_pages": 10},
                    )
                ],
                prompt_tokens=120,
                completion_tokens=30,
                total_tokens=150,
            ),
        ]
    )

    # Budget cap is Rs 0.01; OCR for 2 pages costs 2 * 0.20 = Rs 0.40
    task = "Perform OCR on document_2page.pdf and summarize findings."
    agent = AgentLoop(
        task=task,
        files_dir=files_dir,
        out_dir=out_dir,
        max_cost_inr=0.01,
        llm_client=fake_llm,
        verify=False,
    )
    res = await agent.run()

    assert res["status"] == "budget_stopped"
    assert res["total_cost_inr"] == 0.0
    assert res["http_requests_count"] == 0

    report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "budget_stopped"
    assert report["total_cost_inr"] == 0.0
    assert "budget" in report["explanation"].lower()
    print("\n--- Case (e) report.json ---")
    print(json.dumps(report, indent=2))


# ------------------------------------------------------------------------------
# P1 Tests: Topic vs Requested Unsupported Languages
# ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "task_text, filename, file_content, target_lang",
    [
        (
            "Summarize this chapter about the French Revolution in Tamil",
            "chapter.txt",
            "The French Revolution began in 1789 and transformed France.",
            "ta",
        ),
        (
            "Translate this recipe for Spanish rice into Hindi",
            "recipe.txt",
            "Spanish rice is made with tomatoes, onions, garlic and rice.",
            "hi",
        ),
        (
            "Read this German-engineering article aloud in Bengali",
            "article.txt",
            "German engineering is famous for precision and automotive quality.",
            "bn",
        ),
    ],
)
@pytest.mark.asyncio
async def test_unsupported_language_as_topic_proceeds(
    tmp_path: Path, task_text: str, filename: str, file_content: str, target_lang: str
):
    """When an unsupported world language is only the TOPIC, the task must PROCEED."""
    from bodhan_agent.loop import check_task_feasibility

    files_dir = tmp_path / "files"
    files_dir.mkdir()
    sample_file = files_dir / filename
    sample_file.write_text(file_content, encoding="utf-8")

    # 1. Feasibility check must pass
    feasible, reason = check_task_feasibility(task_text, files_dir)
    assert feasible is True, f"Feasibility check unexpectedly rejected topic mention: {reason}"
    assert reason is None

    # 2. Agent execution must proceed (not abort as impossible)
    out_dir = tmp_path / "out_topic"
    fake_llm = ScriptedFakeLLMClient(
        [
            LLMResponse(
                content=f"Plan: Process the {filename} text and produce {target_lang} output.",
                prompt_tokens=40,
                completion_tokens=25,
                total_tokens=65,
            ),
            LLMResponse(
                tool_calls=[
                    ToolCallItem(
                        id="finish_call",
                        name="finish",
                        arguments={
                            "status": "completed",
                            "explanation": f"Successfully processed topic in {target_lang}.",
                        },
                    )
                ],
                prompt_tokens=80,
                completion_tokens=20,
                total_tokens=100,
            ),
        ]
    )

    agent = AgentLoop(
        task=task_text, files_dir=files_dir, out_dir=out_dir, llm_client=fake_llm, verify=False
    )
    res = await agent.run()
    assert res["status"] == "completed"
    assert res["status"] != "impossible"


@pytest.mark.parametrize(
    "task_text, expected_lang",
    [
        ("dub this into German", "German"),
        ("make a Japanese audio version", "Japanese"),
    ],
)
@pytest.mark.asyncio
async def test_unsupported_language_requested_must_stop(
    tmp_path: Path, task_text: str, expected_lang: str
):
    """When an unsupported world language is the REQUESTED language, execution must STOP immediately."""
    from bodhan_agent.loop import check_task_feasibility

    files_dir = tmp_path / "files"
    files_dir.mkdir()
    out_dir = tmp_path / f"out_stop_{expected_lang.lower()}"

    # 1. Feasibility check must reject
    feasible, reason = check_task_feasibility(task_text, files_dir)
    assert feasible is False
    assert expected_lang in reason

    # 2. Agent run must terminate with impossible and 0 spend
    agent = AgentLoop(task=task_text, files_dir=files_dir, out_dir=out_dir, verify=False)
    res = await agent.run()

    assert res["status"] == "impossible"
    assert res["total_cost_inr"] == 0.0
    assert res["http_requests_count"] == 0
    assert expected_lang in res["explanation"]


# ------------------------------------------------------------------------------
# C2 Tests: New Phrasings with World Languages Table
# ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "task_text, expected_lang",
    [
        ("Please dub this into German audio for our international team.", "German"),
        ("Kindly convert the notes to Spanish so the visitors can read them.", "Spanish"),
        ("Make a Japanese audio version from our lecture recording.", "Japanese"),
    ],
)
@pytest.mark.asyncio
async def test_c2_world_languages_phrasings(tmp_path: Path, task_text: str, expected_lang: str):
    """Verify that unsupported world languages are caught with zero paid calls across varied phrasings."""
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    out_dir = tmp_path / f"out_{expected_lang.lower()}"

    agent = AgentLoop(task=task_text, files_dir=files_dir, out_dir=out_dir, verify=False)
    res = await agent.run()

    assert res["status"] == "impossible"
    assert res["total_cost_inr"] == 0.0
    assert res["http_requests_count"] == 0
    assert expected_lang in res["explanation"]

    report_path = out_dir / "report.json"
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "impossible"
    assert expected_lang in report["explanation"]
    assert report["llm_ms"] == 0.0
    assert report["tool_ms"] == 0.0
    assert "time_breakdown_ms" in report


@pytest.mark.asyncio
async def test_c5_time_split_reporting(tmp_path: Path):
    """Verify that report.json and trace.jsonl track llm_ms and tool_ms per step and in total."""
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    out_dir = tmp_path / "out_timing"

    sample_doc = files_dir / "input.txt"
    sample_doc.write_text("A simple test notice.", encoding="utf-8")

    fake_llm = ScriptedFakeLLMClient(
        [
            LLMResponse(
                content="Plan:\n1. Finish task.\n2. Report success.",
                prompt_tokens=30,
                completion_tokens=20,
                total_tokens=50,
            ),
            LLMResponse(
                tool_calls=[
                    ToolCallItem(
                        id="finish_time_test",
                        name="finish",
                        arguments={
                            "status": "completed",
                            "explanation": "Completed timing validation run.",
                        },
                    )
                ],
                prompt_tokens=60,
                completion_tokens=25,
                total_tokens=85,
            ),
        ]
    )

    agent = AgentLoop(
        task="Read input.txt and finish",
        files_dir=files_dir,
        out_dir=out_dir,
        llm_client=fake_llm,
        verify=False,
    )
    res = await agent.run()
    assert res["status"] == "completed"

    report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    assert "llm_ms" in report
    assert "tool_ms" in report
    assert "time_breakdown_ms" in report
    assert report["time_breakdown_ms"]["llm_ms"] >= 0.0
    assert report["time_breakdown_ms"]["tool_ms"] >= 0.0

    trace_lines = [
        json.loads(line)
        for line in (out_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    llm_turns = [r for r in trace_lines if r.get("type") == "llm_turn"]
    steps = [r for r in trace_lines if "step" in r]

    assert len(llm_turns) >= 2
    for turn in llm_turns:
        assert "latency_ms" in turn

    assert len(steps) >= 1
    for step_rec in steps:
        assert "llm_ms" in step_rec
        assert "tool_ms" in step_rec
