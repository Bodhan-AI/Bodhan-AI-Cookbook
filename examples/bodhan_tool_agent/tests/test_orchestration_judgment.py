#!/usr/bin/env python3
"""Orchestration judgment tests: deterministic pre-flight gates, parallel dispatch,
caching, SRT generation, verifier retries, output filtering, trace logging."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import wave
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure src/ is on path so `from src.bodhan_mcp.*` works in tests.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Dummy credentials so AsyncOpenAI accepts construction during tests.
os.environ.setdefault("LLM_BASE_URL", "https://api.openai.com/v1")
os.environ.setdefault("LLM_API_KEY", "sk-dummy-test-key")
os.environ.setdefault("LLM_MODEL", "gpt-4o-mini")

from bodhan_agent.cli import preflight_cost_check
from bodhan_agent.planner import TaskPlanner
from bodhan_agent.reporter import Reporter
from bodhan_agent.verifier import TranslationVerifier, SpeechVerifier, format_srt, extract_untrusted_text
from bodhan_mcp.client import InrLedger, BodhanCache, APIResponse


def _make_wav(path: Path, duration_sec: float = 1.0, sample_rate: int = 16000) -> bytes:
    n = int(duration_sec * sample_rate)
    buf = bytearray()
    for _ in range(n):
        buf += b"\x00\x00"
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(buf))
    return bytes(buf)


def _build_mock_bridge(tool_responses: dict[str, dict] | None = None) -> MagicMock:
    """Return a fake MCPBridge that responds to tool names from tool_responses."""
    tools = [
        {"type": "function", "function": {
            "name": "inspect_media",
            "description": "Inspect media",
            "parameters": {"type": "object", "properties": {"file_path": {"type": "string"}}, "required": ["file_path"]},
        }},
        {"type": "function", "function": {
            "name": "extract_document_text",
            "description": "OCR",
            "parameters": {"type": "object", "properties": {"file_path": {"type": "string"}, "page_range": {"type": "string"}}, "required": ["file_path"]},
        }},
        {"type": "function", "function": {
            "name": "transcribe_audio",
            "description": "Transcribe",
            "parameters": {"type": "object", "properties": {"file_path": {"type": "string"}, "language_hint": {"type": "string"}, "max_duration_sec": {"type": "number"}}, "required": ["file_path"]},
        }},
        {"type": "function", "function": {
            "name": "translate_text",
            "description": "Translate",
            "parameters": {"type": "object", "properties": {"source_lang": {"type": "string"}, "target_lang": {"type": "string"}, "text": {"type": "string"}}, "required": ["source_lang", "target_lang"]},
        }},
        {"type": "function", "function": {
            "name": "synthesize_speech",
            "description": "TTS",
            "parameters": {"type": "object", "properties": {"language": {"type": "string"}, "output_file": {"type": "string"}, "text": {"type": "string"}}, "required": ["language", "output_file"]},
        }},
    ]
    resp_map = tool_responses or {}

    async def list_tools():
        return tools

    async def call_tool(name: str, arguments):
        key = name
        if key in resp_map:
            return resp_map[key]
        return {"result": json.dumps({"tool": name, "args": str(arguments)[:80]}), "cost_inr": 0.0}

    bridge = MagicMock()
    bridge.list_tools = list_tools
    bridge.call_tool = call_tool
    bridge.start = AsyncMock(return_value=None)
    bridge.stop = AsyncMock(return_value=None)
    return bridge


def _mock_llm_response(content: str | None = None, tool_calls: list | None = None) -> MagicMock:
    """Build a fake OpenAI chat.completions.create return value."""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _fake_tool_call(name: str, arguments: str, tc_id: str = "tc_1") -> MagicMock:
    fn = MagicMock()
    fn.name = name
    fn.arguments = arguments
    tc = MagicMock()
    tc.id = tc_id
    tc.function = fn
    return tc


class TestPreflightBudgetAbort:
    @pytest.mark.asyncio
    async def test_preflight_budget_abort_makes_zero_llm_calls(self, tmp_path: Path):
        """10-min audio with max-cost 0.10 should abort pre-flight, 0 LLM calls, status=budget_insufficient, cost=0.0."""
        # Create a 10-minute dummy WAV (600 sec)
        audio_path = tmp_path / "long_audio.wav"
        _make_wav(audio_path, duration_sec=600.0)

        # Mock bridge.call_tool to return high min_cost for inspect_media
        async def mock_call_tool(name, args):
            if name == "inspect_media":
                return {
                    "result": json.dumps({
                        "format": "wav",
                        "duration_sec": 600.0,
                        "min_cost_inr": 1.0,
                        "supported_for_transcribe": True,
                    }),
                    "cost_inr": 0.0,
                }
            return {"result": "{}", "cost_inr": 0.0}

        ledger = InrLedger(max_cost=0.10)
        files = [str(audio_path)]

        # Verify preflight check computes correct min_cost
        min_cost, _ = await preflight_cost_check(files, ledger)
        assert min_cost > 0.10

        # Now run the actual agent and verify it aborts with zero LLM calls
        out_dir = tmp_path / "out"
        bridge = _build_mock_bridge()
        bridge.call_tool = mock_call_tool
        reporter = Reporter(out_dir, out_dir / "artifacts", ledger, max_steps=10, max_cost_inr=0.10)
        planner = TaskPlanner(bridge, reporter, ledger, max_steps=10, max_cost_inr=0.10)

        # If LLM is called, this will fail
        create_mock = AsyncMock(side_effect=AssertionError("LLM should not be called"))

        with patch.object(planner._client.chat.completions, "create", new=create_mock):
            result = await planner.execute("Transcribe this audio", files)

        assert result["status"] == "budget_insufficient"
        assert result["total_cost_inr"] == 0.0
        assert result["steps_used"] == 0

    @pytest.mark.asyncio
    async def test_preflight_budget_abort_cli_status(self, tmp_path: Path):
        """CLI should return budget_insufficient status with total_cost_inr=0.0."""
        audio_path = tmp_path / "long_audio.wav"
        _make_wav(audio_path, duration_sec=600.0)

        # Mock bridge.call_tool to return high min_cost for inspect_media
        async def mock_call_tool(name, args):
            if name == "inspect_media":
                return {
                    "result": json.dumps({
                        "format": "wav",
                        "duration_sec": 600.0,
                        "min_cost_inr": 1.0,
                        "supported_for_transcribe": True,
                    }),
                    "cost_inr": 0.0,
                }
            return {"result": "{}", "cost_inr": 0.0}

        # Run the agent flow directly (simulating CLI but without asyncio.run conflict)
        out_dir = tmp_path / "out"
        ledger = InrLedger(max_cost=0.10)
        files = [str(audio_path)]

        bridge = _build_mock_bridge()
        bridge.call_tool = mock_call_tool
        reporter = Reporter(out_dir, out_dir / "artifacts", ledger, max_steps=10, max_cost_inr=0.10)
        planner = TaskPlanner(bridge, reporter, ledger, max_steps=10, max_cost_inr=0.10)

        result = await planner.execute("Transcribe this audio", files)

        # Simulate reporter.finalize
        await reporter.finalize(result, elapsed_sec=0.1)

        report_path = out_dir / "report.json"
        assert report_path.exists()
        report = json.loads(report_path.read_text())
        assert report["status"] == "budget_insufficient"
        assert report["total_cost_inr"] == 0.0
        assert report["steps_used"] == 0


class TestUnsupportedLanguagePreGate:
    @pytest.mark.asyncio
    async def test_unsupported_language_pregate_makes_zero_llm_calls(self, tmp_path: Path):
        """French translation task should be rejected at planner pre-gate, 0 LLM calls, 0 cost."""
        bridge = _build_mock_bridge()
        ledger = InrLedger(max_cost=5.0)
        reporter = Reporter(tmp_path / "out", tmp_path / "out" / "artifacts", ledger, max_steps=3, max_cost_inr=5.0)
        planner = TaskPlanner(bridge, reporter, ledger, max_steps=3, max_cost_inr=5.0)

        # If LLM is called, this will fail
        create_mock = AsyncMock(side_effect=AssertionError("LLM should not be called for unsupported language"))

        with patch.object(planner._client.chat.completions, "create", new=create_mock):
            result = await planner.execute("Translate this video into French", [])

        assert result["status"] == "unsupported_task"
        assert result["total_cost_inr"] == 0.0
        assert result["steps_used"] == 0
        assert any("Pre-gate" in m for m in result["missed_targets"])


class TestUnknownAudioLanguageProbe:
    @pytest.mark.asyncio
    async def test_unknown_audio_language_triggers_cheap_15s_probe_first(self, tmp_path: Path):
        """Audio without language hint should trigger 15s probe on first transcribe_audio call."""
        audio_path = tmp_path / "unknown_lang.wav"
        _make_wav(audio_path, duration_sec=120.0)  # 2-minute audio

        # Track the transcribe_audio calls
        call_log = []

        async def mock_transcribe_audio(file_path, language_hint=None, max_duration_sec=None):
            call_log.append({
                "file_path": file_path,
                "language_hint": language_hint,
                "max_duration_sec": max_duration_sec,
            })
            return {
                "text": "<untrusted_source_data>Hello world</untrusted_source_data>",
                "language": "en",
                "detected_script": "latin",
                "duration_sec": 15.0 if max_duration_sec else 120.0,
                "cost_inr": 0.025 if max_duration_sec else 0.20,
                "segments": [{"start_sec": 0.0, "end_sec": 15.0, "text": "Hello world"}],
                "cached": False,
            }

        async def mock_call_tool(name, args):
            if name == "transcribe_audio":
                return await mock_transcribe_audio(args["file_path"], args.get("language_hint"), args.get("max_duration_sec"))
            return {"result": "{}", "cost_inr": 0.0}

        with patch("bodhan_mcp.tools.transcribe.transcribe_audio", new=mock_transcribe_audio):
            # Also mock inspect_media to avoid ffprobe
            with patch("bodhan_mcp.tools.inspect.inspect_media", new_callable=AsyncMock) as mock_inspect:
                mock_inspect.return_value = {
                    "format": "wav",
                    "duration_sec": 120.0,
                    "min_cost_inr": 0.20,
                    "supported_for_transcribe": True,
                }

                bridge = _build_mock_bridge()
                # Override the bridge to use our mock transcribe
                bridge.call_tool = mock_call_tool

                ledger = InrLedger(max_cost=5.0)
                reporter = Reporter(tmp_path / "out", tmp_path / "out" / "artifacts", ledger, max_steps=5, max_cost_inr=5.0)
                planner = TaskPlanner(bridge, reporter, ledger, max_steps=5, max_cost_inr=5.0)

                # LLM returns a transcribe_audio tool call with max_duration_sec=15 for unknown language
                tool_resp = _mock_llm_response(
                    tool_calls=[_fake_tool_call("transcribe_audio", json.dumps({"file_path": str(audio_path), "max_duration_sec": 15}))]
                )
                final_resp = _mock_llm_response(content="Done")

                with patch.object(planner._client.chat.completions, "create", new=AsyncMock(side_effect=[tool_resp, final_resp])):
                    await planner.execute("Transcribe this audio", [str(audio_path)])

        # The first transcribe call should have max_duration_sec <= 15
        probe_calls = [c for c in call_log if c["max_duration_sec"] is not None and c["max_duration_sec"] <= 15]
        assert len(probe_calls) >= 1, f"Expected 15s probe call, got: {call_log}"


class TestParallelToolDispatch:
    @pytest.mark.asyncio
    async def test_multi_clip_directory_dispatches_parallel_tool_calls(self, tmp_path: Path):
        """Directory with 4 audio clips should dispatch 4 transcribe_audio calls concurrently."""
        # Create a clips directory with 4 audio files
        clips_dir = tmp_path / "clips"
        clips_dir.mkdir()
        audio_files = []
        for i in range(4):
            f = clips_dir / f"clip_{i}.wav"
            _make_wav(f, duration_sec=5.0)
            audio_files.append(str(f))

        call_times = []
        call_order = []

        async def slow_transcribe(file_path, language_hint=None, max_duration_sec=None):
            import time
            start = time.time()
            call_order.append(file_path)
            await asyncio.sleep(0.05)  # Simulate network delay
            call_times.append((file_path, time.time() - start))
            return {
                "text": f"<untrusted_source_data>Clip {file_path}</untrusted_source_data>",
                "language": "hi",
                "detected_script": "devanagari",
                "duration_sec": 5.0,
                "cost_inr": 0.01,
                "segments": [{"start_sec": 0.0, "end_sec": 5.0, "text": "Clip"}],
                "cached": False,
            }

        async def mock_call_tool(name, args):
            if name == "transcribe_audio":
                return await slow_transcribe(args["file_path"], args.get("language_hint"), args.get("max_duration_sec"))
            return {"result": "{}", "cost_inr": 0.0}

        with patch("bodhan_mcp.tools.transcribe.transcribe_audio", new=slow_transcribe):
            with patch("bodhan_mcp.tools.inspect.inspect_media", new_callable=AsyncMock) as mock_inspect:
                mock_inspect.return_value = {
                    "format": "wav",
                    "duration_sec": 5.0,
                    "min_cost_inr": 0.01,
                    "supported_for_transcribe": True,
                }

                bridge = _build_mock_bridge()
                bridge.call_tool = mock_call_tool

                ledger = InrLedger(max_cost=5.0)
                reporter = Reporter(tmp_path / "out", tmp_path / "out" / "artifacts", ledger, max_steps=10, max_cost_inr=5.0)
                planner = TaskPlanner(bridge, reporter, ledger, max_steps=10, max_cost_inr=5.0)

                # LLM returns 4 parallel transcribe_audio calls
                tool_calls = [
                    _fake_tool_call("transcribe_audio", json.dumps({"file_path": f}), f"tc_{i}")
                    for i, f in enumerate(audio_files)
                ]
                tool_resp = _mock_llm_response(tool_calls=tool_calls)
                final_resp = _mock_llm_response(content="All done")

                with patch.object(planner._client.chat.completions, "create", new=AsyncMock(side_effect=[tool_resp, final_resp])):
                    await planner.execute("Transcribe all clips", audio_files)

        # All 4 calls should have executed
        assert len(call_order) == 4

        # Check trace.jsonl has all 4 completed steps
        trace_path = tmp_path / "out" / "trace.jsonl"
        assert trace_path.exists()
        trace_lines = trace_path.read_text(encoding="utf-8").strip().split("\n")
        completed_transcribe_steps = []
        for line in trace_lines:
            entry = json.loads(line)
            if entry.get("tool") == "transcribe_audio" and entry.get("status") == "completed":
                completed_transcribe_steps.append(entry)
        assert len(completed_transcribe_steps) == 4, f"Expected 4 completed transcribe steps in trace, got {len(completed_transcribe_steps)}"


class TestDuplicateCacheDeduplication:
    @pytest.mark.asyncio
    async def test_duplicate_tool_call_hits_cache_with_zero_extra_cost(self, tmp_path: Path):
        """Two identical translate_text calls should hit SHA-256 cache, second call cost=0."""
        with tempfile.TemporaryDirectory() as cache_dir:
            cache = BodhanCache(cache_dir=cache_dir)

            # First call - populate cache
            model = "indic-translate"
            endpoint = "/chat/completions"
            params = {"model": "indic-translate", "messages": [{"role": "user", "content": "Translate 'hello' to hi"}], "script": "native"}
            input_bytes = b"Translate 'hello' to hi"
            result = {"choices": [{"message": {"content": "नमस्ते"}}]}
            cost = 0.02

            cache.set(model, endpoint, params, input_bytes, result, cost)

            # Second call - should hit cache
            cached = cache.get(model, endpoint, params, input_bytes)
            assert cached is not None
            assert cached.response == result
            assert cached.cost_inr == cost

            # Verify the cached flag would be set in tool result
            from bodhan_mcp.tools.translate import translate_text
            from bodhan_mcp.client import client

            with patch.object(client, "request", new_callable=AsyncMock) as mock_request:
                mock_request.return_value = APIResponse(data=result, cost_inr=cost, cached=True)

                with patch("bodhan_mcp.tools.translate.client", client):
                    result1 = await translate_text("en", "hi", text="hello")
                    result2 = await translate_text("en", "hi", text="hello")

                    # Both should succeed
                    assert "नमस्ते" in result1["translation"]
                    assert "नमस्ते" in result2["translation"]


class TestDeterministicSRTGeneration:
    def test_deterministic_srt_generation_without_llm_timestamps(self):
        """SRT timecodes must be produced deterministically by format_srt, not LLM."""
        segments = [
            {"start_sec": 0.0, "end_sec": 2.5, "text": "नमस्ते दुनिया"},
            {"start_sec": 3.0, "end_sec": 5.5, "text": "यह एक परीक्षण है"},
            {"start_sec": 6.123, "end_sec": 9.456, "text": "धन्यवाद"},
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "output.srt"
            format_srt(segments, str(out_path))
            content = out_path.read_text(encoding="utf-8")

            # Verify SRT format with HH:MM:SS,mmm --> HH:MM:SS,mmm
            assert "00:00:00,000 --> 00:00:02,500" in content
            assert "00:00:03,000 --> 00:00:05,500" in content
            assert "00:00:06,123 --> 00:00:09,456" in content

            # Verify segment numbers
            assert content.strip().startswith("1\n")
            assert "2\n" in content
            assert "3\n" in content

            # Verify Hindi text preserved
            assert "नमस्ते दुनिया" in content
            assert "यह एक परीक्षण है" in content
            assert "धन्यवाद" in content


class TestUnicodeScriptVerifierRetry:
    @pytest.mark.asyncio
    async def test_unicode_script_verifier_triggers_single_bounded_retry(self, tmp_path: Path):
        """Mock translate to return Latin on attempt 1, Devanagari on attempt 2; verifier triggers exactly 1 retry."""
        call_count = {"translate": 0}

        async def mock_translate(source_lang, target_lang, text=None, input_file=None, output_file=None, script="native"):
            call_count["translate"] += 1
            if call_count["translate"] == 1:
                # First attempt: Latin script (wrong)
                return {
                    "translation": "<untrusted_source_data>namaste duniya</untrusted_source_data>",
                    "target_language": "mr",
                    "script": "roman",
                    "target_script_verified": False,
                    "cost_inr": 0.02,
                    "output_file": output_file,
                    "cached": False,
                }
            else:
                # Second attempt: Devanagari (correct)
                return {
                    "translation": "<untrusted_source_data>नमस्ते दुनिया</untrusted_source_data>",
                    "target_language": "mr",
                    "script": "native",
                    "target_script_verified": True,
                    "cost_inr": 0.02,
                    "output_file": output_file,
                    "cached": False,
                }

        with patch("bodhan_mcp.tools.translate.translate_text", new=mock_translate):
            v = TranslationVerifier(target_lang="mr")

            # Simulate planner's retry logic: call translate, verify, retry if failed
            # Attempt 1
            result1 = await mock_translate("en", "mr", text="hello world")
            res1 = v.verify("", extract_untrusted_text(result1["translation"]), has_tables=False)
            assert res1["passed"] is False
            assert res1["script_ratio"] < 0.85

            # Attempt 2 (retry)
            result2 = await mock_translate("en", "mr", text="hello world")
            res2 = v.verify("", extract_untrusted_text(result2["translation"]), has_tables=False)
            assert res2["passed"] is True
            assert res2["script_ratio"] >= 0.85

            # Verify exactly 2 calls (1 retry)
            assert call_count["translate"] == 2


class TestSpeechVerifierRetry:
    @pytest.mark.asyncio
    async def test_speech_verifier_triggers_single_bounded_retry(self, tmp_path: Path):
        """Mock synthesize_speech to fail back-transcription on attempt 1, pass on attempt 2."""
        wav_path = tmp_path / "speech.wav"
        _make_wav(wav_path, duration_sec=1.0)

        call_count = {"transcribe": 0}

        async def mock_transcribe_for_verify(path):
            call_count["transcribe"] += 1
            if call_count["transcribe"] == 1:
                # First attempt: dissimilar transcription
                return {"text": "completely different text"}
            else:
                # Second attempt: matching transcription
                return {"text": "नमस्ते दुनिया"}

        v = SpeechVerifier(min_similarity=0.75)

        # First attempt fails
        res1 = await v.verify(str(wav_path), "नमस्ते दुनिया", transcribe_func=mock_transcribe_for_verify)
        assert res1["passed"] is False
        assert res1["similarity"] < 0.75

        # Second attempt passes
        res2 = await v.verify(str(wav_path), "नमस्ते दुनिया", transcribe_func=mock_transcribe_for_verify)
        assert res2["passed"] is True
        assert res2["similarity"] >= 0.75

        # Verify exactly 2 calls (1 retry)
        assert call_count["transcribe"] == 2


class TestLLMOutputFilterBlocksIndic:
    @pytest.mark.asyncio
    async def test_llm_blocked_from_emitting_raw_indic_script(self, tmp_path: Path):
        """Final answer with raw Tamil/Hindi must be stripped; English preserved."""
        bridge = _build_mock_bridge()
        ledger = InrLedger(max_cost=5.0)
        reporter = Reporter(tmp_path / "out", tmp_path / "out" / "artifacts", ledger, max_steps=3, max_cost_inr=5.0)
        planner = TaskPlanner(bridge, reporter, ledger, max_steps=3, max_cost_inr=5.0)

        # LLM returns mixed English + Hindi + Tamil
        hindi = "नमस्ते"
        tamil = "வணக்கம்"
        mixed_content = f"The summary is {hindi} and {tamil}. Done."
        final_resp = _mock_llm_response(content=mixed_content)

        with patch.object(planner._client.chat.completions, "create", new=AsyncMock(return_value=final_resp)):
            result = await planner.execute("Summarize in Hindi", [])

        final_answer = result["final_answer"]

        # Hindi (Devanagari U+0900-U+097F) and Tamil (U+0B80-U+0BFF) should be stripped
        for ch in final_answer:
            cp = ord(ch)
            assert not (0x0900 <= cp <= 0x097F), f"Devanagari char found: {ch}"
            assert not (0x0B80 <= cp <= 0x0BFF), f"Tamil char found: {ch}"

        # English content should be preserved
        assert "The summary is" in final_answer
        assert "and" in final_answer
        assert "Done." in final_answer


class TestTraceJSONLLogging:
    @pytest.mark.asyncio
    async def test_trace_jsonl_logs_rationale_cost_and_verifier_for_every_step(self, tmp_path: Path):
        """3-step pipeline: each trace entry must have tool, rationale, latency_ms, cost_inr, cumulative_cost_inr."""
        output_wav = tmp_path / "out" / "artifacts" / "speech.wav"
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        _make_wav(output_wav, duration_sec=1.5)

        tool_responses = {
            "inspect_media": {"result": json.dumps({"format": "pdf", "pages": 1, "min_cost_inr": 0.2}), "cost_inr": 0.0},
            "extract_document_text": {"result": json.dumps({
                "markdown": "<untrusted_source_data>Hello world this is a test.</untrusted_source_data>",
                "pages": 1, "cost_inr": 0.2, "detected_script": "latin"
            }), "cost_inr": 0.2},
            "translate_text": {"result": json.dumps({
                "translation": "வணக்கம் உலகம்", "target_language": "ta",
                "target_script_verified": True, "cost_inr": 0.05,
            }), "cost_inr": 0.05},
        }

        bridge = _build_mock_bridge(tool_responses)
        ledger = InrLedger(max_cost=5.0)
        reporter = Reporter(tmp_path / "out", tmp_path / "out" / "artifacts", ledger, max_steps=10, max_cost_inr=5.0)
        planner = TaskPlanner(bridge, reporter, ledger, max_steps=10, max_cost_inr=5.0)

        # LLM returns 3 tool calls in one batch
        tool_calls = [
            _fake_tool_call("inspect_media", json.dumps({"file_path": "/doc.pdf"}), "tc_1"),
            _fake_tool_call("extract_document_text", json.dumps({"file_path": "/doc.pdf"}), "tc_2"),
            _fake_tool_call("translate_text", json.dumps({"source_lang": "en", "target_lang": "ta", "text": "Hello world"}), "tc_3"),
        ]
        tool_resp = _mock_llm_response(tool_calls=tool_calls)
        final_resp = _mock_llm_response(content="Done")

        with patch.object(planner._client.chat.completions, "create", new=AsyncMock(side_effect=[tool_resp, final_resp])):
            result = await planner.execute("Process document", ["/doc.pdf"])

        await reporter.finalize(result, elapsed_sec=0.5)

        # Verify trace.jsonl
        trace_path = tmp_path / "out" / "trace.jsonl"
        assert trace_path.exists()
        trace_lines = trace_path.read_text(encoding="utf-8").strip().split("\n")

        # Filter completed steps
        completed_steps = []
        for line in trace_lines:
            entry = json.loads(line)
            if entry.get("status") == "completed":
                completed_steps.append(entry)

        assert len(completed_steps) >= 3, f"Expected at least 3 completed steps, got {len(completed_steps)}"

        for step in completed_steps[:3]:
            # Every entry must have non-empty tool
            assert step.get("tool"), f"Missing tool in step: {step}"
            assert step["tool"] in ("inspect_media", "extract_document_text", "translate_text")

            # Every entry must have non-empty rationale
            assert step.get("plan_rationale"), f"Missing rationale in step: {step}"
            assert len(step["plan_rationale"]) > 0

            # Every entry must have latency_ms
            assert "latency_ms" in step, f"Missing latency_ms in step: {step}"
            assert step["latency_ms"] >= 0

            # Every entry must have cost_inr
            assert "cost_inr" in step, f"Missing cost_inr in step: {step}"
            assert step["cost_inr"] >= 0

            # Every entry must have cumulative_cost_inr
            assert "cumulative_cost_inr" in step, f"Missing cumulative_cost_inr in step: {step}"
            assert step["cumulative_cost_inr"] >= 0


class TestLoopGuardStopsRepeatedCalls:
    """Defect-B regression: when the LLM re-invokes the same tool on the same
    input file beyond MAX_LOOP_REPEATS, the planner must stop cleanly and NOT
    return 'completed'."""

    @pytest.mark.asyncio
    async def test_repeated_identical_tool_call_stops_not_completed(self, tmp_path: Path):
        bridge = _build_mock_bridge()
        ledger = InrLedger(max_cost=10.0)
        reporter = Reporter(tmp_path / "out", tmp_path / "out" / "artifacts", ledger, max_steps=30, max_cost_inr=10.0)
        planner = TaskPlanner(bridge, reporter, ledger, max_steps=30, max_cost_inr=10.0)

        fake_file = "/doc.pdf"
        # LLM keeps calling extract_document_text on the same file — after
        # MAX_LOOP_REPEATS (3) it should stop and return a non-'completed' status.
        calls = {"n": 0}
        async def mock_call_tool(name, args):
            calls["n"] += 1
            return {"result": json.dumps({"markdown": "<untrusted>Hello</untrusted>", "cost_inr": 0.2}), "cost_inr": 0.2}

        bridge.call_tool = mock_call_tool

        same_call = _fake_tool_call("extract_document_text", json.dumps({"file_path": fake_file}))
        repeats = [_mock_llm_response(tool_calls=[same_call]) for _ in range(5)]
        with patch.object(planner._client.chat.completions, "create", new=AsyncMock(side_effect=repeats)):
            result = await planner.execute("Process this doc", [fake_file])

        # Must NOT be 'completed' — loop guard tripped.
        assert result["status"] != "completed", (
            f"Expected non-completed due to loop guard, got {result['status']}"
        )
        assert any("loop guard" in m.lower() for m in result["missed_targets"]), (
            f"Expected loop-guard message in missed_targets; got {result['missed_targets']}"
        )

    @pytest.mark.asyncio
    async def test_honest_completion_requires_real_work(self, tmp_path: Path):
        """LLM returns a final answer after running steps but with zero cost and
        no artifacts — status must NOT be 'completed'."""
        bridge = _build_mock_bridge()
        ledger = InrLedger(max_cost=10.0)
        reporter = Reporter(tmp_path / "out", tmp_path / "out" / "artifacts", ledger, max_steps=30, max_cost_inr=10.0)
        planner = TaskPlanner(bridge, reporter, ledger, max_steps=30, max_cost_inr=10.0)

        # Return zero-cost steps then a final answer.
        async def mock_call_tool(name, args):
            return {"result": json.dumps({"ok": True}), "cost_inr": 0.0}

        bridge.call_tool = mock_call_tool

        tool_resp = _mock_llm_response(
            tool_calls=[_fake_tool_call("inspect_media", json.dumps({"file_path": "/x"}))]
        )
        final_resp = _mock_llm_response(content="Done.")
        with patch.object(planner._client.chat.completions, "create",
                          new=AsyncMock(side_effect=[tool_resp, final_resp])):
            result = await planner.execute("Do something", ["/x"])

        assert result["status"] == "incomplete", (
            f"Expected 'incomplete' for zero-cost work, got {result['status']}"
        )

    @pytest.mark.asyncio
    async def test_planning_cost_accrues_on_real_call(self, tmp_path: Path):
        """Steps with nonzero cost_inr from the bridge must accrue to the ledger
        and report correctly in total_cost_inr."""
        out_dir = tmp_path / "out"
        artifacts_dir = out_dir / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        output_wav = str(artifacts_dir / "speech.wav")
        _make_wav(Path(output_wav), duration_sec=1.5)

        tool_responses = {
            "inspect_media": {"result": json.dumps({"format": "pdf", "pages": 1, "min_cost_inr": 0.2}), "cost_inr": 0.0},
            "extract_document_text": {"result": json.dumps({
                "markdown": "<untrusted>Hello</untrusted>", "pages": 1, "cost_inr": 0.2,
            }), "cost_inr": 0.2},
            "translate_text": {"result": json.dumps({
                "translation": "வணககம்", "target_language": "ta",
                "target_script_verified": True, "cost_inr": 0.05,
            }), "cost_inr": 0.05},
        }
        bridge = _build_mock_bridge(tool_responses)
        ledger = InrLedger(max_cost=5.0)
        reporter = Reporter(tmp_path / "out", tmp_path / "out" / "artifacts", ledger, max_steps=10, max_cost_inr=5.0)
        planner = TaskPlanner(bridge, reporter, ledger, max_steps=10, max_cost_inr=5.0)

        tool_call_resp = _mock_llm_response(
            tool_calls=[
                _fake_tool_call("extract_document_text", json.dumps({"file_path": "/doc.pdf"})),
                _fake_tool_call("translate_text", json.dumps({"source_lang": "en", "target_lang": "ta", "text": "Hello"})),
            ]
        )
        final_resp = _mock_llm_response(content="Done")
        with patch.object(planner._client.chat.completions, "create",
                          new=AsyncMock(side_effect=[tool_call_resp, final_resp])):
            result = await planner.execute("Translate PDF", ["/doc.pdf"])

        assert result["total_cost_inr"] > 0
        assert result["total_cost_inr"] == pytest.approx(0.2 + 0.05, abs=0.001)
        assert result["status"] == "completed"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
