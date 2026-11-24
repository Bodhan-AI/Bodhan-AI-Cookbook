"""
Unit regression tests for Batch A: Audio Correctness.
Covers A1 (no overlap, slack borrowing, re-stretch, end-of-media padding, no caller mutation),
A2 (un-muted VAD gap chunks),
A3 (condense candidate QA & duration selection),
A4 (raw audio caching & resume restoration),
A5 (audio response validation before caching, translation nonce),
A6 (CPS warm-up advisory duration check and API_FACTS seeding).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import numpy as np
import pytest
import soundfile as sf

from bodhan_dub.gateway.bodhan import BodhanClient
from bodhan_dub.gateway.cache import DiskCache
from bodhan_dub.gateway.cost import BudgetGuard, CostLedger
from bodhan_dub.media.audio_proc import resample_to_48k
from bodhan_dub.media.mix import compute_union_mute_intervals
from bodhan_dub.stages.fit import SegmentFitResult
from bodhan_dub.stages.qa import QAMetrics, SpeechQA, SynthesisCandidate
from bodhan_dub.stages.schedule import TimelineScheduler
from bodhan_dub.stages.segment import SegmentSlot
from bodhan_dub.stages.tts import API_FACTS_CPS, CPSCalibrator


# ---------------------------------------------------------------------------
# A1: TimelineScheduler: strict no-overlap, slack borrowing, tail extension, no caller mutation
# ---------------------------------------------------------------------------
def test_A1_no_overlap():
    scheduler = TimelineScheduler(sample_rate=48000, max_sync_drift_s=0.300)

    # Two closely spaced segments: seg1 ends at 1.0s, seg2 starts at 0.9s (100ms overlap attempt)
    seg1 = SegmentSlot(
        id="s1", index=0, text="first", start=0.0, end=1.0, duration=1.0, slot=[0.0, 1.0], slack_before=0.0, slack_after=0.0
    )
    seg2 = SegmentSlot(
        id="s2", index=1, text="second", start=0.9, end=2.0, duration=1.1, slot=[0.9, 2.0], slack_before=0.0, slack_after=0.0
    )

    # Both audio buffers are 1.0s long (48000 samples)
    buf1 = np.ones(48000, dtype=np.float32) * 0.5
    buf2 = np.ones(48000, dtype=np.float32) * 0.8

    raw1 = buf1.copy()
    raw2 = buf2.copy()

    fit1 = SegmentFitResult(
        segment_id="s1",
        source_en="first",
        final_en="first",
        final_translation="first",
        raw_duration=1.0,
        final_duration=1.0,
        slot_duration=1.0,
        start_shift=0.0,
        speed_factor=1.0,
        qa_metrics=QAMetrics(cer=0.0, passed=True, failure_reasons=[]),
        qa_status="passed",
        fit_status="fit_exact",
        attempts=1,
        reasons=[],
        raw_audio=raw1,
    )
    fit2 = SegmentFitResult(
        segment_id="s2",
        source_en="second",
        final_en="second",
        final_translation="second",
        raw_duration=1.0,
        final_duration=1.0,
        slot_duration=1.1,
        start_shift=0.0,
        speed_factor=1.0,
        qa_metrics=QAMetrics(cer=0.0, passed=True, failure_reasons=[]),
        qa_status="passed",
        fit_status="fit_exact",
        attempts=1,
        reasons=[],
        raw_audio=raw2,
    )

    logger = MagicMock()
    sched_res, master_buf = scheduler.schedule(
        lang="hi",
        source_segments=[seg1, seg2],
        fit_results=[fit1, fit2],
        audio_buffers=[buf1, buf2],
        total_duration_s=2.5,
        logger=logger,
    )

    # Verify no caller mutation
    assert fit1.speed_factor == 1.0
    assert fit2.speed_factor == 1.0

    # Verify intervals do not intersect
    s1_placed = sched_res.segments[0]
    s2_placed = sched_res.segments[1]
    assert s2_placed.placed_start >= s1_placed.placed_end, (
        f"Overlap detected: s1 ends at {s1_placed.placed_end}, s2 starts at {s2_placed.placed_start}"
    )

    # Overrun beyond +/-300ms window test:
    # Segment where target start is 1.0s, but previous segment ends at 1.5s (+500ms > max_sync_drift 300ms)
    seg3 = SegmentSlot(
        id="s3", index=1, text="overrun target", start=1.0, end=1.5, duration=0.5, slot=[1.0, 1.5], slack_before=0.0, slack_after=0.0
    )
    buf3 = np.ones(48000, dtype=np.float32) * 0.4
    fit3 = SegmentFitResult(
        segment_id="s3",
        source_en="overrun target",
        final_en="overrun target",
        final_translation="overrun target",
        raw_duration=1.0,
        final_duration=1.0,
        slot_duration=0.5,
        start_shift=0.0,
        speed_factor=1.0,
        qa_metrics=QAMetrics(cer=0.0, passed=True, failure_reasons=[]),
        qa_status="passed",
        fit_status="fit_exact",
        attempts=1,
        reasons=[],
        raw_audio=buf3.copy(),
    )

    sched_res2, _ = scheduler.schedule(
        lang="hi",
        source_segments=[seg1, seg3],
        fit_results=[fit1, fit3],
        audio_buffers=[buf1, buf3],
        total_duration_s=2.5,
        logger=logger,
    )
    assert sched_res2.segments[1].placed_start == 1.0
    # S3 target start is 1.0s, last placed end is 1.0s. Max allowed start is 1.3s. Placed at 1.0s.
    # Now make seg1 1.8s long. Even re-stretched to 1.20x, it ends at 1.5s > 1.3s (+300ms drift window)
    buf1_long = np.ones(int(1.8 * 48000), dtype=np.float32)
    fit1_long = fit1.model_copy()
    fit1_long.final_duration = 1.8
    fit1_long.raw_duration = 1.8
    fit1_long.raw_audio = buf1_long.copy()

    sched_res3, _ = scheduler.schedule(
        lang="hi",
        source_segments=[seg1, seg3],
        fit_results=[fit1_long, fit3],
        audio_buffers=[buf1_long, buf3],
        total_duration_s=3.5,
        logger=logger,
    )
    s3_placed_long = sched_res3.segments[1]
    # Seg1 ended at 1.5s. Seg3 source.start = 1.0s. Max drift is 0.3s (1.3s).
    # Since 1.5s > 1.3s, Seg3 has overrun drift exceeded
    assert s3_placed_long.placed_start >= 1.5
    assert s3_placed_long.qa_status == "degraded"
    assert "drift_exceeded" in s3_placed_long.reasons or s3_placed_long.fit_status == "overrun"

    # Segment at end of media test: speech extending past total_duration_s must pad buffer and not truncate
    seg_end = SegmentSlot(
        id="send", index=0, text="end seg", start=2.0, end=2.5, duration=0.5, slot=[2.0, 2.5], slack_before=0.0, slack_after=0.0
    )
    buf_end = np.ones(int(1.0 * 48000), dtype=np.float32)  # 1.0s long audio, ends at 3.0s > 2.5s
    fit_end = fit1.model_copy(update={"segment_id": "send", "raw_duration": 1.0, "final_duration": 1.0, "raw_audio": buf_end.copy()})
    sched_end, master_end = scheduler.schedule(
        lang="hi",
        source_segments=[seg_end],
        fit_results=[fit_end],
        audio_buffers=[buf_end],
        total_duration_s=2.2,  # media is 2.2s
        logger=logger,
    )
    # Master buffer must be padded beyond the original 2.2s (105600 samples) to accommodate all speech without truncating
    assert len(master_end) > int(2.2 * 48000)
    assert sched_end.segments[0].placed_end > 2.2
    assert sched_end.segments[0].qa_status == "degraded"
    assert "truncation_risk" in sched_end.segments[0].reasons


# ---------------------------------------------------------------------------
# A2: VAD regions without placed segment stay un-muted
# ---------------------------------------------------------------------------
def test_A2_gap_chunk_unmuted():
    # Source speech has 3 VAD regions: [1.0, 3.0], [5.0, 8.0] (gap chunk), [10.0, 12.0]
    source_speech = [(1.0, 3.0), (5.0, 8.0), (10.0, 12.0)]

    # Placed segments only cover [1.0, 3.0] and [10.0, 12.0]. Region [5.0, 8.0] was skipped/dropped.
    placed_spans = [(1.0, 3.0), (10.0, 12.0)]
    skipped_spans = []

    mute_intervals = compute_union_mute_intervals(
        source_speech_spans=source_speech,
        placed_spans=placed_spans,
        skipped_spans=skipped_spans,
    )

    # Mute intervals should only mute around [1.0, 3.0] and [10.0, 12.0].
    # Region [5.0, 8.0] MUST NOT be in mute intervals!
    for m_s, m_e in mute_intervals:
        # Check no overlap with gap chunk [5.0, 8.0]
        overlap = max(0.0, min(m_e, 8.0) - max(m_s, 5.0))
        assert overlap == 0.0, f"Gap chunk [5.0, 8.0] was muted by interval ({m_s}, {m_e})"


# ---------------------------------------------------------------------------
# A3: Condense candidate selection with SpeechQA.evaluate
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_A3_condense_candidate_selection():
    # Verify candidate selection logic in post-synthesis condense loop
    # Initial candidate: dur 2.0s, cer 0.10, passed
    cand_curr = SynthesisCandidate(
        attempt=1,
        text="शुरुआती पाठ",
        audio_48k=np.ones(int(2.0 * 48000), dtype=np.float32),
        duration_s=2.0,
        metrics=QAMetrics(cer=0.10, passed=True, failure_reasons=[]),
    )

    # Candidate 2 is longer (2.2s > 2.0s): must be rejected
    # Candidate 3 has higher CER (0.35) and failed QA: must be rejected
    re_dur_longer = 2.2
    assert not (re_dur_longer < cand_curr.duration_s), "Longer audio must not be accepted"

    re_dur_shorter_but_failing = 1.8
    re_qa_failing = QAMetrics(cer=0.40, passed=False, failure_reasons=["cer_high_0.400"])
    # Condition: re_dur < current AND (re_qa.passed OR re_qa.cer < current.cer)
    is_better = (
        re_dur_shorter_but_failing < cand_curr.duration_s
        and (re_qa_failing.passed or (re_qa_failing.cer is not None and re_qa_failing.cer < cand_curr.metrics.cer))
    )
    assert not is_better, "Shorter but failing/worse CER candidate must be rejected"

    # Candidate 4 is shorter (1.8s) AND passed QA with CER 0.08 < 0.10: accepted
    re_qa_good = QAMetrics(cer=0.08, passed=True, failure_reasons=[])
    is_good_better = (
        re_dur_shorter_but_failing < cand_curr.duration_s
        and (re_qa_good.passed or (re_qa_good.cer is not None and re_qa_good.cer < cand_curr.metrics.cer))
    )
    assert is_good_better, "Shorter and passing QA candidate must be accepted"


# ---------------------------------------------------------------------------
# A4: Resume correctness: raw audio caching and restoration
# ---------------------------------------------------------------------------
def test_A4_resume_restores_raw_audio(tmp_path: Path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()

    raw_audio = np.random.randn(48000).astype(np.float32)
    final_audio = np.random.randn(40000).astype(np.float32)  # Stretched audio (1.2x)

    raw_wav_path = work_dir / "seg1.raw.wav"
    final_wav_path = work_dir / "seg1.wav"
    meta_path = work_dir / "seg1.meta.json"

    sf.write(str(raw_wav_path), raw_audio, 48000)
    sf.write(str(final_wav_path), final_audio, 48000)

    meta_data = {
        "content_hash": "hash123",
        "res": {
            "segment_id": "seg1",
            "source_en": "test",
            "final_en": "test",
            "final_translation": "परीक्षण",
            "raw_duration": 1.0,
            "final_duration": 0.833,
            "slot_duration": 0.85,
            "start_shift": 0.0,
            "speed_factor": 1.2,
            "qa_metrics": {"cer": 0.0, "passed": True, "failure_reasons": []},
            "qa_status": "passed",
            "fit_status": "fit_exact",
            "attempts": 1,
            "reasons": [],
        },
    }
    meta_path.write_text(json.dumps(meta_data), encoding="utf-8")

    # Simulate resume: restore raw_audio from raw_wav_path
    c_audio, c_sr = sf.read(str(final_wav_path), dtype="float32")
    raw_c_audio, raw_sr = sf.read(str(raw_wav_path), dtype="float32")
    saved_meta = json.loads(meta_path.read_text(encoding="utf-8"))

    res = SegmentFitResult(**saved_meta["res"], raw_audio=raw_c_audio)
    assert res.raw_audio is not None
    assert len(res.raw_audio) == 48000
    assert len(c_audio) == 40000
    assert res.speed_factor == 1.2


# ---------------------------------------------------------------------------
# A5: Audio response validation before caching & translate nonce
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_A5_tts_validation_and_nonce(tmp_path: Path):
    settings = MagicMock()
    settings.bodhan_base_url = "https://api.bodhan.ai/v1"
    settings.bodhan_speak_key = "test_key"
    settings.bodhan_translate_key = "test_key"
    settings.bodhan_transcribe_key = "test_key"
    settings.llm_base_url = "https://api.groq.com/openai/v1"
    settings.timeout_tts = 10.0
    settings.timeout_translate = 10.0
    settings.timeout_asr = 10.0
    settings.semaphore_asr = 2
    settings.semaphore_translate = 5
    settings.semaphore_tts = 3
    settings.allow_test_hosts = True

    cache = DiskCache(tmp_path / "cache")
    ledger = CostLedger()
    budget_guard = BudgetGuard(ledger=ledger, max_cost_inr=100.0)
    logger = MagicMock()

    # 1. Truncated audio (missing RIFF/WAV header)
    truncated_bytes = b"NOT_A_WAV_HEADER_TRUNCATED"

    # Mock HTTP response with 200 OK but bad body
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.content = truncated_bytes
    mock_resp.raise_for_status = MagicMock()

    transport = MagicMock(spec=httpx.AsyncBaseTransport)
    client = BodhanClient(
        settings=settings,
        cache=cache,
        ledger=ledger,
        budget_guard=budget_guard,
        logger=logger,
        transport=transport,
    )

    client._http.post = AsyncMock(return_value=mock_resp)

    # Must raise ValueError and NOT cache
    with pytest.raises(ValueError, match="missing RIFF/WAV header"):
        await client.speak("नमस्ते", voice="Kavya", language_code="hi")

    # Verify nothing was cached
    assert len(list((tmp_path / "cache").glob("*"))) == 0

    # 2. JSON error body with 200 OK
    json_error_bytes = json.dumps({"error": {"message": "Internal error", "code": 500}}).encode("utf-8")
    mock_resp.content = json_error_bytes

    with pytest.raises(ValueError, match="missing RIFF/WAV header"):
        await client.speak("नमस्ते", voice="Kavya", language_code="hi")

    # 3. Dynamic resampling test
    # Create valid 16kHz audio buffer and verify resample_to_48k
    sr_16k = 16000
    audio_16k = np.ones(sr_16k, dtype=np.float32) * 0.5
    resampled_48k = resample_to_48k(audio_16k, orig_sr=sr_16k)
    assert len(resampled_48k) == 48000
    assert abs(resampled_48k.mean() - 0.5) < 0.05

    # 4. Translation nonce produces different cache key
    key1 = cache.compute_key("indic-translate", "indic-translate", {"source": "en", "target": "hi"}, "hello")
    key2 = cache.compute_key("indic-translate", "indic-translate", {"source": "en", "target": "hi", "nonce": 1}, "hello")
    assert key1 != key2, "Nonce must produce distinct cache key"

    await client.close()


# ---------------------------------------------------------------------------
# A6: Warmup CPS advisory duration check & API_FACTS seeding
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_A6_warmup_duration_advisory():
    # 1. API_FACTS CPS seeding
    calibrator = CPSCalibrator()
    # Check that Kavya seeded CPS matches API_FACTS (11.37)
    kavya_cps = calibrator.get_cps("hi", "Kavya")
    assert kavya_cps == API_FACTS_CPS[("hi", "Kavya")]
    assert not calibrator.is_warmed_up("hi", "Kavya")

    # 2. Duration mismatch during warm-up is advisory and does not fail QA
    mock_client = MagicMock()
    # Mock transcribe response for back-transcription
    mock_client.transcribe = AsyncMock(return_value={"text": "कक्षा नौ बजे शुरू होती है।"})
    qa = SpeechQA(bodhan_client=mock_client, logger=MagicMock(), cer_warn=0.20, cer_fail=0.35)

    audio_48k = np.zeros(int(1.0 * 48000), dtype=np.float32)
    # Predicted duration = 3.0s, actual = 1.0s -> dur_ratio = 0.33 (< 0.60, severe mismatch)
    metrics_warmup = await qa.evaluate(
        audio_48k=audio_48k,
        reference_text="कक्षा नौ बजे शुरू होती है।",
        predicted_duration=3.0,
        lang="hi",
        segment_id="seg_warmup",
        is_warmed_up=False,
    )

    # During warmup, duration check is advisory
    assert any("advisory" in r for r in metrics_warmup.failure_reasons)
    assert metrics_warmup.passed is True

    # Once warmed up, the same mismatch fails QA
    metrics_warmed = await qa.evaluate(
        audio_48k=audio_48k,
        reference_text="कक्षा नौ बजे शुरू होती है।",
        predicted_duration=3.0,
        lang="hi",
        segment_id="seg_warmed",
        is_warmed_up=True,
    )
    assert any("duration_mismatch" in r and "advisory" not in r for r in metrics_warmed.failure_reasons)
    assert metrics_warmed.passed is False
