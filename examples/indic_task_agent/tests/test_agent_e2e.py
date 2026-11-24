"""End-to-end: the real agent loop, the real MCP server as a subprocess over stdio, a fake Bodhan API
and a scripted LLM. Covers a multi-step success, an impossible task, prompt injection, budget
exhaustion, the no-Indic-text guard and fabricated artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from bodhan_agent.agent import Agent, Budgets
from bodhan_agent.mcp_bridge import ToolBridge
from bodhan_agent.report import write_outputs
from tests.fakes.fake_bodhan import tone_wav
from tests.fakes.fake_llm import ScriptedLLM, result


def _bridge_factory(fake, tmp_path: Path):
    def make(env: dict[str, str]) -> ToolBridge:
        return ToolBridge(
            env={
                **env,
                "BODHAN_BASE_URL": fake.url,
                "BODHAN_API_KEY": "test-key",
                "BODHAN_CACHE_DIR": str(tmp_path / "cache"),
                "BODHAN_MAX_RETRIES": "1",
            }
        )

    return make


async def _run(fake, tmp_path, task, files, llm, **budget):
    out = tmp_path / "out"
    agent = Agent(task, [str(f) for f in files], out, Budgets(**budget), llm, _bridge_factory(fake, tmp_path))
    state = await agent.run()
    report = write_outputs(state, llm.model)
    trace = [json.loads(line) for line in (out / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    return state, report, trace, out


def _page(path: Path, width: int) -> Path:
    im = Image.new("RGB", (width, 600), "white")
    ImageDraw.Draw(im).rectangle([40, 40, 300, 200], fill="black")
    im.save(path)
    return path


PLAN = {
    "understanding": "x",
    "deliverables": ["x"],
    "feasible": True,
    "steps": ["x"],
    "estimated_cost_inr": 0.1,
    "assumptions": ["Source language assumed to be Hindi as stated."],
}


async def test_subtitles_multistep_success(fake, tmp_path):
    clip = tmp_path / "inputs" / "voice_note.wav"
    clip.parent.mkdir()
    tone_wav(clip, 40, 220, gaps=[(6, 6.4), (13, 13.3), (21, 21.5), (30, 30.4)])
    turns = [
        lambda s: [
            (
                "transcribe_audio",
                {"path": str(clip), "language": "hi", "for_subtitles": True, "why": "get timed Hindi text"},
            )
        ],
        lambda s: [
            (
                "translate_text",
                {
                    "target_language": "en",
                    "input_path": result(s, "transcribe_audio")["segments_path"],
                    "why": "English cues",
                },
            )
        ],
        lambda s: [
            (
                "make_subtitles",
                {"segments_path": result(s, "translate_text")["output_path"], "format": "srt", "why": "write SRT"},
            )
        ],
        lambda s: [
            (
                "finish",
                {
                    "status": "success",
                    "answer_en": "English subtitles are attached.",
                    "artifacts": [
                        {
                            "path": result(s, "make_subtitles")["subtitle_path"],
                            "name": "voice_note.en.srt",
                            "description": "English subtitles",
                        }
                    ],
                },
            )
        ],
    ]
    state, report, trace, out = await _run(
        fake,
        tmp_path,
        "Transcribe this Hindi voice note and give me English subtitles",
        [clip.parent],
        ScriptedLLM(PLAN, turns),
    )
    assert report["status"] == "success", report
    srt = (out / "artifacts" / "voice_note.en.srt").read_text(encoding="utf-8")
    assert "-->" in srt and "word" in srt
    assert report["artifacts"][0]["verified"] is True
    assert 0 < report["cost"]["total_inr"] < 0.2
    kinds = [t["kind"] for t in trace]
    assert kinds[0] == "start" and "plan" in kinds and kinds[-1] == "end"
    tool_lines = [t for t in trace if t["kind"] == "tool"]
    assert all({"why", "args", "latency_ms", "cost_inr", "result_summary"} <= set(t) for t in tool_lines)
    assert all(t["args"].get("output_dir") == str(out.resolve() / "work") for t in tool_lines)
    assert (out / "result.md").read_text(encoding="utf-8").startswith("# Task result")


async def test_impossible_language_has_no_output(fake, tmp_path):
    video = tmp_path / "talk.wav"
    tone_wav(video, 5, 220)
    plan = {
        **PLAN,
        "feasible": False,
        "infeasible_reason": "French is not supported by Bodhan's models, so no translation was produced.",
    }
    llm = ScriptedLLM(plan, [])
    state, report, trace, out = await _run(fake, tmp_path, "Translate this video into French", [video], llm)
    assert report["status"] == "impossible"
    assert report["artifacts"] == [] and list((out / "artifacts").iterdir()) == []
    assert "French" in report["answer_en"]
    assert report["cost"]["total_inr"] == 0
    assert llm.calls == 1


async def test_prompt_injection_is_flagged_and_table_kept(fake, tmp_path):
    page = _page(tmp_path / "worksheet.png", 850)
    turns = [
        lambda s: [("ocr_document", {"path": str(page), "why": "read the worksheet"})],
        lambda s: [
            (
                "translate_text",
                {
                    "target_language": "mr",
                    "input_path": result(s, "ocr_document")["output_path"],
                    "why": "translate keeping table",
                },
            )
        ],
        lambda s: [
            (
                "finish",
                {
                    "status": "success",
                    "answer_en": "Marathi worksheet attached; an embedded instruction in the image was ignored.",
                    "artifacts": [
                        {"path": result(s, "translate_text")["output_path"], "description": "Marathi Markdown"}
                    ],
                },
            )
        ],
    ]
    llm = ScriptedLLM(PLAN, turns)
    state, report, trace, out = await _run(
        fake, tmp_path, "Translate this worksheet into Marathi and keep the table", [page], llm
    )
    assert report["status"] == "success"
    assert report["security_flags"], "injection text should be flagged"
    md = next((out / "artifacts").iterdir()).read_text(encoding="utf-8")
    assert md.count("|") >= 9 and "|---|---|" in md
    # the OCR text reached the LLM only inside an untrusted wrapper with a security note
    tool_msgs = [
        m for m in llm.history[-1] if m.get("role") == "tool" and "untrusted_file_content" in m.get("content", "")
    ]
    assert tool_msgs and "security_note" in tool_msgs[0]["content"]


async def test_budget_too_small_stops_cleanly(fake, tmp_path):
    text = tmp_path / "para.txt"
    text.write_text("আজ বিদ্যালয়ে সভা হবে। " * 400, encoding="utf-8")
    turns = [
        lambda s: [
            ("synthesize_speech", {"language": "bn", "input_path": str(text), "gender": "female", "why": "read aloud"})
        ],
        lambda s: [
            (
                "finish",
                {"status": "failed", "answer_en": "The budget is too small to synthesise this text.", "artifacts": []},
            )
        ],
    ]
    state, report, trace, out = await _run(
        fake,
        tmp_path,
        "Read this paragraph aloud in Bengali with a female voice",
        [text],
        ScriptedLLM(PLAN, turns),
        max_cost_inr=0.5,
    )
    assert report["status"] == "failed"
    assert report["cost"]["total_inr"] <= 0.5
    err = next(t for t in trace if t["kind"] == "tool")
    assert err["ok"] is False and err["result_summary"]["error"]["code"] == "budget_exceeded"
    assert fake.state.calls.get("/v1/audio/speech", 0) == 0  # refused before spending


async def test_guard_blocks_llm_written_indic_text_and_fake_artifacts(fake, tmp_path):
    text = tmp_path / "en.txt"
    text.write_text("School is closed tomorrow.", encoding="utf-8")
    turns = [
        lambda s: [
            ("synthesize_speech", {"language": "ta", "text": "நாளை பள்ளி விடுமுறை.", "why": "speak my own Tamil"})
        ],
        lambda s: [
            (
                "finish",
                {"status": "success", "answer_en": "Done", "artifacts": [{"path": str(text), "description": "fake"}]},
            )
        ],
        lambda s: [
            (
                "finish",
                {
                    "status": "failed",
                    "answer_en": "I could not produce verified audio.",
                    "artifacts": [{"path": str(text), "description": "fake"}],
                },
            )
        ],
    ]
    llm = ScriptedLLM(PLAN, turns)
    state, report, trace, out = await _run(fake, tmp_path, "Read this in Tamil", [text], llm)
    guard = next(t for t in trace if t["kind"] == "guard")
    assert guard["error_code"] == "policy_violation"
    assert fake.state.calls.get("/v1/audio/speech", 0) == 0
    assert report["status"] == "failed" and report["artifacts"] == []


async def test_step_limit_wraps_up(fake, tmp_path):
    clip = tmp_path / "c.wav"
    tone_wav(clip, 4, 330)
    turns = [
        lambda s: [("transcribe_audio", {"path": str(clip), "why": "detect language"})],
        lambda s: [("read_text", {"path": result(s, "transcribe_audio")["transcript_path"], "why": "look"})],
        lambda s: [
            (
                "finish",
                {
                    "status": "partial",
                    "answer_en": "Stopped at the step limit after transcribing.",
                    "artifacts": [
                        {"path": result(s, "transcribe_audio")["transcript_path"], "description": "Telugu transcript"}
                    ],
                },
            )
        ],
    ]
    llm = ScriptedLLM(PLAN, turns)
    state, report, trace, out = await _run(fake, tmp_path, "Which language is this clip?", [clip], llm, max_steps=1)
    assert report["stop_reason"] == "step_limit"
    assert report["steps"]["tool_calls"] == 1
    assert llm.tools_offered[-1] == ["finish"]


@pytest.mark.parametrize("missing", ["does/not/exist.wav"])
async def test_missing_file_is_reported_to_planner(fake, tmp_path, missing):
    plan = {**PLAN, "feasible": False, "infeasible_reason": "The input file does not exist."}
    llm = ScriptedLLM(plan, [])
    state, report, trace, out = await _run(fake, tmp_path, "Transcribe this", [missing], llm)
    assert report["status"] == "impossible"
    brief = llm.history[0][1]["content"]
    assert "does not exist" in brief
