"""
Unit regression tests for Batch B: ASR and Shared Stages.
Covers B1 (sparse-chunk drop fix, VAD snapping & clamping),
B2 (gather with sibling task cancellation on fatal errors, over-length 400 split),
B3 (shared-stage failure isolation, report/manifest generation, exit 1, Settings exit 2),
B4 (resume shared stages from work_dir checkpoints with content-hash checks).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from bodhan_dub.gateway.bodhan import BodhanClient
from bodhan_dub.logging import PipelineLogger
from bodhan_dub.manifest import RunManifest
from bodhan_dub.media.chunks import AudioChunk
from bodhan_dub.media.vad import Pause, SpeechRegion, VADResult
from bodhan_dub.orchestrator import PipelineOrchestrator
from bodhan_dub.stages.asr import ASRStage


def make_mock_settings(tmp_path: Path):
    s = MagicMock()
    s.cache_dir = tmp_path / "cache"
    s.bodhan_base_url = "https://api.bodhan.ai/v1"
    s.bodhan_transcribe_key = "test_key"
    s.bodhan_translate_key = "test_key"
    s.bodhan_speak_key = "test_key"
    s.llm_base_url = "https://api.groq.com/openai/v1"
    s.llm_api_key = "test_key"
    s.timeout_asr = 10.0
    s.timeout_translate = 10.0
    s.timeout_tts = 10.0
    s.semaphore_asr = 2
    s.semaphore_translate = 5
    s.semaphore_tts = 3
    s.semaphore_llm = 2
    s.allow_test_hosts = True
    return s


# ---------------------------------------------------------------------------
# B1: Sparse-chunk VAD snapping and clamping (25s chunk with ~8s speech keeps text)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_B1_sparse_chunk_vad_snap():
    logger = MagicMock()
    mock_client = MagicMock(spec=BodhanClient)

    # Coarse single segment returned by API covering full 25s chunk (0.0 to 25.0)
    coarse_resp = {
        "text": "This is important speech that must not be dropped.",
        "segments": [
            {
                "start": 0.0,
                "end": 25.0,
                "text": "This is important speech that must not be dropped.",
            }
        ],
    }
    mock_client.transcribe = AsyncMock(return_value=coarse_resp)

    # VAD speech is only 8s long: from 10.0s to 18.0s (8/25 = 32% < 40%)
    speech_regions = [SpeechRegion(start=10.0, end=18.0)]
    pauses = [Pause(start=0.0, end=10.0), Pause(start=18.0, end=25.0)]
    vad_res = VADResult(
        total_duration=25.0,
        speech_regions=speech_regions,
        pauses=pauses,
        engine_used="silero_onnx",
    )

    stage = ASRStage(bodhan_client=mock_client, logger=logger, speech_overlap_min_ratio=0.40)
    chunks = [AudioChunk(id=0, start=0.0, end=25.0)]

    with patch("bodhan_dub.stages.asr.slice_wav_bytes", return_value=b"DUMMY_BYTES"):
        segments = await stage.run(
            audio_path=Path("dummy.wav"),
            chunks=chunks,
            vad_result=vad_res,
        )

    # Segment must NOT be dropped as hallucination!
    assert len(segments) == 1, "Sparse chunk segment was wrongly dropped"
    seg = segments[0]
    assert seg.text == "This is important speech that must not be dropped."
    # Segment boundaries must be clamped to VAD speech region [10.0, 18.0]
    assert seg.start == 10.0
    assert seg.end == 18.0


# ---------------------------------------------------------------------------
# B2: Gather with sibling task cancellation on fatal errors & overlength 400
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_B2_asr_cancels_siblings_on_fatal():
    logger = MagicMock()
    mock_client = MagicMock(spec=BodhanClient)

    # Create two chunks: chunk 0 fails with fatal 401 Unauthorized, chunk 1 would sleep
    chunk1_cancelled = False

    async def _mock_transcribe(audio_bytes, **kwargs):
        segment_id = kwargs.get("segment_id", "")
        if "chunk_0" in segment_id:
            req = httpx.Request("POST", "https://api.bodhan.ai/v1/audio/transcriptions")
            resp = httpx.Response(401, request=req, text="Unauthorized: invalid key")
            raise httpx.HTTPStatusError("Unauthorized", request=req, response=resp)
        else:
            nonlocal chunk1_cancelled
            try:
                await asyncio.sleep(5.0)
                return {"text": "Late speech", "segments": []}
            except asyncio.CancelledError:
                chunk1_cancelled = True
                raise

    mock_client.transcribe = AsyncMock(side_effect=_mock_transcribe)

    speech_regions = [SpeechRegion(start=1.0, end=4.0), SpeechRegion(start=12.0, end=15.0)]
    vad_res = VADResult(
        total_duration=20.0,
        speech_regions=speech_regions,
        pauses=[],
        engine_used="silero_onnx",
    )

    chunks = [
        AudioChunk(id=0, start=0.0, end=10.0),
        AudioChunk(id=1, start=10.0, end=20.0),
    ]

    stage = ASRStage(bodhan_client=mock_client, logger=logger)

    with patch("bodhan_dub.stages.asr.slice_wav_bytes", return_value=b"DUMMY_BYTES"):
        with pytest.raises(httpx.HTTPStatusError):
            await stage.run(audio_path=Path("dummy.wav"), chunks=chunks, vad_result=vad_res)

    # Chunk 1 must be cancelled immediately, avoiding orphan calls
    assert chunk1_cancelled is True


@pytest.mark.asyncio
async def test_B2_overlength_400_splits_chunk():
    logger = MagicMock()
    mock_client = MagicMock(spec=BodhanClient)

    # When duration > 15s, mock returns 400 "audio duration exceeds maximum 30 seconds"
    async def _mock_transcribe(audio_bytes, duration_seconds=10.0, **kwargs):
        if duration_seconds > 15.0:
            req = httpx.Request("POST", "https://api.bodhan.ai/v1/audio/transcriptions")
            resp = httpx.Response(400, request=req, text="Audio duration exceeds maximum 30 seconds limit")
            raise httpx.HTTPStatusError("Duration exceeded", request=req, response=resp)
        return {
            "text": "split success",
            "segments": [{"start": 0.0, "end": duration_seconds, "text": "split success"}],
        }

    mock_client.transcribe = AsyncMock(side_effect=_mock_transcribe)

    speech_regions = [SpeechRegion(start=2.0, end=20.0)]
    vad_res = VADResult(
        total_duration=25.0,
        speech_regions=speech_regions,
        pauses=[],
        engine_used="silero_onnx",
    )

    chunks = [AudioChunk(id=0, start=0.0, end=24.0)]
    stage = ASRStage(bodhan_client=mock_client, logger=logger)

    with patch("bodhan_dub.stages.asr.slice_wav_bytes", return_value=b"DUMMY_WAV"):
        results = await stage.run(audio_path=Path("dummy.wav"), chunks=chunks, vad_result=vad_res)

    assert len(results) > 0
    assert any("split success" in s.text for s in results)


# ---------------------------------------------------------------------------
# B3: Shared-stage failure isolation & Settings exit code 2
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_B3_shared_stage_failure_isolation(tmp_path: Path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    video_path = tmp_path / "test.mp4"
    video_path.write_bytes(b"dummy_mp4_bytes")

    logger = PipelineLogger(run_id="test_run_b3", log_file=out_dir / "run.log.jsonl")
    manifest = RunManifest(run_id="test_run_b3", input_file=str(video_path), target_langs=["hi", "bn"])

    settings = make_mock_settings(tmp_path)
    registry = MagicMock()

    orchestrator = PipelineOrchestrator(
        settings=settings,
        registry=registry,
        logger=logger,
        manifest=manifest,
        out_dir=out_dir,
    )

    async def _failing_shared(*args, **kwargs):
        raise RuntimeError("FFmpeg decoding error on corrupt stream")

    orchestrator.run_shared_stages = _failing_shared

    exit_code = await orchestrator.run(
        video_path=video_path,
        target_langs=["hi", "bn"],
    )

    # Exit code must be 1 for failure in shared stages
    assert exit_code == 1

    # Report, manifest, and run log must be written
    assert (out_dir / "report.json").exists()
    assert (out_dir / "manifest.json").exists()
    assert (out_dir / "run.log.jsonl").exists()

    report_data = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    assert report_data["overall_status"] == "failed"
    for lang in ["hi", "bn"]:
        assert report_data["languages"][lang]["status"] == "failed"
        assert any("FFmpeg decoding error" in err for err in report_data["languages"][lang]["errors"])

    await orchestrator.close()


def test_B3_settings_exit_2_on_invalid_env():
    # If Settings raises ValidationError (e.g. invalid type or broken config), cli returns 2
    from bodhan_dub.cli import async_main

    args = MagicMock()
    args.input = Path("test.mp4")
    args.langs = "hi"
    args.out = "test_out"
    args.log_level = "INFO"
    args.estimate_only = False
    args.max_cost_inr = None

    with patch("bodhan_dub.cli.Settings", side_effect=ValueError("BODHAN_SPEAK_KEY missing or corrupt")):
        loop = asyncio.new_event_loop()
        code = loop.run_until_complete(async_main(args))
        loop.close()
        assert code == 2


# ---------------------------------------------------------------------------
# B4: Resume shared stages from work_dir checkpoints
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_B4_resume_shared_stages(tmp_path: Path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"TEST_VIDEO_BYTES_B4")

    logger = MagicMock()
    manifest = MagicMock()
    manifest.is_stage_completed = MagicMock(return_value=True)

    settings = make_mock_settings(tmp_path)
    registry = MagicMock()

    orchestrator = PipelineOrchestrator(
        settings=settings,
        registry=registry,
        logger=logger,
        manifest=manifest,
        out_dir=out_dir,
        resume=True,
    )
    orchestrator.work_dir = work_dir

    # Pre-populate checkpoint files with matching input hash
    st = video_path.stat()
    import hashlib
    inp_hash = hashlib.sha256(f"{st.st_size}_{st.st_mtime}_{video_path.name}".encode("utf-8")).hexdigest()[:16]

    (work_dir / "audio_16k.wav").write_bytes(b"DUMMY_16K")
    (work_dir / "audio_original.wav").write_bytes(b"DUMMY_ORIG")

    vad_data = {
        "input_hash": inp_hash,
        "data": {
            "total_duration": 10.0,
            "engine_used": "checkpoint",
            "speech_regions": [{"start": 1.0, "end": 4.0}],
            "pauses": [{"start": 4.0, "end": 10.0}],
        },
    }
    (work_dir / "vad_result.json").write_text(json.dumps(vad_data), encoding="utf-8")

    asr_data = {
        "input_hash": inp_hash,
        "data": [
            {"text": "Resumed speech segment", "start": 1.0, "end": 4.0, "timestamp_source": "checkpoint"}
        ],
    }
    (work_dir / "asr_segments.json").write_text(json.dumps(asr_data), encoding="utf-8")

    seg_data = {
        "input_hash": inp_hash,
        "data": [
            {
                "id": "s0", "index": 0, "text": "Resumed speech segment",
                "start": 1.0, "end": 4.0, "duration": 3.0,
                "slot": [1.0, 4.0], "slack_before": 0.0, "slack_after": 0.0,
            }
        ],
    }
    (work_dir / "english_segments.json").write_text(json.dumps(seg_data), encoding="utf-8")

    gl_data = {
        "input_hash": inp_hash,
        "data": {
            "user_terms": {"term1": {"hi": "अनुवाद"}},
            "extracted_terms": ["term1"],
            "canonical_map": {"hi": {"term1": "अनुवाद"}},
        },
    }
    (work_dir / "glossary.json").write_text(json.dumps(gl_data), encoding="utf-8")

    vad_res, segs, orig_path, glossary = await orchestrator.run_shared_stages(video_path, ["hi"])

    # Must have reloaded all stages directly from checkpoint files
    assert vad_res.total_duration == 10.0
    assert len(segs) == 1
    assert segs[0].text == "Resumed speech segment"
    assert glossary is not None
    assert "term1" in glossary.user_terms

    await orchestrator.close()
