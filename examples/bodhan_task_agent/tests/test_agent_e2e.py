from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path
from typing import Any

import pytest
from conftest import speech_wav

import bodhan_agent.__main__ as cli
from bodhan_agent.core.config import RunSettings
from bodhan_agent.schemas.llm import LlmReply, ToolCall
from bodhan_agent.schemas.mcp import McpResult, McpToolSpec
from bodhan_agent.schemas.trace import LlmTokens

ENV = {"LLM_BASE_URL": "https://llm.test/v1", "LLM_MODEL": "m", "LLM_API_KEY": "sk-test-secret-123456"}
NOTICE_HI = "विद्यालय सूचना\n\nविज्ञान मेला शुक्रवार को मुख्य हॉल में होगा। हर छात्र एक कार्यशील मॉडल लाए।"
NOTICE_EN = (
    "School notice\n\nThe science fair will be held on Friday in the main hall. "
    "Every student should bring one working model."
)
SUMMARY_EN = "The science fair is on Friday in the main hall, and every student should bring a working model."
SUMMARY_HI = "विज्ञान मेला शुक्रवार को मुख्य हॉल में है, और हर छात्र को एक कार्यशील मॉडल लाना चाहिए।"
LABEL = "AI-generated speech (Bodhan Indic-Speak)"
COSTS = {"ocr_document": 0.2, "translate_text": 0.001, "synthesize_speech": 0.06}
PLAN = {
    "task_en": "Make a short Hindi audio summary of this notice.",
    "goal": "A Hindi audio summary of the notice and its Hindi text.",
    "input_notes": ["The input is a photo of a notice; its language is not stated."],
    "deliverables": [
        {"id": "D1", "kind": "audio", "language": "hi", "format": "wav", "produced_by": "synthesize_speech",
         "description": "Hindi audio summary"},
        {"id": "D2", "kind": "text", "language": "hi", "format": "md", "produced_by": "translate_text",
         "description": "Hindi summary text"},
    ],
    "steps": [
        {"id": "S1", "intent": "Read the notice", "why": "Text in an image needs OCR.", "tool": "ocr_document"},
        {"id": "S2", "intent": "Translate to English", "why": "I only read English (language policy).",
         "tool": "translate_text", "depends_on": ["S1"]},
        {"id": "S3", "intent": "Read the English text", "why": "Needed to summarise it.", "tool": "read",
         "depends_on": ["S2"]},
        {"id": "S4", "intent": "Write an English summary", "why": "The LLM writes English only.", "tool": "write",
         "depends_on": ["S3"]},
        {"id": "S5", "intent": "Translate the summary to Hindi", "why": "The user wants Hindi.",
         "tool": "translate_text", "depends_on": ["S4"]},
        {"id": "S6", "intent": "Speak the Hindi summary", "why": "The user wants audio.",
         "tool": "synthesize_speech", "depends_on": ["S5"]},
    ],
    "feasibility": {"status": "feasible"},
}  # fmt: skip


def png_bytes(width: int = 8, height: int = 8) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    rows = b"".join(b"\x00" + b"\xff\xff\xff" * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def usage(tool: str) -> dict[str, Any]:
    return {"model_calls": 1, "http_attempts": 1, "estimated_cost_inr": COSTS[tool], "billing_uncertain": False,
            "cache_hits": 0, "possibly_billed_failed_attempts": 0, "latency_ms": 1}  # fmt: skip


class ScriptedLlm:
    script: list[Any] = []
    seen: list[list[dict[str, Any]]] = []

    def __init__(self, settings: Any) -> None:
        self.turn = 0

    async def complete(self, messages: Any, tools: Any = None, *, deadline: float | None = None) -> LlmReply:
        ScriptedLlm.seen.append(list(messages))
        self.turn += 1
        step = ScriptedLlm.script.pop(0) if ScriptedLlm.script else "Nothing more to do."
        tokens = LlmTokens(prompt=10, completion=5)
        if isinstance(step, str):
            return LlmReply(content=step, latency_ms=1, attempts=1, finish_reason="stop", tokens=tokens)
        name, args = step(messages) if callable(step) else step
        call = ToolCall(id=f"c{self.turn}", name=name, raw_arguments=json.dumps(args), arguments=args)
        return LlmReply(tool_calls=[call], latency_ms=1, attempts=1, finish_reason="tool_calls", tokens=tokens)

    async def aclose(self) -> None:
        return None


class FakeBodhan:
    calls: list[tuple[str, dict[str, Any]]] = []

    def __init__(self, settings: RunSettings, env: Any = None) -> None:
        self.work = settings.work_dir
        schema = {"type": "object", "properties": {}}
        self.tools = [McpToolSpec(name=name, description=name, input_schema=schema) for name in COSTS]

    async def start(self) -> FakeBodhan:
        return self

    async def aclose(self) -> None:
        return None

    def write(self, name: str, content: str | bytes) -> str:
        path = self.work / name
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
        return str(path)

    async def call(self, name: str, args: dict[str, Any], *, deadline: float | None = None) -> McpResult:
        FakeBodhan.calls.append((name, args))
        if name == "ocr_document":
            data = {
                "output_path": self.write("notice.ocr.md", NOTICE_HI),
                "blocks_path": self.write("notice.ocr.blocks.json", "{}"),
                "text": NOTICE_HI, "text_is_preview": False, "pages_read": [1], "unread_pages": [],
                "script": "Devanagari", "language": None, "language_source": "undetermined",
                "candidate_languages": ["hi", "mr", "ne"], "untrusted_content": True, "warnings": [],
            }  # fmt: skip
        elif name == "translate_text" and args["target_language"] == "en":
            data = {
                "output_path": self.write("notice.en.md", NOTICE_EN), "target_language": "en",
                "script": "native", "translation": NOTICE_EN, "translation_is_preview": False,
                "source_chars": len(NOTICE_HI), "translated_chars": len(NOTICE_EN), "chunks": 1,
                "verification_status": "passed", "untrusted_content": True,
            }  # fmt: skip
        elif name == "translate_text":
            data = {
                "output_path": self.write("summary.hi.md", SUMMARY_HI), "target_language": "hi",
                "script": "native", "translation": SUMMARY_HI, "translation_is_preview": False,
                "source_chars": len(SUMMARY_EN), "translated_chars": len(SUMMARY_HI), "chunks": 1,
                "verification_status": "passed", "untrusted_content": False,
            }  # fmt: skip
        else:
            data = {
                "output_path": self.write("summary.hi.wav", speech_wav(2.0)), "format": "wav", "voice": "Kavya",
                "voice_gender": "female", "language": "hi", "duration_s": 6.5, "chars": len(SUMMARY_HI),
                "chunks": 1, "label": LABEL, "verification": {"status": "passed", "word_recall": 0.96},
            }  # fmt: skip
        data["usage"] = usage(name)
        return McpResult(tool=name, ok=True, latency_ms=1, data=data)


@pytest.fixture(autouse=True)
def fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    ScriptedLlm.script, ScriptedLlm.seen, FakeBodhan.calls = [], [], []
    monkeypatch.setattr(cli, "LlmClient", ScriptedLlm)
    monkeypatch.setattr(cli, "McpSession", FakeBodhan)


def script(settings: RunSettings, photo: Path) -> list[Any]:
    work = settings.work_dir
    return [
        json.dumps(PLAN),
        ("ocr_document", {"reason": "read the notice", "plan_step": "S1", "file_path": str(photo)}),
        ("translate_text", {"reason": "to English", "plan_step": "S2", "text_path": str(work / "notice.ocr.md"),
                            "target_language": "en"}),
        ("read", {"reason": "understand it", "plan_step": "S3", "path": str(work / "notice.en.md")}),
        ("write", {"reason": "summary", "plan_step": "S4", "path": "summary.en.md", "content": SUMMARY_EN}),
        ("translate_text", {"reason": "to Hindi", "plan_step": "S5", "text_path": str(work / "summary.en.md"),
                            "target_language": "hi"}),
        ("synthesize_speech", {"reason": "speak it", "plan_step": "S6", "text_path": str(work / "summary.hi.md"),
                               "language": "hi", "format": "wav"}),
        ("finish", {"reason": "done", "answer_en": "Made a Hindi audio summary of the science fair notice.",
                    "deliverables": [{"id": "D1", "path": str(work / "summary.hi.wav")},
                                     {"id": "D2", "path": str(work / "summary.hi.md")}]}),
    ]  # fmt: skip


async def test_multi_step_task_runs_end_to_end(tmp_path: Path) -> None:
    photo = tmp_path / "in" / "notice.png"
    photo.parent.mkdir()
    photo.write_bytes(png_bytes())
    settings = RunSettings.create("Make a short Hindi audio summary of this notice", [str(photo)], str(tmp_path / "o"))
    ScriptedLlm.script = script(settings, photo)

    report = await cli.Run(settings, env=ENV).run()

    assert (report.status, report.stop_reason) == ("completed", "finished")
    assert [(item.id, item.status) for item in report.deliverables] == [("D1", "verified"), ("D2", "verified")]
    audio = next(item for item in report.deliverables if item.id == "D1")
    assert {check.name for check in audio.checks} >= {"speech_word_recall", "ai_label", "provenance"}
    assert (settings.artifacts_dir / "summary.hi.md").read_text(encoding="utf-8") == SUMMARY_HI
    assert (settings.artifacts_dir / "summary.hi.wav").read_bytes()[:4] == b"RIFF"

    assert [name for name, _ in FakeBodhan.calls] == [
        "ocr_document", "translate_text", "translate_text", "synthesize_speech",
    ]  # fmt: skip
    assert report.cost.bodhan_inr == pytest.approx(sum(COSTS.values()) + COSTS["translate_text"])
    assert report.budgets.steps.used == 7

    tools = [event["tool"] for event in map(json.loads, settings.trace_path.read_text().splitlines())
             if event["kind"] == "tool"]  # fmt: skip
    assert tools == ["ocr_document", "translate_text", "read", "write", "translate_text", "synthesize_speech",
                     "finish"]  # fmt: skip

    seen = json.dumps(ScriptedLlm.seen, ensure_ascii=False)
    assert not any("ऀ" <= char <= "ॿ" for char in seen)
    assert report.compliance.indic_redactions > 0 and report.compliance.media_to_llm == 0
    result = settings.result_path.read_text(encoding="utf-8")
    assert "# Result: completed" in result and LABEL in result
