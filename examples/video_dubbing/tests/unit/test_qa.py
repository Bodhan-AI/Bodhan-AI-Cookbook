"""
Unit tests for SpeechQA evaluation and retry planner.
"""

import numpy as np
import pytest

from bodhan_dub.logging import PipelineLogger
from bodhan_dub.stages.qa import QAMetrics, SpeechQA


@pytest.mark.asyncio
async def test_speech_qa_evaluate_passing():
    ref_text = "यह एक परीक्षण वाक्य है।"

    class MockBodhanClient:
        async def transcribe(
            self, audio_bytes, language, response_format, duration_seconds, stage, lang, segment_id
        ):
            return {"text": ref_text}

    logger = PipelineLogger(run_id="test_run")
    qa = SpeechQA(bodhan_client=MockBodhanClient(), logger=logger)

    # 1 second of normal 48kHz audio (not clipped, not silent)
    t = np.linspace(0, 1.0, 48000, endpoint=False, dtype=np.float32)
    clean_audio = 0.3 * np.sin(2 * np.pi * 300 * t)

    metrics = await qa.evaluate(
        audio_48k=clean_audio,
        reference_text=ref_text,
        predicted_duration=1.0,
        lang="hi",
        segment_id="seg_01",
    )

    assert metrics.passed is True
    assert metrics.cer == 0.0
    assert not metrics.is_clipped
    assert not metrics.is_silent


@pytest.mark.asyncio
async def test_speech_qa_evaluate_clipped_and_silent():
    ref_text = "नमस्ते"

    class MockBodhanClient:
        async def transcribe(
            self, audio_bytes, language, response_format, duration_seconds, stage, lang, segment_id
        ):
            return {"text": ref_text}

    logger = PipelineLogger(run_id="test_run")
    qa = SpeechQA(bodhan_client=MockBodhanClient(), logger=logger)

    # Clipped audio
    clipped_audio = np.ones(48000, dtype=np.float32)  # saturated at 1.0

    metrics = await qa.evaluate(
        audio_48k=clipped_audio,
        reference_text=ref_text,
        predicted_duration=1.0,
        lang="hi",
        segment_id="seg_01",
    )

    assert metrics.is_clipped is True
    assert metrics.passed is False
    assert "audio_clipping" in metrics.failure_reasons


def test_qa_plan_retry_action():
    logger = PipelineLogger(run_id="test_run")
    qa = SpeechQA(bodhan_client=None, logger=logger)

    # Tail omission -> split_text
    m_omission = QAMetrics(cer=0.1, passed=False, has_tail_omission=True)
    assert qa.plan_retry_action(m_omission, attempt=1) == "split_text"

    # High CER attempt 1 -> paraphrase_source
    m_high_cer = QAMetrics(cer=0.35, passed=False)
    assert qa.plan_retry_action(m_high_cer, attempt=1) == "paraphrase_source"

    # High CER attempt 2 -> condense
    assert qa.plan_retry_action(m_high_cer, attempt=2) == "condense"

    # Large duration overrun -> condense
    m_overrun = QAMetrics(cer=0.05, passed=False, duration_ratio=1.4)
    assert qa.plan_retry_action(m_overrun, attempt=1) == "condense"
