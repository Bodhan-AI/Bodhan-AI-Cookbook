"""Unit tests for 3D audio QA metrics: Coverage, Repetition detection, and CER/WER."""

from bodhan_lesson.verifier import (
    calculate_cer_and_wer,
    compute_coverage_score,
    detect_speech_repetitions,
    levenshtein_distance,
)


def test_levenshtein_distance():
    assert levenshtein_distance("kitten", "sitting") == 3
    assert levenshtein_distance(["hello", "world"], ["hello", "there", "world"]) == 1


def test_calculate_cer_and_wer():
    ref = "यह एक समकोण त्रिभुज है"
    hyp = "यह एक समकोण त्रिभुज है"
    cer, wer = calculate_cer_and_wer(ref, hyp)
    assert cer == 0.0
    assert wer == 0.0

    hyp_diff = "यह एक न्यूनकोण त्रिभुज है"
    cer_diff, wer_diff = calculate_cer_and_wer(ref, hyp_diff)
    assert 0.0 < cer_diff < 0.5
    assert 0.0 < wer_diff < 0.5


def test_compute_coverage_score():
    ref = "The sum of adjacent angles on a straight line is 180 degrees"
    hyp = "The sum of adjacent angles on a straight line is 180"
    cov = compute_coverage_score(ref, hyp)
    assert cov > 0.85


def test_detect_speech_repetitions():
    normal_text = "angle AOB plus angle BOC equals 180 degrees"
    has_rep, _ = detect_speech_repetitions(normal_text)
    assert not has_rep

    looping_text = "angle AOB angle BOC angle AOB angle BOC angle AOB angle BOC"
    has_rep_loop, phrases = detect_speech_repetitions(looping_text)
    assert has_rep_loop


def test_verifier_circuit_breaker_handling(monkeypatch):
    from bodhan_lesson.verifier import AudioVerifier

    verifier = AudioVerifier()
    # Mock client to simulate circuit breaker skipping
    monkeypatch.setattr(
        verifier.client,
        "transcribe_audio",
        lambda audio_bytes, language: {
            "text": "",
            "duration_seconds": 0.0,
            "status": "skipped_due_to_service_unavailability",
            "error": "transcribe_circuit_broken",
        },
    )

    res = verifier.verify_section_audio(
        audio_bytes=b"dummy",
        reference_script="Hello test",
        section_id="01",
        language="hi",
    )
    assert res.passed is False
    assert res.qa_status == "skipped_due_to_service_unavailability"
    assert "skipped" in res.failure_notes.lower()


def test_client_circuit_breaker_on_502(monkeypatch):
    import time

    import bodhan_lesson.client
    import httpx
    from bodhan_lesson.client import BodhanClient

    monkeypatch.setattr(bodhan_lesson.client, "is_mock_mode", lambda: False)
    monkeypatch.setattr(time, "sleep", lambda s: None)

    client = BodhanClient()
    client._transcribe_circuit_broken = False
    client._transcribe_consecutive_failures = 0

    def mock_post(*args, **kwargs):
        req = httpx.Request("POST", "http://test")
        resp = httpx.Response(502, request=req)
        raise httpx.HTTPStatusError("502 Bad Gateway", request=req, response=resp)

    monkeypatch.setattr(httpx.Client, "post", mock_post)

    result = client.transcribe_audio(b"audio", language="hi")
    assert result["status"] == "skipped_due_to_service_unavailability"
    assert client._transcribe_circuit_broken is True
