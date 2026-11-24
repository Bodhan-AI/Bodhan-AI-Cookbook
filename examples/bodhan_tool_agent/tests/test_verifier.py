"""Tests for verifier metrics: audio back-transcription, SRT validation, translation coverage, table parity (Slice 12)."""

from pathlib import Path

import httpx
import pytest

from bodhan_agent.verifier import (
    compute_char_similarity,
    verify_audio_back_transcription,
    verify_srt_structure,
    verify_table_parity,
    verify_translation_coverage,
)
from bodhan_mcp.clients.base import BaseBodhanClient
from bodhan_mcp.clients.bodhan import BodhanClient


def test_char_similarity():
    """Verify character similarity metric computation."""
    # Exact match
    assert compute_char_similarity("नमस्ते दुनिया", "नमस्ते दुनिया") == 1.0
    # Minor difference
    sim = compute_char_similarity("नमस्ते भारत", "नमस्ते भरत")
    assert sim >= 0.85
    # Major difference
    assert compute_char_similarity("नमस्ते", "शुभप्रभात दोस्तों") < 0.50


@pytest.mark.asyncio
async def test_verify_audio_back_transcription_pass_and_fail(short_wav: Path):
    """Verify audio back-transcription passes when similarity >= 0.85 and fails when below."""
    # 1. High similarity match -> PASS
    def pass_mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "text": "नमस्ते भारत महान",
                "language": "hi",
                "language_confidence": 0.95,
                "segments": [{"start": 0.0, "end": 1.0, "text": "नमस्ते भारत महान"}],
            },
        )

    t_pass = httpx.MockTransport(pass_mock_handler)
    base_client_pass = BaseBodhanClient(base_url="https://mock.bodhan.ai/v1")
    base_client_pass._client = httpx.AsyncClient(transport=t_pass)
    bodhan_pass = BodhanClient(base_client=base_client_pass)

    res_pass = await verify_audio_back_transcription(
        audio_path=str(short_wav),
        expected_text="नमस्ते भारत महान",
        target_duration_s=1.0,
        client=bodhan_pass,
    )
    assert res_pass.passed is True
    assert res_pass.metric["char_similarity"] == 1.0
    await base_client_pass.close()

    # 2. Low similarity match -> FAIL
    def fail_mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "text": "बिल्कुल अलग पाठ",
                "language": "hi",
                "language_confidence": 0.90,
                "segments": [{"start": 0.0, "end": 1.0, "text": "बिल्कुल अलग पाठ"}],
            },
        )

    t_fail = httpx.MockTransport(fail_mock_handler)
    base_client_fail = BaseBodhanClient(base_url="https://mock.bodhan.ai/v1")
    base_client_fail._client = httpx.AsyncClient(transport=t_fail)
    bodhan_fail = BodhanClient(base_client=base_client_fail)

    res_fail = await verify_audio_back_transcription(
        audio_path=str(short_wav),
        expected_text="नमस्ते भारत महान",
        target_duration_s=1.0,
        client=bodhan_fail,
    )
    assert res_fail.passed is False
    assert res_fail.metric["char_similarity"] < 0.50
    await base_client_fail.close()


def test_verify_translation_coverage():
    """Verify translation segment parity and length ratio constraints."""
    # Good translation
    src = ["Good morning.", "How are you today?"]
    tgt = ["காலை வணக்கம்.", "இன்று நீங்கள் எப்படி இருக்கிறீர்கள்?"]
    res_ok = verify_translation_coverage(src, tgt)
    assert res_ok.passed is True

    # Bad translation: missing segment
    res_mismatch = verify_translation_coverage(src, ["ஒன்று மட்டுமே"])
    assert res_mismatch.passed is False

    # Bad translation: empty segment
    res_empty = verify_translation_coverage(src, ["வணக்கம்", "   "])
    assert res_empty.passed is False


def test_verify_srt_structure():
    """Verify SRT parser, monotonicity, and cue boundary validation."""
    valid_srt = (
        "1\n"
        "00:00:01,000 --> 00:00:03,500\n"
        "First subtitle line\n\n"
        "2\n"
        "00:00:04,000 --> 00:00:07,200\n"
        "Second subtitle line\n"
    )
    res_valid = verify_srt_structure(valid_srt, source_duration_s=10.0)
    assert res_valid.passed is True
    assert res_valid.metric["cues"] == 2

    # Non-monotonic timestamps
    broken_srt = (
        "1\n"
        "00:00:05,000 --> 00:00:07,000\n"
        "First cue\n\n"
        "2\n"
        "00:00:02,000 --> 00:00:04,000\n"
        "Broken back in time cue\n"
    )
    res_broken = verify_srt_structure(broken_srt, source_duration_s=10.0)
    assert res_broken.passed is False


def test_verify_table_parity():
    """Verify table row and column count parity."""
    src_table = (
        "| ID | Name | Age |\n"
        "|---|---|---|\n"
        "| 1 | Alice | 25 |\n"
        "| 2 | Bob | 30 |\n"
    )
    tgt_table_ok = (
        "| आईडी | नाम | आयु |\n"
        "|---|---|---|\n"
        "| 1 | एलिस | 25 |\n"
        "| 2 | बॉब | 30 |\n"
    )
    tgt_table_missing_row = (
        "| आईडी | नाम | आयु |\n"
        "|---|---|---|\n"
        "| 1 | एलिस | 25 |\n"
    )

    assert verify_table_parity(src_table, tgt_table_ok).passed is True
    assert verify_table_parity(src_table, tgt_table_missing_row).passed is False
