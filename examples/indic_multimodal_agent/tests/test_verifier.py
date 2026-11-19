"""Unit tests for the artifact verification suite."""

from bodhan_agent.verifier import (
    check_numbers_preserved,
    check_script_matches,
    check_unicode_normalization,
    check_repetition_or_omission,
    check_srt_validity,
    verify_subtitles,
)
from bodhan_mcp.types import SubtitleSegment


def test_check_numbers_preserved():
    # Digits preserved
    assert check_numbers_preserved("मीटिंग 4 बजे है", "The meeting is at 4 o'clock") is True
    # Digits missing
    assert check_numbers_preserved("रोल नंबर 101 और 102", "Roll numbers without digits") is False
    # No digits in source
    assert check_numbers_preserved("सुप्रभात", "Good morning") is True


def test_check_script_matches():
    # English target should have Latin script
    assert check_script_matches("This is English text.", "en") is True
    # Hindi target should have Devanagari script
    assert check_script_matches("यह हिंदी पाठ है।", "hi") is True
    # Tamil target should have Tamil script
    assert check_script_matches("இது தமிழ் உரை.", "ta") is True


def test_check_unicode_normalization():
    text = "नमस्ते दुनिया"
    assert check_unicode_normalization(text) is True


def test_check_repetition_or_omission():
    normal_text = "The quick brown fox jumps over the lazy dog."
    assert check_repetition_or_omission(normal_text) is True

    # Hallucinatory loop repeating 3-grams
    repetitive_text = "hello world again hello world again hello world again hello world again"
    assert check_repetition_or_omission(repetitive_text) is False


def test_check_srt_validity():
    # Clean cues
    good_cues = [
        SubtitleSegment(index=1, start_time="00:00:00,000", end_time="00:00:05,000", start_seconds=0.0, end_seconds=5.0, text="First subtitle cue\nwith two lines"),
        SubtitleSegment(index=2, start_time="00:00:05,000", end_time="00:00:10,000", start_seconds=5.0, end_seconds=10.0, text="Second subtitle cue"),
    ]
    ok, details = check_srt_validity(good_cues, audio_duration_s=10.0)
    assert ok is True

    # Overlap failure
    bad_overlap = [
        SubtitleSegment(index=1, start_time="00:00:00,000", end_time="00:00:06,000", start_seconds=0.0, end_seconds=6.0, text="First cue"),
        SubtitleSegment(index=2, start_time="00:00:05,000", end_time="00:00:10,000", start_seconds=5.0, end_seconds=10.0, text="Second cue"),
    ]
    ok, details = check_srt_validity(bad_overlap, audio_duration_s=10.0)
    assert ok is False
    assert "overlaps" in details

    # Exceeds audio duration
    bad_end = [
        SubtitleSegment(index=1, start_time="00:00:00,000", end_time="00:00:15,000", start_seconds=0.0, end_seconds=15.0, text="Too long"),
    ]
    ok, details = check_srt_validity(bad_end, audio_duration_s=10.0)
    assert ok is False
    assert "exceeds audio duration" in details

    # Line length exceeds limit
    bad_length = [
        SubtitleSegment(index=1, start_time="00:00:00,000", end_time="00:00:05,000", start_seconds=0.0, end_seconds=5.0, text="This line is extraordinarily long and definitely exceeds the forty-two characters limit by far"),
    ]
    ok, details = check_srt_validity(bad_length, audio_duration_s=5.0)
    assert ok is False
    assert "exceeds 42 limit" in details


def test_verify_subtitles_suite():
    source_segs = [
        SubtitleSegment(index=1, start_time="00:00:00,000", end_time="00:00:10,000", start_seconds=0.0, end_seconds=10.0, text="आज तारीख 15 है। मीटिंग शुरू हो गई है।"),
    ]
    target_cues = [
        SubtitleSegment(index=1, start_time="00:00:00,000", end_time="00:00:05,000", start_seconds=0.0, end_seconds=5.0, text="Today's date is 15."),
        SubtitleSegment(index=2, start_time="00:00:05,000", end_time="00:00:10,000", start_seconds=5.0, end_seconds=10.0, text="The meeting has started."),
    ]

    results = verify_subtitles(
        target_segments=target_cues,
        source_segments=source_segs,
        target_lang="en",
        audio_duration_s=10.0,
        artifact_name="artifacts/subtitles_en.srt",
    )
    assert len(results) == 6
    assert all(r.passed for r in results), "All subtitle verifications should pass for clean inputs"
    assert all(r.artifact_name == "artifacts/subtitles_en.srt" for r in results)


def test_verify_audio_summary_coverage_weak():
    from bodhan_agent.verifier import verify_audio_summary
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".mp3") as tmp_audio:
        tmp_audio.write(b"0" * 500)
        tmp_audio.flush()

        # Case 1: Low coverage (< 70%) should be flagged as WEAK
        results_low = verify_audio_summary(
            ocr_pages=1,
            summary_text="one two three four five six seven eight nine ten",
            target_audio_path=tmp_audio.name,
            back_transcription_text="one two",  # 2/10 = 20% coverage
            target_lang="en",
        )
        cov_res = [r for r in results_low if r.check_type == "speech_coverage_verified"][0]
        assert cov_res.status == "WEAK"
        assert cov_res.passed is False
        assert "< 70% threshold" in cov_res.details

        # Case 2: High coverage (>= 70%) should PASS
        results_high = verify_audio_summary(
            ocr_pages=1,
            summary_text="one two three four five",
            target_audio_path=tmp_audio.name,
            back_transcription_text="one two three four",  # 4/5 = 80% coverage
            target_lang="en",
        )
        cov_res_high = [r for r in results_high if r.check_type == "speech_coverage_verified"][0]
        assert cov_res_high.status == "PASS"
        assert cov_res_high.passed is True


def test_verify_audio_summary_without_ocr():
    """Verify audio verification succeeds on audio-only summary (ocr_pages=0)."""
    from bodhan_agent.verifier import verify_audio_summary
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".mp3") as tmp_audio:
        tmp_audio.write(b"0" * 500)
        tmp_audio.flush()

        results = verify_audio_summary(
            ocr_pages=0,
            summary_text="आजची सभा तीन वाजता सुरू होईल आणि पाच महत्त्वाचे विषय आहेत",
            target_audio_path=tmp_audio.name,
            back_transcription_text="आजची सभा तीन वाजता सुरू होईल आणि पाच महत्त्वाचे विषय आहेत",
            target_lang="mr",
        )

        checks = {r.check_type: r for r in results}
        assert "document_pages_processed" not in checks
        assert checks["summary_content_valid"].passed is True
        assert checks["target_script_match"].passed is True
        assert checks["audio_file_generated"].passed is True
        assert checks["speech_coverage_verified"].passed is True



