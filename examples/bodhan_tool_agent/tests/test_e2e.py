"""End-to-end: the real MCP server in-process, a fake Bodhan HTTP backend, and a scripted planner.

The planner is scripted (fixed tool calls that read paths out of earlier tool
results), so everything else - inventory, MCP schemas and errors, guards,
verification gate, outputs - runs exactly as in production.
"""

from __future__ import annotations

import asyncio
import json
import math
import struct
import wave
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("bodhan_mcp.server", reason="MCP server not available yet")
fakes = pytest.importorskip("fakes", reason="tests/fakes.py not available yet")

from test_agent_loop import ScriptedLLM, call, last_result, turn  # noqa: E402

from bodhan_agent.agent import AgentConfig, TaskAgent  # noqa: E402
from bodhan_agent.report import RunOutcome  # noqa: E402
from bodhan_agent.tools_backend import McpToolBackend  # noqa: E402
from bodhan_mcp.config import MODEL_KEY_ENV, Settings  # noqa: E402
from bodhan_mcp.runtime import Runtime, set_runtime  # noqa: E402


def write_voice_note(path: Path, seconds: float = 6.0, rate: int = 16000) -> Path:
    """Tone bursts separated by pauses, so VAD has something to segment."""
    frames = bytearray()
    for i in range(int(seconds * rate)):
        t = i / rate
        voiced = (t % 2.0) < 1.4
        sample = int(9000 * math.sin(2 * math.pi * 220 * t)) if voiced else 0
        frames += struct.pack("<h", sample)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return path


def first_path(result: dict[str, Any], suffixes: tuple[str, ...]) -> str:
    for key, value in result.items():
        if isinstance(value, str) and key.endswith("_path") and value.lower().endswith(suffixes):
            return value
    raise AssertionError(f"no {suffixes} path in {result}")


def run_with_server(tmp_path: Path, task: str, inputs: list[str], llm: ScriptedLLM,
                    max_cost_inr: float = 20.0) -> RunOutcome:
    out = tmp_path / "out"
    settings = Settings(
        keys={m: "test-key" for m in MODEL_KEY_ENV},
        workdir=(out / "work").resolve(),
        cache_dir=(tmp_path / "cache").resolve(),
        max_cost_inr=max_cost_inr,
    )
    set_runtime(Runtime(settings, transport=fakes.make_fake_transport()))
    config = AgentConfig(task=task, inputs=inputs, out_dir=out, max_cost_inr=max_cost_inr)

    async def go() -> RunOutcome:
        async with McpToolBackend.inprocess() as backend:
            return await TaskAgent(config, llm, backend).run()

    try:
        return asyncio.run(go())
    finally:
        set_runtime(None)


def test_hindi_voice_note_to_english_subtitles(tmp_path: Path) -> None:
    wav = write_voice_note(tmp_path / "inputs" / "voice_note.wav")
    llm = ScriptedLLM([
        turn(call("submit_plan", goal="English subtitles for a Hindi voice note", feasibility="possible",
                  input_languages=["hi"], target_languages=["en"],
                  steps=["transcribe_audio (hi)", "translate_text segments to en", "make_subtitles srt"],
                  expected_outputs=["English SRT"]),
             content="Transcribe first, then translate the timed segments."),
        turn(call("transcribe_audio", path=str(wav), language="hi")),
        lambda m: turn(call("translate_text", target_language="en",
                            path=last_result(m, "transcribe_audio")["result"]["transcript_path"])),
        lambda m: turn(call("make_subtitles", format="srt",
                            segments_path=last_result(m, "translate_text")["result"].get("segments_path")
                            or last_result(m, "translate_text")["result"]["output_path"])),
        lambda m: turn(call("finish", status="completed", answer="English subtitles for the voice note.",
                            artifacts=[first_path(last_result(m, "make_subtitles")["result"], (".srt",))],
                            assumptions=["The voice note is Hindi, as stated."])),
    ])
    outcome = run_with_server(tmp_path, "Transcribe this Hindi voice note and give me English subtitles",
                              [str(wav.parent)], llm)

    assert outcome.status == "completed", outcome.answer + " | " + "; ".join(outcome.verification_failures)
    out = tmp_path / "out"
    srt = [a for a in outcome.artifacts if a.name.endswith(".srt")]
    assert len(srt) == 1 and srt[0].verified is True
    assert "-->" in (out / srt[0].rel_path).read_text(encoding="utf-8")

    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["tool_calls_by_name"].get("verify_translation") == 1  # run by the gate
    assert report["total_cost_inr"] > 0 and report["budget"]["exhausted"] is None
    events = [json.loads(x) for x in (out / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    tools_in_order = [e["tool"] for e in events if e["type"] in ("tool", "verify")]
    assert tools_in_order == ["transcribe_audio", "translate_text", "make_subtitles", "verify_translation"]
    assert any(e["type"] == "llm" and e["reasoning"] for e in events)
    assert "Assumptions" in (out / "result.md").read_text(encoding="utf-8")


def test_translate_into_french_is_impossible(tmp_path: Path) -> None:
    src = tmp_path / "inputs" / "note.txt"
    src.parent.mkdir(parents=True)
    src.write_text("The school opens at nine tomorrow.", encoding="utf-8")
    llm = ScriptedLLM([
        turn(call("list_languages_and_voices")),
        turn(call("submit_plan", goal="Translate the note into French", feasibility="impossible",
                  feasibility_reason="French is not among the supported languages", target_languages=["fr"],
                  steps=[], expected_outputs=["French text"])),
        turn(call("finish", status="impossible",
                  answer="French is not supported by the Bodhan translation model, so nothing was produced.")),
    ])
    outcome = run_with_server(tmp_path, "Translate this into French", [str(src)], llm)
    out = tmp_path / "out"
    assert outcome.status == "impossible" and outcome.exit_code == 2
    assert outcome.artifacts == [] and list((out / "artifacts").iterdir()) == []
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["total_cost_inr"] == 0
    assert "French" in (out / "result.md").read_text(encoding="utf-8")


def test_stdio_server_exposes_same_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BODHAN_WORKDIR", str(tmp_path / "work"))

    async def go() -> list[str]:
        async with McpToolBackend.stdio() as backend:
            return sorted(s.name for s in await backend.list_tools())

    names = asyncio.run(go())
    assert {"inspect_file", "transcribe_audio", "translate_text", "synthesize_speech", "ocr_document",
            "verify_speech", "verify_translation", "make_subtitles"} <= set(names)
