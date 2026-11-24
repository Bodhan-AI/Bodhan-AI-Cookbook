"""Unit tests for the artifact verification engine."""

from __future__ import annotations

from pathlib import Path
from bodhan_agent.models import VerificationStatus
from bodhan_agent.verifier import Verifier


def test_verify_file_artifact(tmp_path: Path):
    valid_file = tmp_path / "summary.md"
    valid_file.write_text("# Summary\n\nThis is a non-empty artifact file with valid contents.")

    res = Verifier.verify_file_artifact(valid_file)
    assert res.status == VerificationStatus.PASSED

    missing = tmp_path / "missing.md"
    res_missing = Verifier.verify_file_artifact(missing)
    assert res_missing.status == VerificationStatus.FAILED


def test_verify_translation_completeness():
    src = "First paragraph.\n\nSecond paragraph."
    target = "पहला पैराग्राफ।\n\nदूसरा पैराग्राफ।"

    res = Verifier.verify_translation_completeness(src, target, target_lang="hi")
    assert res.status == VerificationStatus.PASSED

    # Identical translation when target is non-English should fail
    res_fail = Verifier.verify_translation_completeness(src, src, target_lang="hi")
    assert res_fail.status == VerificationStatus.FAILED


def test_verify_srt_subtitles():
    valid_srt = "1\n00:00:01,000 --> 00:00:04,000\nHello world\n\n2\n00:00:05,000 --> 00:00:08,000\nSecond cue\n"
    res = Verifier.verify_srt_subtitles(valid_srt)
    assert res.status == VerificationStatus.PASSED

    res_invalid = Verifier.verify_srt_subtitles("Just random text without timestamps")
    assert res_invalid.status == VerificationStatus.FAILED


def test_verify_ocr_pages():
    res_all = Verifier.verify_ocr_pages(total_pages=5, successful_pages=5, failed_pages=0)
    assert res_all.status == VerificationStatus.PASSED

    res_partial = Verifier.verify_ocr_pages(total_pages=5, successful_pages=3, failed_pages=2)
    assert res_partial.status == VerificationStatus.PARTIAL

    res_fail = Verifier.verify_ocr_pages(total_pages=5, successful_pages=0, failed_pages=5)
    assert res_fail.status == VerificationStatus.FAILED
