import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
import pytest

from bodhan_agent.agent import SYSTEM_PROMPT, TaskAgent, _openrouter_retry_info, _repair_utf8_mojibake, _request_url, _strip_reasoning_tags, _tool_calls_from_dsml, _tool_calls_from_fenced_json, _tool_message_payload
import bodhan_agent.agent as agent_module


def test_openai_compatible_endpoint_url():
    assert _request_url("https://llm.example/v1/") == "https://llm.example/v1/chat/completions"
    assert _request_url("https://llm.example/v1/chat/completions") == "https://llm.example/v1/chat/completions"


def test_repair_utf8_mojibake_from_local_model():
    assert _repair_utf8_mojibake("à¤µà¤¿à¤¦à¥à¤¯à¤¾à¤²à¤¯") == "विद्यालय"
    tamil = "வித தமிழ்"
    assert _repair_utf8_mojibake(tamil.encode("utf-8").decode("latin-1")) == tamil
    assert _repair_utf8_mojibake("plain English") == "plain English"


def test_strip_private_reasoning_before_reusing_model_content():
    assert _strip_reasoning_tags("<think>private plan</think>\nFinal answer") == "Final answer"
    assert _strip_reasoning_tags("<think>truncated private plan") == ""


def test_recovers_only_registered_fenced_json_tool_calls():
    content = 'Tool call:\n```json\n{"name":"ocr_document","arguments":{"image_file":"/tmp/input.png"}}\n```'
    calls, remaining = _tool_calls_from_fenced_json(content, {"ocr_document", "translate_text"})
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "ocr_document"
    assert json.loads(calls[0]["function"]["arguments"]) == {"image_file": "/tmp/input.png"}
    assert remaining == ""
    ignored, unchanged = _tool_calls_from_fenced_json(content.replace("ocr_document", "shell"), {"ocr_document"})
    assert ignored == []
    assert unchanged == content.replace("ocr_document", "shell")


def test_recovers_lfm_dsml_tool_call_even_when_pipes_are_mojibake():
    content = '<｜DSML｜function_calls>\n{"name":"transliterate_text","arguments":{"text":"नमस्ते","language":"hi","script":"native"}}\n</｜DSML｜function_calls>'
    corrupted = content.encode("utf-8").decode("latin-1")
    repaired = _repair_utf8_mojibake(corrupted)
    calls, remaining = _tool_calls_from_dsml(repaired, {"transliterate_text"})
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "transliterate_text"
    assert json.loads(calls[0]["function"]["arguments"])["script"] == "native"
    assert remaining == ""


def test_tool_output_keeps_file_id_when_text_is_long():
    payload = _tool_message_payload({"text": "नमस्ते" * 5000, "file_id": "setu:=keep-this-id"})
    assert "setu:=keep-this-id" in payload
    assert "returned file_id directly" in payload
    assert len(payload) < 6000


def test_prompt_stops_on_unsupported_requested_language():
    assert "Never silently substitute a different target language" in SYSTEM_PROMPT
    assert "do not substitute" in SYSTEM_PROMPT


def test_required_tools_follow_explicit_document_audio_workflow(tmp_path):
    scan = tmp_path / "lesson.pdf"
    scan.touch()
    agent = TaskAgent(
        task="Read this lesson, summarize it in English, and make a spoken Tamil version.",
        input_paths=[scan], output_root=tmp_path / "runs", max_steps=8, max_cost_inr=1.0,
        tool_timeout_seconds=10, llm_timeout_seconds=5, model="test-model",
        base_url="http://unused.invalid/v1", api_key="test-key",
    )
    assert agent._required_tools_for_task(agent._input_manifest()) == {
        "ocr_document", "translate_text", "synthesize_speech"
    }


def test_required_tools_allow_explicit_unsupported_response(tmp_path):
    agent = TaskAgent(
        task="Transliterate this to Cyrillic.", input_paths=[], output_root=tmp_path / "runs",
        max_steps=4, max_cost_inr=1.0, tool_timeout_seconds=10, llm_timeout_seconds=5,
        model="test-model", base_url="http://unused.invalid/v1", api_key="test-key",
    )
    assert agent._required_tools_for_task([]) == {"transliterate_text"}


def test_transliteration_request_that_forbids_translation_stays_single_tool(tmp_path):
    agent = TaskAgent(
        task="Write this Hindi sentence in Latin letters. Do not translate it.",
        input_paths=[], output_root=tmp_path / "runs", max_steps=4, max_cost_inr=1.0,
        tool_timeout_seconds=10, llm_timeout_seconds=5, model="test-model",
        base_url="http://unused.invalid/v1", api_key="test-key",
    )
    assert agent._required_tools_for_task([]) == {"transliterate_text"}


def test_transliteration_that_says_instead_of_translation_stays_single_tool(tmp_path):
    agent = TaskAgent(
        task="Convert 'aapka swagat hai' into Hindi script, preserving the Hindi words rather than translating them.",
        input_paths=[], output_root=tmp_path / "runs", max_steps=4, max_cost_inr=1.0,
        tool_timeout_seconds=10, llm_timeout_seconds=5, model="test-model",
        base_url="http://unused.invalid/v1", api_key="test-key",
    )
    assert agent._required_tools_for_task([]) == {"transliterate_text"}


def test_full_video_translation_hides_manual_chain_alternatives(tmp_path):
    agent = TaskAgent(
        task="Translate this video and return a new video with the translated audio track.",
        input_paths=[], output_root=tmp_path / "runs", max_steps=4, max_cost_inr=1.0,
        tool_timeout_seconds=10, llm_timeout_seconds=5, model="test-model",
        base_url="http://unused.invalid/v1", api_key="test-key",
    )
    names = ["translate_video", "transcribe_video", "translate_text", "synthesize_speech", "get_file"]
    tools = [SimpleNamespace(name=name, description="", inputSchema={"type": "object"}) for name in names]
    definitions = agent._task_tool_definitions(tools, [{"suffix": ".mp4"}])
    offered = {item["function"]["name"] for item in definitions}
    assert "translate_video" in offered
    assert not ({"transcribe_video", "translate_text", "synthesize_speech", "get_file"} & offered)


def test_tool_catalog_exposes_source_reader_for_text_files_only(tmp_path):
    agent = TaskAgent(
        task="Translate this notice into Tamil and Bengali.", input_paths=[], output_root=tmp_path / "runs",
        max_steps=4, max_cost_inr=1.0, tool_timeout_seconds=10, llm_timeout_seconds=5,
        model="test-model", base_url="http://unused.invalid/v1", api_key="test-key",
    )
    names = ["translate_text", "transcribe_audio", "chunk_audio", "ocr_document", "convert_pdf_to_images", "translate_audio", "get_file"]
    tools = [SimpleNamespace(name=name, description="", inputSchema={"type": "object"}) for name in names]
    offered = {item["function"]["name"] for item in agent._task_tool_definitions(tools, [{"suffix": ".txt"}])}
    assert offered == {"translate_text", "get_file"}


def test_tool_catalog_keeps_file_reader_hidden_for_media_inputs(tmp_path):
    agent = TaskAgent(
        task="Transcribe this recording.", input_paths=[], output_root=tmp_path / "runs",
        max_steps=4, max_cost_inr=1.0, tool_timeout_seconds=10, llm_timeout_seconds=5,
        model="test-model", base_url="http://unused.invalid/v1", api_key="test-key",
    )
    names = ["transcribe_audio", "translate_text", "ocr_document", "get_file"]
    tools = [SimpleNamespace(name=name, description="", inputSchema={"type": "object"}) for name in names]
    offered = {item["function"]["name"] for item in agent._task_tool_definitions(tools, [{"suffix": ".wav"}])}
    assert offered == {"transcribe_audio"}


def test_task_tool_catalog_can_gate_synthesis_until_translation_succeeds(tmp_path):
    agent = TaskAgent(
        task="Make a Tamil spoken summary of this scanned chapter.", input_paths=[], output_root=tmp_path / "runs",
        max_steps=8, max_cost_inr=2.0, tool_timeout_seconds=10, llm_timeout_seconds=5,
        model="test-model", base_url="http://unused.invalid/v1", api_key="test-key",
    )
    names = ["ocr_document", "translate_text", "synthesize_speech"]
    tools = [SimpleNamespace(name=name, description="", inputSchema={"type": "object"}) for name in names]
    manifest = [{"suffix": ".pdf"}]
    before_translation = {item["function"]["name"] for item in agent._task_tool_definitions(tools, manifest, allow_synthesis=False)}
    after_translation = {item["function"]["name"] for item in agent._task_tool_definitions(tools, manifest, allow_synthesis=True)}
    assert before_translation == {"ocr_document", "translate_text"}
    assert after_translation == {"ocr_document", "translate_text", "synthesize_speech"}


def test_language_filtered_folder_workflow_is_general_in_prompt():
    assert "For language-filtered folders, read each transcript" in SYSTEM_PROMPT
    assert "Never infer a file's language from its name" in SYSTEM_PROMPT


def test_rejects_pasted_text_in_file_reference_without_path_error(tmp_path):
    agent = TaskAgent(
        task="Read the supplied file.", input_paths=[], output_root=tmp_path / "runs",
        max_steps=4, max_cost_inr=1.0, tool_timeout_seconds=10, llm_timeout_seconds=5,
        model="test-model", base_url="http://unused.invalid/v1", api_key="test-key",
    )
    with pytest.raises(ValueError, match="not pasted text"):
        agent._validate_input_references({"file_ref": "नमस्ते " * 500})


def test_repairs_malformed_path_to_unique_supplied_input(tmp_path):
    media = tmp_path / "evals" / "audio" / "clip.wav"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"wav")
    agent = TaskAgent(
        task="Transcribe the supplied clip.", input_paths=[media], output_root=tmp_path / "runs",
        max_steps=4, max_cost_inr=1.0, tool_timeout_seconds=10, llm_timeout_seconds=5,
        model="test-model", base_url="http://unused.invalid/v1", api_key="test-key",
    )
    repaired = agent._normalize_input_paths({"audio_file": "/bad/root/evals-audio/clip.wav"})
    assert repaired["audio_file"] == str(media.resolve())
    agent._validate_input_references(repaired)
    artifact = tmp_path / "runs" / "artifacts" / "ocr.txt"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("विद्यालय सूचना", encoding="utf-8")
    file_id = "setu:=ocr-artifact"
    agent.artifact_paths[file_id] = artifact
    assert agent._normalize_input_paths({"text": str(artifact)})["text"] == file_id


def test_speech_artifacts_use_the_wav_extension(tmp_path):
    agent = TaskAgent(
        task="Speak this text.", input_paths=[], output_root=tmp_path / "runs",
        max_steps=4, max_cost_inr=1.0, tool_timeout_seconds=10, llm_timeout_seconds=5,
        model="test-model", base_url="http://unused.invalid/v1", api_key="test-key",
    )
    output = agent._normalize_output_paths("synthesize_speech", {"output_file": "spoken.mp3"})
    assert Path(output["output_file"]).suffix == ".wav"


def test_rejects_english_text_mislabeled_for_indic_speech(tmp_path):
    agent = TaskAgent(
        task="Make a spoken Tamil summary.", input_paths=[], output_root=tmp_path / "runs",
        max_steps=4, max_cost_inr=1.0, tool_timeout_seconds=10, llm_timeout_seconds=5,
        model="test-model", base_url="http://unused.invalid/v1", api_key="test-key",
    )
    with pytest.raises(ValueError, match="translate_text"):
        agent._validate_speech_language({"text": "This is a short summary in English.", "language": "ta"})
    agent._validate_speech_language({"text": "இது ஒரு தமிழ் சுருக்கம்.", "language": "ta"})


def test_get_file_page_size_is_clamped_to_mcp_limit(tmp_path, monkeypatch):
    agent = TaskAgent(
        task="Read this artifact.", input_paths=[], output_root=tmp_path / "runs",
        max_steps=4, max_cost_inr=1.0, tool_timeout_seconds=10, llm_timeout_seconds=5,
        model="test-model", base_url="http://unused.invalid/v1", api_key="test-key",
    )
    file_id = "setu:=issued-id"
    agent.issued_file_ids.add(file_id)
    captured = {}

    async def fake_paid_call(_session, name, arguments, **_kwargs):
        captured.update(arguments)
        return {"content": "page"}, False, {}

    monkeypatch.setattr(agent, "_paid_call", fake_paid_call)
    call = {"id": "get-1", "function": {"name": "get_file", "arguments": json.dumps({"file_ref": file_id, "max_chars": 10000})}}
    asyncio.run(agent._execute_tool_call(None, call))
    assert captured["max_chars"] == 4000


def test_openrouter_retry_uses_reset_header_and_identifies_daily_limit():
    reset_ms = str((time.time() + 25) * 1000)
    delay, daily, detail = _openrouter_retry_info(
        {"X-RateLimit-Reset": reset_ms},
        {"error": {"metadata": {"limit_source": "openrouter_free_tier_per_minute"}}},
        0,
    )
    assert 24 <= delay <= 25
    assert daily is False
    assert "openrouter_free_tier_per_minute" in detail

    delay, daily, detail = _openrouter_retry_info(
        {},
        {"error": {"metadata": {"limit_source": "openrouter_free_tier_daily"}}},
        0,
    )
    assert delay == 1
    assert daily is True
    assert "openrouter_free_tier_daily" in detail


def test_tool_call_signature_ignores_output_path_and_canonicalizes_file_aliases(tmp_path):
    input_file = tmp_path / "scan.png"
    input_file.write_bytes(b"same bytes for either reference")
    agent = TaskAgent(
        task="Read this scan.", input_paths=[input_file], output_root=tmp_path / "runs",
        max_steps=5, max_cost_inr=1.0, tool_timeout_seconds=15, llm_timeout_seconds=5,
        model="test-model", base_url="http://unused.invalid/v1", api_key="test-key",
    )
    file_id = "setu:=file-id-123"
    agent.artifact_paths[file_id] = input_file
    first = {
        "function": {
            "name": "ocr_document",
            "arguments": json.dumps({"image_file": str(input_file), "output_file": "one.md", "dry_run": True}),
        }
    }
    duplicate = {
        "function": {
            "name": "ocr_document",
            "arguments": json.dumps({"image_file": file_id, "output_file": "two.md", "dry_run": True}),
        }
    }
    paid_call = {
        "function": {
            "name": "ocr_document",
            "arguments": json.dumps({"image_file": file_id, "output_file": "two.md", "dry_run": False}),
        }
    }
    assert agent._tool_call_signature(first) == agent._tool_call_signature(duplicate)
    assert agent._tool_call_signature(first) != agent._tool_call_signature(paid_call)


def test_long_text_is_never_treated_as_a_filesystem_path(tmp_path):
    agent = TaskAgent(
        task="Summarize the supplied document.", input_paths=[], output_root=tmp_path / "runs",
        max_steps=4, max_cost_inr=1.0, tool_timeout_seconds=10, llm_timeout_seconds=5,
        model="test-model", base_url="http://unused.invalid/v1", api_key="test-key",
    )
    long_text = "document contents " * 1000
    signature = agent._canonical_operation_signature("synthesize_speech", {"text": long_text, "language": "ta"})
    assert signature.startswith("synthesize_speech:")
    assert agent._normalize_input_paths({"text": long_text})["text"] == long_text


def test_dry_run_then_paid_call_reuses_estimate(tmp_path, monkeypatch):
    input_file = tmp_path / "note.wav"
    input_file.write_bytes(b"test")
    agent = TaskAgent(
        task="Transcribe this recording.", input_paths=[input_file], output_root=tmp_path / "runs",
        max_steps=5, max_cost_inr=1.0, tool_timeout_seconds=15, llm_timeout_seconds=5,
        model="test-model", base_url="http://unused.invalid/v1", api_key="test-key",
    )
    calls = []

    async def fake_call_mcp(session, tool_name, args, *, origin="agent_internal", parent_call_id=None):
        calls.append((dict(args), origin))
        if args.get("dry_run") is True:
            return {"estimated_cost_inr": 0.01, "dry_run": True}, False, 0.0
        return {"text": "A short transcript."}, False, 0.0

    monkeypatch.setattr(agent, "_call_mcp", fake_call_mcp)
    file_ref = str(input_file)
    dry_run_call = {
        "id": "dry-call",
        "function": {"name": "transcribe_audio", "arguments": json.dumps({"audio_file": file_ref, "dry_run": True})},
    }
    _, dry_payload = asyncio.run(agent._execute_tool_call(None, dry_run_call))
    assert dry_payload["result"]["dry_run"] is True

    result, is_error, audit = asyncio.run(agent._paid_call(None, "transcribe_audio", {"audio_file": file_ref}, parent_call_id="paid-call"))
    assert is_error is False
    assert result["text"] == "A short transcript."
    assert audit["estimate_cost_inr"] == 0.01
    assert len(calls) == 2
    assert calls[0][1] == "llm_requested_dry_run"
    assert calls[1][1] == "agent_paid_call"
    assert any(event.get("type") == "preflight_reused" for event in agent.events)


def test_same_batch_dry_run_and_paid_call_are_coalesced(tmp_path, monkeypatch):
    monkeypatch.setenv("BODHAN_CACHE_BACKEND", "memory")
    monkeypatch.setenv("BODHAN_ARTIFACT_DIR", str(tmp_path / "mcp-artifacts"))
    monkeypatch.setenv("BODHAN_MODEL_CACHE_ARTIFACT_DIR", str(tmp_path / "model-artifacts"))
    input_file = tmp_path / "clip.wav"
    input_file.write_bytes(b"test")
    agent = TaskAgent(
        task="Transcribe the supplied recording.", input_paths=[input_file], output_root=tmp_path / "runs",
        max_steps=5, max_cost_inr=1.0, tool_timeout_seconds=15, llm_timeout_seconds=5,
        model="test-model", base_url="http://unused.invalid/v1", api_key="test-key",
    )
    calls = []
    arguments = {"audio_file": str(input_file)}
    responses = [
        {"content": None, "tool_calls": [
            {"id": "dry-1", "type": "function", "function": {"name": "transcribe_audio", "arguments": json.dumps({**arguments, "dry_run": True})}},
            {"id": "paid-1", "type": "function", "function": {"name": "transcribe_audio", "arguments": json.dumps(arguments)}},
        ]},
        {"content": "The transcript is complete."},
    ]

    async def fake_llm_message(messages, tools=None, stream_text=False):
        assert responses, "unexpected additional LLM call"
        return responses.pop(0)

    async def fake_execute_tool_call(session, call):
        call_args = json.loads(call["function"]["arguments"])
        calls.append(call_args)
        if call_args.get("dry_run") is not True:
            agent.events.append({"type": "tool_call", "tool": "transcribe_audio", "origin": "agent_paid_call", "is_error": False})
        return str(call["id"]), {
            "result": {"text": "Transcript result."},
            "preflight": {"dry_run": {"estimated_cost_inr": 0.01}},
        }

    monkeypatch.setattr(agent, "_llm_message", fake_llm_message)
    monkeypatch.setattr(agent, "_execute_tool_call", fake_execute_tool_call)
    run_dir = asyncio.run(agent.run())
    trace = [json.loads(line) for line in (run_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()]

    assert agent.status == "completed"
    assert calls == [arguments]
    assert sum(event.get("type") == "llm_dryrun_coalesced" for event in trace) == 1


def test_agent_runs_mcp_tool_chain_and_writes_assignment_outputs(tmp_path, monkeypatch):
    monkeypatch.setenv("BODHAN_CACHE_BACKEND", "memory")
    monkeypatch.setenv("BODHAN_ARTIFACT_DIR", str(tmp_path / "mcp-artifacts"))
    monkeypatch.setenv("BODHAN_MODEL_CACHE_ARTIFACT_DIR", str(tmp_path / "model-artifacts"))
    input_file = tmp_path / "input.txt"
    input_file.write_text("A short English note.", encoding="utf-8")
    agent = TaskAgent(
        task="Read the supplied note and report its content.",
        input_paths=[input_file],
        output_root=tmp_path / "runs",
        max_steps=5,
        max_cost_inr=1.0,
        tool_timeout_seconds=15,
        llm_timeout_seconds=5,
        model="test-model",
        base_url="http://unused.invalid/v1",
        api_key="test-key",
    )
    responses = [
        {
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "get_file", "arguments": json.dumps({"file_ref": str(input_file)})},
                }
            ],
        },
        {"content": "The note says: A short English note."},
    ]

    async def fake_llm_message(messages, tools=None, stream_text=False):
        assert responses, "unexpected additional LLM call"
        return responses.pop(0)

    monkeypatch.setattr(agent, "_llm_message", fake_llm_message)
    run_dir = asyncio.run(agent.run())

    assert agent.status == "completed"
    assert (run_dir / "result.md").is_file()
    assert (run_dir / "report.json").is_file()
    assert (run_dir / "trace.jsonl").is_file()
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    assert report["steps"] == 1
    assert report["tool_events"][0]["tool"] == "get_file"
    assert "A short English note" in (run_dir / "result.md").read_text(encoding="utf-8")
    assert any(event["type"] == "plan" for event in [json.loads(line) for line in (run_dir / "trace.jsonl").read_text().splitlines()])


def test_agent_deduplicates_identical_tool_calls_in_one_llm_response(tmp_path, monkeypatch):
    monkeypatch.setenv("BODHAN_CACHE_BACKEND", "memory")
    monkeypatch.setenv("BODHAN_ARTIFACT_DIR", str(tmp_path / "mcp-artifacts"))
    monkeypatch.setenv("BODHAN_MODEL_CACHE_ARTIFACT_DIR", str(tmp_path / "model-artifacts"))
    input_file = tmp_path / "input.txt"
    input_file.write_text("One reusable result.", encoding="utf-8")
    agent = TaskAgent(
        task="Read the supplied note once.",
        input_paths=[input_file],
        output_root=tmp_path / "runs",
        max_steps=5,
        max_cost_inr=1.0,
        tool_timeout_seconds=15,
        llm_timeout_seconds=5,
        model="test-model",
        base_url="http://unused.invalid/v1",
        api_key="test-key",
    )
    responses = [
        {
            "content": None,
            "tool_calls": [
                {
                    "id": f"call-{index}",
                    "type": "function",
                    "function": {"name": "get_file", "arguments": json.dumps({"file_ref": str(input_file)})},
                }
                for index in (1, 2)
            ],
        },
        {"content": "The note says: One reusable result."},
    ]

    async def fake_llm_message(messages, tools=None, stream_text=False):
        assert responses, "unexpected additional LLM call"
        return responses.pop(0)

    monkeypatch.setattr(agent, "_llm_message", fake_llm_message)
    run_dir = asyncio.run(agent.run())

    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    trace = [json.loads(line) for line in (run_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    assert agent.status == "completed"
    assert report["steps"] == 1
    assert sum(event.get("origin") == "llm_tool_call" for event in report["tool_events"] if event.get("type") == "tool_call") == 1
    assert sum(event.get("type") == "tool_call_deduplicated" for event in trace) == 1


def test_agent_reuses_successful_tool_result_across_llm_turns(tmp_path, monkeypatch):
    monkeypatch.setenv("BODHAN_CACHE_BACKEND", "memory")
    monkeypatch.setenv("BODHAN_ARTIFACT_DIR", str(tmp_path / "mcp-artifacts"))
    monkeypatch.setenv("BODHAN_MODEL_CACHE_ARTIFACT_DIR", str(tmp_path / "model-artifacts"))
    input_file = tmp_path / "input.txt"
    input_file.write_text("One cached read.", encoding="utf-8")
    agent = TaskAgent(
        task="Read this text and report it.", input_paths=[input_file], output_root=tmp_path / "runs",
        max_steps=5, max_cost_inr=1.0, tool_timeout_seconds=15, llm_timeout_seconds=5,
        model="test-model", base_url="http://unused.invalid/v1", api_key="test-key",
    )
    call = lambda call_id: {
        "id": call_id,
        "type": "function",
        "function": {"name": "get_file", "arguments": json.dumps({"file_ref": str(input_file)})},
    }
    responses = [
        {"content": None, "tool_calls": [call("call-1")]},
        {"content": None, "tool_calls": [call("call-2")]},
        {"content": "It says: One cached read."},
    ]

    async def fake_llm_message(messages, tools=None, stream_text=False):
        assert responses, "unexpected additional LLM call"
        return responses.pop(0)

    monkeypatch.setattr(agent, "_llm_message", fake_llm_message)
    run_dir = asyncio.run(agent.run())
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    trace = [json.loads(line) for line in (run_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    assert agent.status == "completed"
    assert report["steps"] == 1
    assert sum(event.get("origin") == "llm_tool_call" for event in report["tool_events"] if event.get("type") == "tool_call") == 1
    assert sum(event.get("type") == "tool_call_deduplicated" for event in trace) == 1


def test_agent_does_not_mark_empty_final_response_completed(tmp_path, monkeypatch):
    agent = TaskAgent(
        task="Summarize this input.", input_paths=[], output_root=tmp_path / "runs",
        max_steps=3, max_cost_inr=1.0, tool_timeout_seconds=5, llm_timeout_seconds=5,
        model="test-model", base_url="http://unused.invalid/v1", api_key="test-key",
    )
    responses = [{"content": None}, {"content": None}]

    class FakeSession:
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return None
        async def initialize(self): return None
        async def list_tools(self): return SimpleNamespace(tools=[])
        async def read_resource(self, _uri): return SimpleNamespace(contents=[])

    @asynccontextmanager
    async def fake_stdio(_params):
        yield object(), object()

    async def fake_llm(_messages, _tools=None, stream_text=False):
        return responses.pop(0)

    monkeypatch.setattr(agent_module, "stdio_client", fake_stdio)
    monkeypatch.setattr(agent_module, "ClientSession", lambda *_args, **_kwargs: FakeSession())
    monkeypatch.setattr(agent, "_llm_message", fake_llm)
    run_dir = asyncio.run(agent.run())
    assert agent.status == "failed"
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert "no final response" in report["stop_reason"].lower()
    trace = [json.loads(line) for line in (run_dir / "trace.jsonl").read_text().splitlines()]
    assert sum(item.get("type") == "empty_response_recovery" for item in trace) == 1


def test_multistep_get_file_then_translation_is_chained_end_to_end(tmp_path, monkeypatch):
    source = tmp_path / "source.txt"
    source.write_text("Good morning", encoding="utf-8")
    agent = TaskAgent(
        task="Read the supplied phrase, then translate it to Hindi.", input_paths=[source], output_root=tmp_path / "runs",
        max_steps=8, max_cost_inr=1.0, tool_timeout_seconds=15, llm_timeout_seconds=5,
        model="test-model", base_url="http://unused.invalid/v1", api_key="test-key",
    )
    llm_responses = [
        {"content": None, "tool_calls": [{"id": "read-1", "type": "function", "function": {"name": "get_file", "arguments": json.dumps({"file_ref": str(source)})}}]},
        {"content": None, "tool_calls": [{"id": "translate-1", "type": "function", "function": {"name": "translate_text", "arguments": json.dumps({"text": "Good morning", "target_language": "hi"})}}]},
        {"content": "The translation is: सुप्रभात."},
    ]
    calls = []

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def initialize(self):
            return None

        async def list_tools(self):
            return SimpleNamespace(tools=[])

        async def read_resource(self, _uri):
            return SimpleNamespace(contents=[SimpleNamespace(text="{}")])

        async def call_tool(self, name, arguments, **_kwargs):
            calls.append((name, dict(arguments)))
            if name == "get_file":
                return SimpleNamespace(structuredContent={"file_ref": str(source), "content": "Good morning", "is_text": True}, content=[], isError=False)
            if arguments.get("dry_run") is True:
                return SimpleNamespace(structuredContent={"dry_run": True, "estimated_cost_inr": 0.01}, content=[], isError=False)
            return SimpleNamespace(structuredContent={"translation": "सुप्रभात", "target_language": "hi", "cost_inr": 0.01}, content=[], isError=False)

    @asynccontextmanager
    async def fake_stdio(_params):
        yield object(), object()

    async def fake_llm(_messages, _tools=None, stream_text=False):
        assert llm_responses, "unexpected extra planner turn"
        return llm_responses.pop(0)

    async def no_verification(*_args, **_kwargs):
        return None

    monkeypatch.setattr(agent_module, "stdio_client", fake_stdio)
    monkeypatch.setattr(agent_module, "ClientSession", lambda *_args, **_kwargs: FakeSession())
    monkeypatch.setattr(agent, "_llm_message", fake_llm)
    monkeypatch.setattr(agent, "_verify_tool_output", no_verification)
    run_dir = asyncio.run(agent.run())
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    assert agent.status == "completed"
    assert [name for name, _args in calls] == ["get_file", "translate_text", "translate_text"]
    assert calls[1][1]["dry_run"] is True and calls[2][1].get("dry_run") is None
    assert "सुप्रभात" in (run_dir / "result.md").read_text(encoding="utf-8")
    assert {event["tool"] for event in report["tool_events"] if event.get("type") == "tool_call"} == {"get_file", "translate_text"}


def test_checkpoint_round_trip_restores_state_and_inflight_resume_blocks(tmp_path):
    input_file = tmp_path / "source.txt"
    input_file.write_text("resume source", encoding="utf-8")
    agent = TaskAgent(
        task="Continue this task.", input_paths=[input_file], output_root=tmp_path / "runs",
        max_steps=9, max_cost_inr=2.0, tool_timeout_seconds=20, llm_timeout_seconds=10,
        model="test-model", base_url="http://unused.invalid/v1", api_key="test-key",
    )
    agent.steps = 3
    agent.cost_inr = 0.02
    messages = [{"role": "user", "content": "continue"}]
    agent._save_checkpoint(messages)
    state = json.loads((agent.run_dir / "checkpoint.json").read_text(encoding="utf-8"))
    resumed = TaskAgent(
        task=state["task"], input_paths=[input_file], output_root=Path(state["output_root"]),
        max_steps=state["max_steps"], max_cost_inr=state["max_cost_inr"],
        tool_timeout_seconds=state["tool_timeout_seconds"], llm_timeout_seconds=state["llm_timeout_seconds"],
        max_parallel_tools=state["max_parallel_tools"], dry_run_only=state["dry_run_only"],
        model=state["model"], base_url=state["base_url"], api_key="refreshed-key", resume_state=state,
    )
    assert resumed.run_dir == agent.run_dir
    assert resumed.steps == 3 and resumed.cost_inr == 0.02
    assert resumed._conversation_messages == messages

    state["in_flight"] = [{"call_id": "paid-call-1", "tool": "translate_text", "paid": True}]
    blocked = TaskAgent(
        task=state["task"], input_paths=[input_file], output_root=Path(state["output_root"]),
        max_steps=state["max_steps"], max_cost_inr=state["max_cost_inr"],
        tool_timeout_seconds=state["tool_timeout_seconds"], llm_timeout_seconds=state["llm_timeout_seconds"],
        max_parallel_tools=state["max_parallel_tools"], dry_run_only=state["dry_run_only"],
        model=state["model"], base_url=state["base_url"], api_key="refreshed-key", resume_state=state,
    )
    run_dir = asyncio.run(blocked.run())
    assert blocked.status == "blocked"
    assert blocked.steps == 3
    assert "not replayed" in (run_dir / "result.md").read_text(encoding="utf-8")
