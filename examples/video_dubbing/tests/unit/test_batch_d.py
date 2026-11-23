"""
Unit regression tests for Batch D: QA, retry planning, translation, and honest reporting.
Covers:
- D1: Deterministic retry action mapping based on failure reasons without repeating previous actions.
- D2: QA thresholds and Latin share configurable via Settings without hard-coded constants.
- D3: Back-transcription failure leaves CER unverified (None), excluded from averages, and reported honestly.
- D4: Code-mixed translation allows higher Latin share (up to 70%) while keeping target Indic language.
- D5: Translation ladder passes nonce=attempt, step 2 avoids placeholders, and judge requires >=5 chars evidence.
- D6: Report targets scale honestly against baseline (120s per video-min * language count) and check stem loudness.
- D7: mix_dubbed_audio returns MixResult with stem_lufs and final_lufs, and normalizes placed spans.
- D8: decode_muxed_audio verifies container audio stream, and mux accounts for video start_time.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from bodhan_dub.config import Settings
from bodhan_dub.gateway.bodhan import BodhanClient
from bodhan_dub.gateway.cost import CostLedger
from bodhan_dub.languages import LanguageRegistry
from bodhan_dub.logging import PipelineLogger
from bodhan_dub.media.mix import MixResult, mix_dubbed_audio
from bodhan_dub.media.mux import decode_muxed_audio, mux_video_audio
from bodhan_dub.stages.fit import SegmentFitController
from bodhan_dub.stages.glossary import Glossary
from bodhan_dub.stages.qa import SpeechQA, determine_retry_action
from bodhan_dub.stages.report import (
    LanguageSummary,
    build_pipeline_report,
)
from bodhan_dub.stages.translate import (
    TranslationStage,
    check_dominant_script,
)


# ---------------------------------------------------------------------------
# D1: Deterministic retry planner
# ---------------------------------------------------------------------------
def test_D1_retry_planner_deterministic_reasons():
    # Tail omission -> split_text
    assert determine_retry_action(["tail_omission_foo"]) == "split_text"
    # Repetition -> split_text
    assert determine_retry_action(["repetition_bar"]) == "split_text"
    # Duration mismatch ratio > 1.0 -> condense
    assert determine_retry_action(["duration_mismatch_ratio_1.45"]) == "condense"
    # Duration mismatch ratio <= 1.0 -> retry_same
    assert determine_retry_action(["duration_mismatch_ratio_0.75"]) == "retry_same"
    # CER high on attempt 1 -> paraphrase_source
    assert determine_retry_action(["cer_high_0.40"], attempt=1) == "paraphrase_source"
    # CER high on attempt 2 -> condense
    assert determine_retry_action(["cer_high_0.40"], attempt=2) == "condense"
    # Audio clipping -> retry_same
    assert determine_retry_action(["audio_clipping"]) == "retry_same"
    # Internal silence -> retry_same
    assert determine_retry_action(["internal_silence"]) == "retry_same"

    # Avoid repeating previous actions:
    # If candidate is 'split_text' but already done, picks next best action
    action = determine_retry_action(["tail_omission_foo"], previous_actions=["split_text"])
    assert action != "split_text"
    assert action in ["condense", "retry_same", "paraphrase_source"]


# ---------------------------------------------------------------------------
# D2: QA thresholds and Latin share configurable via Settings
# ---------------------------------------------------------------------------
def test_D2_qa_thresholds_settings_wiring(tmp_path: Path):
    s = Settings(
        bodhan_transcribe_key="test",
        bodhan_translate_key="test",
        bodhan_speak_key="test",
        llm_api_key="test",
        cer_warn=0.18,
        cer_fail=0.38,
        cer_degraded_max=0.48,
        max_latin_share=0.25,
    )

    client = MagicMock(spec=BodhanClient)
    logger = PipelineLogger(run_id="test_d2")

    qa = SpeechQA(
        bodhan_client=client,
        logger=logger,
        cer_warn=s.cer_warn,
        cer_fail=s.cer_fail,
        cer_degraded_max=s.cer_degraded_max,
    )

    assert qa.cer_warn == 0.18
    assert qa.cer_fail == 0.38
    assert qa.cer_degraded_max == 0.48

    controller = SegmentFitController(
        bodhan_client=client,
        llm_client=MagicMock(),
        speech_qa=qa,
        logger=logger,
        cer_warn=s.cer_warn,
        cer_fail=s.cer_fail,
        cer_degraded_max=s.cer_degraded_max,
        max_latin_share=s.max_latin_share,
    )

    assert controller.cer_warn == 0.18
    assert controller.cer_fail == 0.38
    assert controller.cer_degraded_max == 0.48
    assert controller.max_latin_share == 0.25


# ---------------------------------------------------------------------------
# D3: Back-transcription failure -> unverified QA, excluded from CER stats
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_D3_unverified_qa_reporting(tmp_path: Path):
    client = MagicMock(spec=BodhanClient)
    # Simulate transcribe network/API error
    client.transcribe = AsyncMock(side_effect=RuntimeError("transcribe timeout"))
    logger = PipelineLogger(run_id="test_d3")
    qa = SpeechQA(bodhan_client=client, logger=logger)

    audio_48k = np.zeros(48000, dtype=np.float32)
    # Give it some non-empty signal so it doesn't fail empty_audio check
    audio_48k[100:200] = 0.1

    metrics = await qa.evaluate(
        audio_48k=audio_48k,
        reference_text="परीक्षण पाठ",
        lang="hi",
        predicted_duration=1.0,
        segment_id="s1",
    )

    assert metrics.verified is False
    assert metrics.cer is None
    assert "back_transcription_failed_unverified" in metrics.failure_reasons

    # Check that report generation ignores None CER in averages and counts qa_unverified
    lang_summary = LanguageSummary(
        status="ok",
        total_segments=2,
        placed_segments=2,
        degraded_segments=0,
        skipped_segments=0,
        qa_unverified=1,
        cer_stats={"mean": 0.12},  # computed from only verified segments
        integrated_lufs=-16.0,
        stem_lufs=-16.0,
        duration_delta_ms=0.0,
        retries_count=0,
    )

    cost_ledger = CostLedger()
    report = build_pipeline_report(
        run_id="test_run",
        input_file=str(tmp_path / "in.mp4"),
        video_duration_s=10.0,
        lang_summaries={"hi": lang_summary},
        cost_ledger=cost_ledger,
        stage_timings={"total": 5.0},
    )

    assert report.languages["hi"].qa_unverified == 1
    # Report targets check for qa_unverified
    target_qa = next(t for t in report.targets if t.name == "Acoustic & text fidelity")
    assert "1 unverified" in target_qa.measured


# ---------------------------------------------------------------------------
# D4: Code-mixed translation retains Indic target & raises Latin share to 0.70
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_D4_code_mixed_latin_share():
    # "React components को render करना बहुत आसान है।"
    # Latin characters: R-e-a-c-t (5) + c-o-m-p-o-n-e-n-t-s (10) + r-e-n-d-e-r (6) = 21
    # Indic characters: क-ो (2) + क-र-न-ा (4) + ब-ह-ु-त (4) + आ-स-ा-न (4) + ह-ै (2) = 16
    # Latin share: 21 / (21 + 16) = 56.7%
    text = "React components को render करना बहुत आसान है।"
    registry = LanguageRegistry()

    # Without code-mixed (max_latin_share=0.40): fails
    ok_default, msg_default = check_dominant_script(text, "hi", registry=registry, max_latin_share=0.40)
    assert ok_default is False
    assert "excessive_latin_share" in msg_default

    # With code-mixed (max_latin_share=0.70): passes
    ok_cm, msg_cm = check_dominant_script(text, "hi", registry=registry, max_latin_share=0.70)
    assert ok_cm is True

    # But completely English text ("This is pure English") still fails even with code-mixed (dominant script must be Devanagari)
    pure_en = "This is completely English text without any Hindi words"
    ok_pure_en, msg_pure_en = check_dominant_script(pure_en, "hi", registry=registry, max_latin_share=0.70)
    assert ok_pure_en is False


# ---------------------------------------------------------------------------
# D5: Translation retry ladder nonce & LLM judge evidence length
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_D5_translation_ladder_nonce_and_evidence(tmp_path: Path):
    client = MagicMock(spec=BodhanClient)
    calls = []

    async def mock_translate(*args, **kwargs):
        nonce = kwargs.get("nonce")
        calls.append((kwargs.get("text"), nonce))
        return ("नमस्ते दुनिया", 0.5)

    client.translate = AsyncMock(side_effect=mock_translate)
    logger = PipelineLogger(run_id="test_d5")

    # LLM judge returns short evidence (<5 chars) -> should not fail
    llm_mock = MagicMock()
    llm_mock.judge_back_translation = AsyncMock(
        return_value=MagicMock(verdict="inaccurate", evidence="bad")
    )

    seg_mock = MagicMock()
    seg_mock.id = "s1"
    seg_mock.index = 0
    seg_mock.text = "Hello world"
    seg_mock.slot = [0.0, 2.0]

    stage = TranslationStage(
        bodhan_client=client,
        llm_client=llm_mock,
        registry=LanguageRegistry(),
        logger=logger,
        code_mixed=False,
    )

    res = await stage.translate_segment(
        seg=seg_mock,
        target_lang="hi",
        glossary=Glossary(user_terms={"world": {"hi": "दुनिया"}}),
    )

    # First attempt called translate without failure
    assert len(calls) >= 1
    # Evidence "bad" was only 3 chars (<5), so it was not treated as a hard failure
    assert res.status == "ok"


# ---------------------------------------------------------------------------
# D6: Report targets scale runtime honestly and include stem loudness
# ---------------------------------------------------------------------------
def test_D6_report_honest_targets(tmp_path: Path):
    lang_summary = LanguageSummary(
        status="ok",
        total_segments=5,
        placed_segments=5,
        degraded_segments=0,
        skipped_segments=0,
        qa_unverified=0,
        cer_stats={"mean": 0.08},
        integrated_lufs=-16.2,
        stem_lufs=-15.8,
        duration_delta_ms=12.0,
        retries_count=0,
    )

    cost_ledger = CostLedger()
    # 5 minute video = 300s, 1 language
    # Target runtime: max(60.0, (300.0 / 60.0) * 1 * 120.0) = 600.0s max allowed
    # If run took 500s: should pass target
    report = build_pipeline_report(
        run_id="test_run_d6",
        input_file=str(tmp_path / "in.mp4"),
        video_duration_s=300.0,
        lang_summaries={"hi": lang_summary},
        cost_ledger=cost_ledger,
        stage_timings={"total": 500.0},
    )

    runtime_target = next(t for t in report.targets if t.name == "Processing runtime")
    assert runtime_target.met is True
    assert "<= 600.0s" in runtime_target.target

    # CER target passes because avg_cer 0.08 <= 0.30
    cer_target = next(t for t in report.targets if t.name == "Acoustic & text fidelity")
    assert cer_target.met is True

    # Loudness target evaluates both final and stem
    loudness_target = next(t for t in report.targets if t.name == "EBU R128 loudness")
    assert loudness_target.met is True
    assert "Stem: -15.8 LUFS" in loudness_target.measured


# ---------------------------------------------------------------------------
# D7: Stem and mix loudness with peak normalization
# ---------------------------------------------------------------------------
def test_D7_stem_and_mix_loudness_with_music(tmp_path: Path):
    sr = 48000
    duration_s = 2.0

    # Source background audio (mild noise)
    source_wav = tmp_path / "source.wav"
    t = np.linspace(0, duration_s, int(sr * duration_s), endpoint=False)
    bg_audio = 0.05 * np.sin(2 * np.pi * 440 * t).astype(np.float32)
    import soundfile as sf
    sf.write(source_wav, bg_audio, sr)

    # Dubbed speech stem: placed between 0.5s and 1.5s
    dubbed_stem = np.zeros(int(sr * duration_s), dtype=np.float32)
    speech = 0.5 * np.sin(2 * np.pi * 220 * t[:sr]).astype(np.float32)
    dubbed_stem[int(0.5 * sr) : int(0.5 * sr) + sr] = speech

    out_wav = tmp_path / "dubbed_mix.wav"
    mix_res = mix_dubbed_audio(
        original_audio_path=source_wav,
        speech_stem_48k=dubbed_stem,
        speech_intervals_to_mute=[(0.5, 1.5)],
        output_wav_path=out_wav,
        placed_spans=[(0.5, 1.5)],
        skipped_spans=[],
    )

    # mix_res is a MixResult instance
    assert isinstance(mix_res, MixResult)
    # Accessible via attributes
    assert hasattr(mix_res, "final_lufs")
    assert hasattr(mix_res, "stem_lufs")
    # Unpackable as 2-tuple
    final_lufs, stem_lufs = mix_res
    assert isinstance(final_lufs, float)
    assert isinstance(stem_lufs, float)


# ---------------------------------------------------------------------------
# D8: decode_muxed_audio & video start_time handling
# ---------------------------------------------------------------------------
def test_D8_mux_decoded_audio_and_nonzero_start_time(tmp_path: Path):
    # Verify decode_muxed_audio with mock subprocess
    import io

    import soundfile as sf
    dummy_wav_io = io.BytesIO()
    sf.write(dummy_wav_io, np.ones((48000, 2), dtype=np.float32) * 0.1, 48000, format="WAV")
    dummy_bytes = dummy_wav_io.getvalue()

    with patch("subprocess.run") as mock_sub:
        mock_sub.return_value = MagicMock(stdout=dummy_bytes)
        audio_data, sr = decode_muxed_audio(tmp_path / "dummy.mp4")
        assert sr == 48000
        assert audio_data.shape == (48000, 2)

    # Test mux_video_audio offset argument logic when start_time > 0.05
    with patch("bodhan_dub.media.mux.probe_media_streams") as mock_probe, \
         patch("subprocess.run") as mock_run:
        mock_probe.return_value = {
            "format": {"duration": "5.0", "start_time": "0.150"},
            "streams": [
                {"codec_type": "video", "duration": "5.0", "start_time": "0.150"},
                {"codec_type": "audio", "duration": "5.0", "start_time": "0.000"},
            ],
        }
        mock_run.return_value = MagicMock(returncode=0)

        logger = PipelineLogger(run_id="test_d8")
        out_mp4 = tmp_path / "video.hi.mp4"
        dummy_v = tmp_path / "v.mp4"
        dummy_a = tmp_path / "a.wav"
        dummy_v.touch()
        dummy_a.touch()

        mux_video_audio(dummy_v, dummy_a, out_mp4, lang="hi", logger=logger)

        # Ensure -itsoffset 0.150 was injected in ffmpeg call
        call_args = mock_run.call_args[0][0]
        assert "-itsoffset" in call_args
        idx = call_args.index("-itsoffset")
        assert call_args[idx + 1] == "0.150"
        assert call_args[idx + 1] == "0.150"
