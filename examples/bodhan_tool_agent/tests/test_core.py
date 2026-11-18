import asyncio
import json
from pathlib import Path

import httpx
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI

from bodhan_agent.__main__ import _result_status, _run
from bodhan_agent.graph import _provider_history, build_graph
from bodhan_core import Run, ToolError, chunks, has_non_latin_letters, safe_name, script_matches
from bodhan_mcp.server import create_server


def test_chunks_keep_text_and_respect_limit():
    source = "First sentence. " + ("long word " * 30) + "\n\nSecond paragraph."
    result = chunks(source, 50)
    assert result
    assert all(len(part) <= 50 for part in result)
    assert "First sentence." in " ".join(result)
    assert "Second paragraph." in " ".join(result)


def test_safe_name_and_script_guard():
    assert safe_name("../../bad name.wav") == "bad_name"
    assert safe_name("worksheet.mr") == "worksheet.mr"
    assert safe_name("translation.md") == "translation"
    assert has_non_latin_letters("English नमस्ते")
    assert not has_non_latin_letters("English summary")
    assert script_matches("தமிழ் உரை", "ta")
    assert not script_matches("English", "ta")
    assert script_matches("Tamil romanized text", "ta", "roman")


def test_request_uses_bearer_and_caches_response(tmp_path, monkeypatch):
    calls = []

    async def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"text": "hello"})

    monkeypatch.setenv("BODHAN_TRANSCRIBE_KEY", "test-key")
    run = Run([], tmp_path / "out", base_url="https://bodhan.test",
              transport=httpx.MockTransport(handler))

    async def execute():
        first = await run._request("transcribe", "/transcribe", 0.01, body={"model": "demo"})
        second = await run._request("transcribe", "/transcribe", 0.01, body={"model": "demo"})
        await run.close()
        return first, second

    first, second = asyncio.run(execute())
    assert first == second == {"text": "hello"}
    assert len(calls) == 1
    assert calls[0].headers["authorization"] == "Bearer test-key"
    assert run.cache_hits == 1


def test_report_omits_speech_source_text(tmp_path):
    run = Run([], tmp_path / "out")
    artifact = run.artifact("speech", ".wav")
    artifact.write_bytes(b"fake")
    run.add_artifact(artifact, "audio", text="private source text")
    run.write_outputs("Done.", "completed")
    report = json.loads(run.report_path.read_text())
    assert "private source text" not in run.report_path.read_text()
    assert report["artifacts"][0]["name"] == "speech.wav"
    asyncio.run(run.close())


def test_agent_boundary_hides_indic_tool_result_and_trace_content(tmp_path):
    run = Run([], tmp_path / "out")

    async def transcribe():
        return {"text": "नमस्ते", "segments": [{"text": "नमस्ते"}],
                "segments_count": 1, "artifact": "transcript.txt", "detected_language": "hi"}

    result = asyncio.run(run.invoke("transcribe_audio", transcribe))
    assert result == {"artifact": "transcript.txt", "detected_language": "hi", "segments_count": 1}
    assert "नमस्ते" not in run.trace_path.read_text()
    asyncio.run(run.close())


def test_read_text_rejects_indic_content(tmp_path):
    source = tmp_path / "input.txt"
    source.write_text("नमस्ते", encoding="utf-8")
    run = Run([source], tmp_path / "out")

    async def read():
        try:
            await run.read_text("file_001")
        except ToolError as exc:
            return exc.code

    assert asyncio.run(read()) == "indic_text_hidden"
    asyncio.run(run.close())


def test_single_file_input_does_not_expose_siblings(tmp_path):
    selected = tmp_path / "selected.txt"
    sibling = tmp_path / "private.txt"
    selected.write_text("Selected input", encoding="utf-8")
    sibling.write_text("Sibling input", encoding="utf-8")
    run = Run([selected], tmp_path / "out")

    assert [item["name"] for item in run.list_files()] == ["selected.txt"]
    try:
        run.file_path(str(sibling))
    except ToolError as exc:
        assert exc.code == "file_not_found"
    else:
        raise AssertionError("a single-file input must not grant access to neighboring files")
    asyncio.run(run.close())


def test_identical_tool_call_is_deduplicated(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("sample", encoding="utf-8")
    run = Run([source], tmp_path / "out")
    calls = []

    async def operation(file_ref):
        calls.append(file_ref)
        await asyncio.sleep(0)
        return {"value": file_ref}

    async def execute():
        first, second = await asyncio.gather(
            run.invoke("operation", operation, "file_001"),
            run.invoke("operation", operation, str(source)),
        )
        await run.close()
        return first, second

    first, second = asyncio.run(execute())
    assert first == second
    assert len(calls) == 1
    assert run.steps == 1
    assert '"status": "deduplicated"' in run.trace_path.read_text(encoding="utf-8")


def test_rate_limit_retry_only_charges_successful_attempt(tmp_path, monkeypatch):
    calls = []

    async def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "0"}, json={"error": "busy"})
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setenv("BODHAN_TRANSCRIBE_KEY", "test-key")
    run = Run([], tmp_path / "out", base_url="https://bodhan.test",
              transport=httpx.MockTransport(handler))

    async def execute():
        result = await run._request("transcribe", "/transcribe", 0.02, body={"model": "demo"})
        await run.close()
        return result

    assert asyncio.run(execute()) == {"ok": True}
    assert len(calls) == 2
    assert run.request_attempts == 2
    assert run.rate_limited_responses == 1
    assert run.request_failures == 1
    assert run.spent == 0.02
    assert run.reserved == 0


def test_persistent_rate_limit_stops_and_deduplicates_paid_tool(tmp_path, monkeypatch):
    calls = []

    async def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"retry-after": "0"}, json={"error": "busy"})

    monkeypatch.setenv("BODHAN_TRANSCRIBE_KEY", "test-key")
    run = Run([], tmp_path / "out", base_url="https://bodhan.test",
              transport=httpx.MockTransport(handler))

    async def execute():
        first = await run.invoke("transcribe", run._request, "transcribe", "/transcribe", 0.02,
                                 body={"model": "demo"})
        second = await run.invoke("transcribe", run._request, "transcribe", "/transcribe", 0.02,
                                  body={"model": "demo"})
        await run.close()
        return first, second

    first, second = asyncio.run(execute())
    assert first == second and first["code"] == "rate_limited"
    assert run.stop_reason == "rate_limited"
    assert len(calls) == 2
    assert run.steps == 1
    assert run.spent == run.reserved == 0


def test_upstream_timeout_stops_without_repeating_uncertain_request(tmp_path, monkeypatch):
    calls = []

    async def handler(request):
        calls.append(request)
        return httpx.Response(408, json={"error": "upstream timeout"})

    monkeypatch.setenv("BODHAN_TRANSCRIBE_KEY", "test-key")
    run = Run([], tmp_path / "out", base_url="https://bodhan.test",
              transport=httpx.MockTransport(handler))

    async def execute():
        result = await run.invoke("transcribe", run._request, "transcribe", "/transcribe", 0.02,
                                  body={"model": "demo"})
        await run.close()
        return result

    result = asyncio.run(execute())
    assert result["code"] == run.stop_reason == "upstream_timeout"
    assert len(calls) == 1
    assert run.request_failures == 1
    assert run.spent == 0.02
    assert run.reserved == 0


def test_english_ocr_artifact_can_be_read_in_bounded_sections(tmp_path):
    run = Run([], tmp_path / "out")
    path = run.artifact("scan", ".md")
    path.write_text("Page one. Page two.", encoding="utf-8")
    artifact = run.add_artifact(path, "ocr", language="en")

    async def execute():
        result = await run.read_text(artifact["artifact_id"], offset=10, limit=5)
        await run.close()
        return result

    result = asyncio.run(execute())
    assert result["text"] == "<untrusted-input>Page </untrusted-input>"
    assert result["total_chars"] == len("Page one. Page two.")


def test_artifact_names_do_not_overwrite(tmp_path):
    run = Run([], tmp_path / "out")
    first = run.artifact("result", ".txt")
    first.write_text("first", encoding="utf-8")
    second = run.artifact("result", ".txt")
    assert first != second
    assert second.name == "result-001.txt"
    asyncio.run(run.close())


def test_recovered_tool_error_does_not_mark_successful_run_partial(tmp_path):
    run = Run([], tmp_path / "out")

    async def translate(file_ref, target_language):
        if target_language == "unsupported":
            raise ToolError("unsupported_language", "unsupported")
        return {"artifact": "translation.md"}

    async def execute():
        await run.invoke("translate_file", translate, "file_001", "unsupported")
        await run.invoke("translate_file", translate, "file_001", "hi")
        await run.close()

    asyncio.run(execute())

    assert _result_status(run, []) == "completed"
    assert run.errors[0]["recovered"] is True


def test_unresolved_unsupported_request_is_reported_impossible(tmp_path):
    run = Run([], tmp_path / "out")
    run.steps = 1
    run.errors.append({"code": "unsupported_language", "message": "unsupported"})

    assert _result_status(run, []) == "impossible"
    asyncio.run(run.close())


def test_workspace_scan_excludes_credentials_and_build_files(tmp_path):
    (tmp_path / "API").mkdir()
    (tmp_path / "API" / "private.txt").write_text("dummy-token", encoding="utf-8")
    (tmp_path / "bodhan-api-key-demo.txt").write_text("dummy-token", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "ignore.txt").write_text("build file", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("English notes", encoding="utf-8")
    run = Run([tmp_path], tmp_path / "out")

    assert [item["name"] for item in run.list_files()] == ["notes.txt"]
    for candidate in (tmp_path / "API" / "private.txt", tmp_path / "bodhan-api-key-demo.txt"):
        try:
            run.file_path(str(candidate))
        except ToolError as exc:
            assert exc.code == "file_not_found"
        else:
            raise AssertionError("credential files must be inaccessible to tools")
    asyncio.run(run.close())


def test_budget_is_reserved_atomically_for_parallel_requests(tmp_path, monkeypatch):
    calls = []

    async def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setenv("BODHAN_TRANSCRIBE_KEY", "test-key")
    run = Run([], tmp_path / "out", max_cost_inr=0.1, base_url="https://bodhan.test",
              transport=httpx.MockTransport(handler))

    async def execute():
        results = await asyncio.gather(
            run._request("transcribe", "/transcribe", 0.06, body={"n": 1}),
            run._request("transcribe", "/transcribe", 0.06, body={"n": 2}),
            return_exceptions=True,
        )
        await run.close()
        return results

    results = asyncio.run(execute())
    assert sum(isinstance(result, dict) for result in results) == 1
    assert any(isinstance(result, ToolError) and result.code == "budget_exceeded" for result in results)
    assert run.spent == 0.06
    assert len(calls) == 1


def test_langgraph_and_mcp_tool_schemas_build_without_network(tmp_path):
    run = Run([], tmp_path / "out")
    app = build_graph(run, ChatOpenAI(model="test-model", base_url="http://localhost/v1", api_key="test"))
    server = create_server(run)
    tools = asyncio.run(server.list_tools())
    names = {item.name for item in tools}
    assert {"translate_file", "translate_transcription", "translate_and_synthesize", "speak_file"} <= names
    assert all(item.description for item in tools)
    assert {"agent", "tools", "verify"} <= set(app.get_graph().nodes)
    assert "plan" not in app.get_graph().nodes
    asyncio.run(run.close())


def test_gemini_compatible_history_replays_tool_results_as_text():
    messages = [
        HumanMessage(content="Do the task"),
        AIMessage(content="", tool_calls=[{"name": "inspect_media", "args": {}, "id": "call-1"}]),
        ToolMessage(content='{"kind":"pdf"}', tool_call_id="call-1", name="inspect_media"),
    ]

    history = _provider_history(messages, gemini_compat=True)

    assert len(history) == 3
    assert history[0] is messages[0]
    assert isinstance(history[1], AIMessage)
    assert history[1].content == "I called these tools: inspect_media."
    assert isinstance(history[2], HumanMessage)
    assert history[2].content == 'Tool result (inspect_media): {"kind":"pdf"}'
    assert _provider_history(messages, gemini_compat=False) is messages


def test_translate_file_keeps_indic_output_inside_tool(tmp_path, monkeypatch):
    source = tmp_path / "source.txt"
    source.write_text("Good morning", encoding="utf-8")
    calls = []

    async def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "सुप्रभात"}}]})

    monkeypatch.setenv("BODHAN_TRANSLATE_KEY", "test-key")
    run = Run([source], tmp_path / "out", base_url="https://bodhan.test",
              transport=httpx.MockTransport(handler))

    async def execute():
        result = await run.invoke("translate_file", run.translate_file, "file_001", "hi")
        await run.close()
        return result

    result = asyncio.run(execute())
    assert result["target_language"] == "hi"
    assert "translation" not in result
    assert "सुप्रभात" not in run.trace_path.read_text()
    assert Path(result["artifact"]).read_text(encoding="utf-8").strip() == "सुप्रभात"
    assert calls[0].headers["authorization"] == "Bearer test-key"


def test_missing_file_writes_honest_impossible_result(tmp_path, capsys):
    missing = tmp_path / "missing.wav"
    args = type("Args", (), {"files": [str(missing)], "out": str(tmp_path / "out"),
                              "max_steps": 30, "max_cost_inr": 20.0,
                              "task": "Transcribe the recording"})()
    assert asyncio.run(_run(args)) == 0
    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "impossible"
    assert not report["artifacts"]
    assert "does not exist" in capsys.readouterr().out
