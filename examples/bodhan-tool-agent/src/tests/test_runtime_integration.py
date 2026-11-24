from __future__ import annotations

import asyncio
import base64
import json
import sys
import wave
from io import BytesIO
from types import SimpleNamespace

import httpx

from bodhan_mcp.cache import DiskCache
from bodhan_mcp.client import BodhanClient, ClientConfig
from bodhan_mcp.models import ErrorCode, Usage
from bodhan_mcp.server import call_tool, list_tools
from bodhan_mcp.tools import inspect as media_inspect
from bodhan_mcp.workspace import Workspace


def test_inspect_media_reads_compressed_audio_duration_metadata(monkeypatch, tmp_path):
    audio_path = tmp_path / "sample.mp3"
    audio_path.write_bytes(b"fixture")
    monkeypatch.setattr(
        media_inspect.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stderr=b"Input #0, mp3, from 'sample.mp3':\n  Duration: 00:00:19.77, start: 0.000",
        ),
    )
    workspace = Workspace(tmp_path)

    result = media_inspect.inspect_media(workspace, "sample.mp3")

    assert result["ok"] is True
    assert result["data"]["duration_seconds"] == 19.77
    assert result["data"]["metadata_method"] == "FFmpeg container duration"


def test_sync_client_calls_configured_endpoint_with_auth():
    seen = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "வணக்கம்"}}],
                "usage": {"cost_inr": 0.02},
            },
            request=request,
        )

    client = BodhanClient(
        ClientConfig(base_url="https://bodhan.test/v1", max_retries=0),
        env={"BODHAN_TRANSLATE_KEY": "test-key"},
        transport=httpx.MockTransport(handle),
    )
    try:
        result = client.translate("Hello", "ta", "en")
    finally:
        client.close()

    assert result["translated_text"] == "வணக்கம்"
    assert result["usage"].cost_inr == 0.02
    assert seen[0].headers["authorization"] == "Bearer test-key"
    assert seen[0].url.path == "/v1/chat/completions"
    body = json.loads(seen[0].content)
    assert body == {
        "model": "indic-translate",
        "messages": [{"role": "user", "content": "Hello"}],
        "target_language_code": "ta",
        "target_script": "native",
    }


def test_speak_client_uses_documented_request_shape():
    seen = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            content=b"wav-audio",
            headers={"content-type": "audio/wav"},
            request=request,
        )

    client = BodhanClient(
        ClientConfig(base_url="https://bodhan.test/v1", max_retries=0),
        env={"BODHAN_SPEAK_KEY": "test-key"},
        transport=httpx.MockTransport(handle),
    )
    try:
        result = client.speak("നമസ്കാരം", "ml", "Kavya")
    finally:
        client.close()

    assert result["audio_bytes"] == b"wav-audio"
    assert result["format"] == "wav"
    assert seen[0].url.path == "/v1/audio/speech"
    body = json.loads(seen[0].content)
    assert body == {
        "model": "indic-speak",
        "input": "നമസ്കാരം",
        "voice": "Kavya",
        "instructions": json.dumps({"lang": "ml"}, ensure_ascii=False),
    }


def test_speak_client_defaults_to_documented_voice():
    seen = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            content=b"wav-audio",
            headers={"content-type": "audio/wav"},
            request=request,
        )

    client = BodhanClient(
        ClientConfig(base_url="https://bodhan.test/v1", max_retries=0),
        env={"BODHAN_SPEAK_KEY": "test-key"},
        transport=httpx.MockTransport(handle),
    )
    try:
        result = client.speak("Hello", "en")
    finally:
        client.close()

    assert result["audio_bytes"] == b"wav-audio"
    body = json.loads(seen[0].content)
    assert body["voice"] == "Amit"


def test_ocr_client_uses_documented_chat_completions_request():
    seen = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "தமிழ் text"}}]},
            request=request,
        )

    image = b"jpeg-image-bytes"
    client = BodhanClient(
        ClientConfig(base_url="https://bodhan.test/v1", max_retries=0),
        env={"BODHAN_OCR_KEY": "test-key"},
        transport=httpx.MockTransport(handle),
    )
    try:
        result = client.ocr(image, "sample.jpg", language="ta")
    finally:
        client.close()

    assert result["text"] == "தமிழ் text"
    assert seen[0].url.path == "/v1/chat/completions"
    body = json.loads(seen[0].content)
    assert body["model"] == "indic-ocr"
    assert body["messages"] == [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/jpeg;base64,"
                        + base64.b64encode(image).decode("ascii"),
                    },
                }
            ],
        }
    ]


def test_mcp_registers_core_and_helper_tools_with_output_schemas():
    tools = asyncio.run(list_tools())
    by_name = {tool.name: tool for tool in tools}

    assert {
        "transcribe_audio",
        "translate_text",
        "speak_text",
        "ocr_document",
        "chunk_media",
        "inspect_media",
        "detect_language",
        "list_capabilities",
    } <= set(by_name)
    assert by_name["transcribe_audio"].inputSchema["required"] == ["file_path"]
    assert by_name["transcribe_audio"].inputSchema["additionalProperties"] is False
    assert "output_format" in by_name["transcribe_audio"].inputSchema["properties"]
    assert "voice_gender" in by_name["speak_text"].inputSchema["properties"]
    assert "output_format" in by_name["speak_text"].inputSchema["properties"]
    assert "table_format" in by_name["ocr_document"].inputSchema["properties"]
    assert by_name["transcribe_audio"].outputSchema["oneOf"][0]["properties"]["data"]["properties"][
        "transcript"
    ]["anyOf"][1]["properties"]["artifact_ref"]["type"] == ["string", "null"]
    assert by_name["list_capabilities"].outputSchema["oneOf"][0]["properties"]["data"][
        "properties"
    ].keys() == {
        "models",
        "pricing",
        "supported_languages",
        "default_voices",
        "limits",
    }


def test_mcp_rejects_invalid_tool_arguments_before_execution(monkeypatch, tmp_path):
    import bodhan_mcp.server as server

    monkeypatch.setattr(server, "workspace", Workspace(tmp_path))
    monkeypatch.setattr(server, "cache", DiskCache(tmp_path / "cache"))
    _, unknown_field = asyncio.run(
        call_tool("inspect_media", {"file_path": "input.wav", "inline_base64": "AAAA"})
    )
    _, invalid_page = asyncio.run(
        call_tool("ocr_document", {"file_path": "input.pdf", "page_number": True})
    )
    _, invalid_chunk_limit = asyncio.run(
        call_tool("chunk_media", {"file_path": "input.wav", "max_duration_s": 31})
    )

    for result in (unknown_field, invalid_page, invalid_chunk_limit):
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_ARGUMENT.value
        assert result["error"]["message"]
        assert result["error"]["hint"]


def test_mcp_tool_errors_are_structured(monkeypatch, tmp_path):
    import bodhan_mcp.server as server

    monkeypatch.setattr(server, "workspace", Workspace(tmp_path))
    monkeypatch.setattr(server, "cache", DiskCache(tmp_path / "cache"))
    _, result = asyncio.run(call_tool("transcribe_audio", {"file_path": "missing.wav"}))

    assert result["ok"] is False
    assert result["error"]["code"] == ErrorCode.FILE_NOT_FOUND.value


def test_cli_writes_trace_report_and_rendered_summary(monkeypatch, tmp_path):
    from bodhan_agent import __main__ as cli

    monkeypatch.setenv("BODHAN_CACHE_DIR", str(tmp_path / "cache"))
    input_audio = tmp_path / "input.wav"
    audio = BytesIO()
    with wave.open(audio, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8_000)
        stream.writeframes(b"\0\0" * 800)
    input_audio.write_bytes(audio.getvalue())

    class Planner:
        def __init__(self, timeout_s=None):
            self.last_usage = {}

        def plan_task(self, task, available_files, input_manifest=None):
            return [{"tool": "transcribe", "file_path": available_files[0]}]

        def summarize_english(self, text, max_words=None):
            return text

    class FakeClient:
        def __init__(self, config=None):
            pass

        def transcribe(self, audio_bytes, filename, language=None):
            return {
                "text": "test transcript",
                "duration": 0.1,
                "usage": Usage(model="indic-transcribe", cost_inr=0.01),
            }

        def close(self):
            pass

    output_dir = tmp_path / "outputs"
    monkeypatch.setattr(cli, "AgentPlanner", Planner)
    monkeypatch.setattr(cli, "BodhanClient", FakeClient)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "bodhan-agent",
            "--task",
            "transcribe input",
            "--files",
            str(input_audio),
            "--out",
            str(output_dir),
        ],
    )

    assert cli.main() == 0
    report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
    trace = [
        json.loads(line)
        for line in (output_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    summary = (output_dir / "result.md").read_text(encoding="utf-8")

    assert report["status"] == "completed"
    assert report["success"] is True
    assert report["elapsed_seconds"] >= 0
    assert report["total_cost_inr"] == 0.01
    assert report["budget"]["scope"] == (
        "Bodhan model API calls only; planner-provider charges are excluded."
    )
    assert "Provider billing may exceed an estimate" in report["budget"]["enforcement"]
    assert any(event["event"] == "tool_call" for event in trace)
    assert trace[-1]["event"] == "summary"
    assert "test transcript" in summary
    assert "{json.dumps(result.final_output" not in summary


def test_cli_preflight_refuses_unsupported_format_without_model_calls(monkeypatch, tmp_path):
    from bodhan_agent import __main__ as cli

    input_archive = tmp_path / "archive.zip"
    input_archive.write_bytes(b"PK\x03\x04fixture")
    output_dir = tmp_path / "outputs"

    class UnexpectedCall:
        def __init__(self, *args, **kwargs):
            raise AssertionError("Preflight must stop before constructing planner or API client.")

    monkeypatch.setattr(cli, "AgentPlanner", UnexpectedCall)
    monkeypatch.setattr(cli, "BodhanClient", UnexpectedCall)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "bodhan-agent",
            "--task",
            "Please summarize, translate, or caption the requested content",
            "--files",
            str(input_archive),
            "--out",
            str(output_dir),
        ],
    )

    assert cli.main() == 1
    report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
    trace = [
        json.loads(line)
        for line in (output_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert report["status"] == "failed"
    assert report["success"] is False
    assert report["stopped_reason"] == "preflight failed"
    assert report["total_cost_inr"] == 0
    assert {issue["code"] for issue in report["final_output"]["preflight"]["issues"]} == {
        ErrorCode.UNSUPPORTED_FORMAT.value
    }
    assert [event["event"] for event in trace] == ["preflight", "summary"]


def test_cli_refuses_unsupported_language_with_video_without_bodhan_call(monkeypatch, tmp_path):
    from bodhan_agent import __main__ as cli
    from bodhan_agent.planner import TaskPlan

    input_video = tmp_path / "chapter.mp4"
    input_video.write_bytes(b"fixture video")
    output_dir = tmp_path / "outputs"
    refusal = {
        "code": "UNSUPPORTED_LANGUAGE",
        "message": "French is not supported by Bodhan Indic models.",
        "hint": "Choose English or one of the configured Indian languages.",
    }

    class Planner:
        tool_catalog = {"version": 1, "tools": []}

        def __init__(self, timeout_s=None):
            self.last_usage = {}

        def plan_task(self, task, available_files, input_manifest=None):
            assert input_manifest[0]["file_type"] == "video"
            return TaskPlan(steps=[], refusal=refusal)

    class UnexpectedClient:
        def __init__(self, config=None):
            raise AssertionError("Unsupported language refusal must stop before any Bodhan call.")

    monkeypatch.setattr(cli, "AgentPlanner", Planner)
    monkeypatch.setattr(cli, "BodhanClient", UnexpectedClient)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "bodhan-agent",
            "--task",
            "Translate this video into French",
            "--files",
            str(input_video),
            "--out",
            str(output_dir),
        ],
    )

    assert cli.main() == 1
    report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "refused"
    assert report["success"] is False
    assert report["stopped_reason"] == "task refused"
    assert report["total_cost_inr"] == 0
    assert report["final_output"]["error"]["code"] == "UNSUPPORTED_LANGUAGE"


def test_cli_rejects_invalid_plan_before_creating_model_client(monkeypatch, tmp_path):
    from bodhan_agent import __main__ as cli

    input_audio = tmp_path / "input.wav"
    audio = BytesIO()
    with wave.open(audio, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8_000)
        stream.writeframes(b"\0\0" * 800)
    input_audio.write_bytes(audio.getvalue())

    class Planner:
        def __init__(self, timeout_s=None):
            self.last_usage = {}

        def plan_task(self, task, available_files, input_manifest=None):
            return [{"tool": "unsupported_tool"}]

    class UnexpectedClient:
        def __init__(self, config=None):
            raise AssertionError("Invalid plans must be rejected before creating the API client.")

    output_dir = tmp_path / "outputs"
    monkeypatch.setattr(cli, "AgentPlanner", Planner)
    monkeypatch.setattr(cli, "BodhanClient", UnexpectedClient)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "bodhan-agent",
            "--task",
            "transcribe input",
            "--files",
            str(input_audio),
            "--out",
            str(output_dir),
        ],
    )

    assert cli.main() == 1
    report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
    trace = [
        json.loads(line)
        for line in (output_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert report["success"] is False
    assert report["stopped_reason"] == "plan validation failed"
    assert report["total_cost_inr"] == 0
    assert report["final_output"]["plan_validation"]["issues"][0]["code"] == "TOOL_UNSUPPORTED"
    assert [event["event"] for event in trace] == [
        "preflight",
        "planner",
        "plan_validation",
        "summary",
    ]


def test_cli_reports_planner_capability_refusal_without_bodhan_call(monkeypatch, tmp_path):
    from bodhan_agent import __main__ as cli
    from bodhan_agent.planner import TaskPlan

    input_text = tmp_path / "recording.txt"
    input_text.write_text("A recording placeholder.", encoding="utf-8")
    output_dir = tmp_path / "outputs"
    refusal = {
        "code": "CAPABILITY_UNAVAILABLE",
        "message": "Timestamped transcription is not available in this task agent.",
        "hint": "Use a deployment that provides timed speech segments.",
        "capability": "timestamped_transcription",
    }

    class Planner:
        tool_catalog = {
            "version": 1,
            "tools": [],
        }

        def __init__(self, timeout_s=None):
            self.last_usage = {}

        def plan_task(self, task, available_files, input_manifest=None):
            assert input_manifest[0]["extension"] == ".txt"
            return TaskPlan(steps=[], refusal=refusal)

    class UnexpectedClient:
        def __init__(self, config=None):
            raise AssertionError("A task refusal must stop before any Bodhan API call.")

    monkeypatch.setattr(cli, "AgentPlanner", Planner)
    monkeypatch.setattr(cli, "BodhanClient", UnexpectedClient)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "bodhan-agent",
            "--task",
            "Create timestamped English subtitles for this recording",
            "--files",
            str(input_text),
            "--out",
            str(output_dir),
        ],
    )

    assert cli.main() == 1
    report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
    trace = [
        json.loads(line)
        for line in (output_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert report["status"] == "refused"
    assert report["success"] is False
    assert report["stopped_reason"] == "task refused"
    assert report["final_output"]["refusal"] == refusal
    assert report["final_output"]["error"]["code"] == "CAPABILITY_UNAVAILABLE"
    assert [event["event"] for event in trace] == [
        "preflight",
        "planner",
        "plan_validation",
        "task_refusal",
        "summary",
    ]