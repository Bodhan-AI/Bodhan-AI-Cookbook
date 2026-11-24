from pathlib import Path

import pytest

from bodhan_agent.errors import ToolError
from bodhan_agent.settings import Settings
from bodhan_agent.tools import ToolExecutor
from bodhan_agent.tools import _format_srt
from bodhan_agent.__main__ import _contains_indic


def test_speech_output_must_be_inside_artifact_directory(tmp_path: Path):
    settings = Settings("t", "tr", "s", "o", "", "", "")
    executor = ToolExecutor(settings, tmp_path / "artifacts")
    with pytest.raises(ToolError, match="inside this run's artifacts"):
        executor._safe_artifact_path(str(tmp_path / "outside.wav"))


def test_invalid_language_fails_before_network_call():
    from bodhan_agent.bodhan import translate_text

    settings = Settings("t", "tr", "s", "o", "", "", "")
    with pytest.raises(ToolError, match="Unsupported language"):
        translate_text("hello", "xx", settings)


def test_srt_formatter_uses_subrip_milliseconds():
    content = _format_srt([{"start": 1.25, "end": 3.5, "text": "A sentence."}])
    assert "00:00:01,250 --> 00:00:03,500" in content
    assert content.endswith("A sentence.\n")


def test_srt_formatter_rejects_overlapping_segments():
    with pytest.raises(ToolError, match="invalid times"):
        _format_srt([
            {"start": 0, "end": 2, "text": "First."},
            {"start": 1, "end": 3, "text": "Second."},
        ])


def test_indic_script_guard_does_not_match_latin_text():
    assert not _contains_indic("Hello, world.")
    assert _contains_indic("नमस्ते")


def test_final_response_can_quote_exact_bodhan_output():
    from bodhan_agent.__main__ import _contains_unverified_indic

    verified = "சமுதாய மருத்துவமனை அறிவிப்பு"
    assert not _contains_unverified_indic(f"Tamil translation: {verified}", [verified])
    assert _contains_unverified_indic(f"Tamil translation: {verified} நண்பர்களே", [verified])
