from __future__ import annotations

import io
import tempfile
import wave
from pathlib import Path

from bodhan_agent.loop import BodhanAgentLoop
from bodhan_mcp.client import BodhanClient
from bodhan_mcp.models import Usage


def wav_bytes() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8_000)
        audio.writeframes(b"\0\0" * 800)
    return output.getvalue()


class MockBodhanClient(BodhanClient):
    """Mock client simulating multi-step API responses for agent testing."""

    def __init__(self):
        self.transcribe_calls = 0

    def transcribe(self, audio_bytes, filename, language=None):
        self.transcribe_calls += 1
        return {
            "text": "नमस्ते दुनिया",
            "language": language or "hi",
            "duration": 5.0,
            "usage": Usage(model="indic-transcribe", cost_inr=0.05),
        }

    def translate(self, text, target_language, source_language=None):
        return {
            "translated_text": f"Translated to {target_language}: {text}",
            "usage": Usage(model="indic-translate", cost_inr=0.02),
        }

    def speak(self, text, language, voice_id=None):
        return {
            "audio_bytes": wav_bytes(),
            "format": "wav",
            "usage": Usage(model="indic-speak", cost_inr=0.03),
        }

    def ocr(self, image_bytes, filename, language=None):
        return {
            "text": "Extracted OCR Document Text",
            "usage": Usage(model="indic-ocr", cost_inr=0.01),
        }


def test_agent_pipeline_execution():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Create a dummy audio file in the workspace
        audio_path = root / "sample_audio.wav"
        audio_path.write_bytes(wav_bytes())

        client = MockBodhanClient()
        agent = BodhanAgentLoop(workspace_dir=root, client=client)

        # Define a multi-step pipeline plan
        plan = [
            {"tool": "transcribe", "file_path": "sample_audio.wav", "language": "hi"},
            {"tool": "translate", "target_language": "ta", "source_key": "transcript"},
            {"tool": "speak", "language": "ta", "source_key": "translation"},
        ]

        result = agent.execute_pipeline(plan)

        assert result.success is True
        assert len(result.steps) == 3
        assert result.total_cost_inr == 0.15  # Includes the speech back-transcription.
        assert "नमस्ते दुनिया" in result.final_output["transcript"]
        assert "Translated to ta" in result.final_output["translation"]
        assert "audio_path" in result.final_output
        assert result.final_output["speech_verification"]["back_transcription_complete"] is True
        assert result.final_output["speech_verification"]["back_transcribed_audio_chunks"] == 1
        assert client.transcribe_calls == 2
        assert result.trace[0]["event"] == "plan"
        assert result.trace[-1]["event"] == "summary"
        tool_event = result.trace[1]
        assert tool_event["event"] == "tool_call"
        assert "arguments" in tool_event
        assert "latency_ms" in tool_event
        assert "cost_inr" in tool_event


def test_agent_uses_english_summary_only_as_input_to_bodhan_translation(tmp_path):
    class TranslationCaptureClient(MockBodhanClient):
        translated_text = None

        def translate(self, text, target_language, source_language=None):
            self.translated_text = text
            return super().translate(text, target_language, source_language)

    client = TranslationCaptureClient()
    agent = BodhanAgentLoop(
        workspace_dir=tmp_path,
        client=client,
        english_summarizer=lambda text, max_words: "The chapter explains the water cycle.",
    )

    result = agent.execute_pipeline(
        [
            {"tool": "summarize_english", "text": "Bodhan OCR source text."},
            {"tool": "translate", "target_language": "ta", "source_key": "summary"},
        ]
    )

    assert result.success is True
    assert client.translated_text == "The chapter explains the water cycle."
    assert result.final_output["summary"] == "The chapter explains the water cycle."
    assert result.final_output["translation"].startswith("Translated to ta:")


def test_agent_rejects_non_english_script_from_summary_callback(tmp_path):
    agent = BodhanAgentLoop(
        workspace_dir=tmp_path,
        english_summarizer=lambda text, max_words: "ഇത് ഒരു സംഗ്രഹമാണ്",
    )

    result = agent.execute_pipeline([{"tool": "summarize_english", "text": "OCR text"}])

    assert result.success is False
    assert result.trace[-2]["result_summary"]["error"]["code"] == "PARTIAL_FAILURE"


def test_agent_stops_before_tool_call_when_cost_budget_is_exhausted():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        audio_path = root / "sample_audio.wav"
        audio_path.write_bytes(wav_bytes())
        client = MockBodhanClient()
        agent = BodhanAgentLoop(
            workspace_dir=root,
            client=client,
            max_cost_inr=0.0,
        )

        result = agent.execute_pipeline([{"tool": "transcribe", "file_path": "sample_audio.wav"}])

        assert result.success is False
        assert result.stopped_reason is not None
        assert client.transcribe_calls == 0
        assert result.trace[-1]["event"] == "summary"


def test_transcription_cost_estimate_uses_audio_duration_metadata(tmp_path, monkeypatch):
    from bodhan_agent import loop

    audio_path = tmp_path / "sample_audio.mp3"
    audio_path.write_bytes(b"x" * 319_275)
    agent = BodhanAgentLoop(workspace_dir=tmp_path)
    monkeypatch.setattr(loop, "audio_duration_seconds", lambda path: 19.77)

    estimate = agent._estimate_step_cost(
        {"tool": "transcribe", "file_path": "sample_audio.mp3"},
        {},
    )

    assert estimate == 0.03295


def test_agent_fails_speech_step_when_back_transcription_is_empty():
    class EmptyTranscriptionClient(MockBodhanClient):
        def transcribe(self, audio_bytes, filename, language=None):
            return {
                "text": "",
                "language": language,
                "duration": 0.1,
                "usage": Usage(model="indic-transcribe", cost_inr=0.01),
            }

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        client = EmptyTranscriptionClient()
        agent = BodhanAgentLoop(workspace_dir=root, client=client)

        result = agent.execute_pipeline(
            [{"tool": "speak", "text": "Hello audio", "language": "en"}]
        )

        assert result.success is False
        assert result.stopped_reason == "tool call failed"
        assert result.trace[-2]["result_summary"]["ok"] is False
        assert result.total_cost_inr == 0.04


def test_agent_executes_array_iteration_conditions_and_deliverables(tmp_path, monkeypatch):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"fixture")
    agent = BodhanAgentLoop(workspace_dir=tmp_path)
    calls: list[dict] = []

    def run_step(step, context):
        calls.append(step)
        if step["tool"] == "ocr":
            return {
                "ok": True,
                "data": {
                    "pages": [
                        {"page_number": 1, "text": "alpha"},
                        {"page_number": 2, "text": "beta"},
                    ],
                    "text": "alpha beta",
                    "pages_processed": 2,
                    "total_pages": 2,
                    "coverage_complete": True,
                },
                "artifacts": [],
            }
        translated = f"translated:{step['text']}"
        return {
            "ok": True,
            "data": {
                "translation": translated,
                "chunk_count": 1,
                "translated_chunk_count": 1,
                "coverage_complete": True,
            },
            "artifacts": [],
        }

    monkeypatch.setattr(agent, "_run_step", run_step)
    monkeypatch.setattr(agent, "_estimate_step_cost", lambda step, context: 0.0)

    result = agent.execute_pipeline(
        [
            {
                "id": "ocr_pages",
                "tool": "ocr",
                "arguments": {"file_path": "source.pdf"},
            },
            {
                "id": "translate_pages",
                "tool": "translate",
                "arguments": {
                    "target_language": "hi",
                    "text": {"$item": "text"},
                },
                "for_each": {
                    "source": {"step_id": "ocr_pages", "output": "pages"},
                    "as": "item",
                },
            },
            {
                "id": "conditional_summary",
                "tool": "translate",
                "arguments": {"target_language": "en", "text": "summary"},
                "when": {
                    "ref": {
                        "step_id": "translate_pages",
                        "output": "translation",
                    },
                    "operator": "contains",
                    "value": "translated:alpha",
                },
            },
        ],
        deliverables=[
            {
                "name": "page_translations",
                "step_id": "translate_pages",
                "output": "translation",
                "media_type": "json",
                "description": "One translation per document page.",
            }
        ],
    )

    assert result.success is True
    assert [call["text"] for call in calls[1:]] == ["alpha", "beta", "summary"]
    assert result.final_output["deliverables"]["page_translations"] == [
        "translated:alpha",
        "translated:beta",
    ]
    assert any(event.get("iteration") == 2 for event in result.trace)


def test_agent_records_false_conditions_as_skipped(tmp_path, monkeypatch):
    agent = BodhanAgentLoop(workspace_dir=tmp_path)
    calls: list[dict] = []

    def run_step(step, context):
        calls.append(step)
        return {
            "ok": True,
            "data": {
                "translation": "hello",
                "chunk_count": 1,
                "translated_chunk_count": 1,
                "coverage_complete": True,
            },
            "artifacts": [],
        }

    monkeypatch.setattr(agent, "_run_step", run_step)
    monkeypatch.setattr(agent, "_estimate_step_cost", lambda step, context: 0.0)

    result = agent.execute_pipeline(
        [
            {
                "id": "first",
                "tool": "translate",
                "arguments": {"target_language": "hi", "text": "hello"},
            },
            {
                "id": "skipped",
                "tool": "translate",
                "arguments": {"target_language": "en", "text": "later"},
                "when": {
                    "ref": {"step_id": "first", "output": "translation"},
                    "operator": "equals",
                    "value": "not hello",
                },
            },
        ]
    )

    assert result.success is True
    assert len(calls) == 1
    assert any(
        event.get("event") == "step_skipped" and event.get("step_id") == "skipped"
        for event in result.trace
    )


def test_agent_iterates_over_input_file_references(tmp_path, monkeypatch):
    agent = BodhanAgentLoop(workspace_dir=tmp_path)
    calls: list[dict] = []

    def run_step(step, context):
        calls.append(step)
        return {
            "ok": True,
            "data": {
                "translation": f"translated:{step['text']}",
                "chunk_count": 1,
                "translated_chunk_count": 1,
                "coverage_complete": True,
            },
            "artifacts": [],
        }

    monkeypatch.setattr(agent, "_run_step", run_step)
    monkeypatch.setattr(agent, "_estimate_step_cost", lambda step, context: 0.0)

    result = agent.execute_pipeline(
        [
            {
                "id": "translate_files",
                "tool": "translate",
                "arguments": {
                    "target_language": "hi",
                    "text": {"$item": "file_path"},
                },
                "for_each": {"source": {"input": "files"}, "as": "item"},
            }
        ],
        deliverables=[
            {
                "name": "translations",
                "step_id": "translate_files",
                "output": "translation",
                "media_type": "json",
                "description": "Translations in input file order.",
            }
        ],
        input_items=[
            {"file_path": "one.txt"},
            {"file_path": "two.txt"},
        ],
    )

    assert result.success is True
    assert [call["text"] for call in calls] == ["one.txt", "two.txt"]
    assert result.final_output["deliverables"]["translations"] == [
        "translated:one.txt",
        "translated:two.txt",
    ]


def test_agent_ocr_to_translate_via_source_key_text(tmp_path):
    img = tmp_path / "table.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    client = MockBodhanClient()
    agent = BodhanAgentLoop(workspace_dir=tmp_path, client=client)

    result = agent.execute_pipeline(
        [
            {"id": "ocr_step", "tool": "ocr", "arguments": {"file_path": "table.png"}},
            {
                "id": "translate_step",
                "tool": "translate",
                "arguments": {"source_key": "text", "target_language": "ur"},
            },
        ],
        deliverables=[
            {
                "name": "urdu_table_markdown",
                "step_id": "translate_step",
                "output": "translation",
                "media_type": "text",
                "description": "Translated Markdown table.",
            }
        ],
    )

    assert result.success is True
    out = result.final_output["deliverables"]
    assert "Extracted OCR Document Text" in out["urdu_table_markdown"]



