"""Unit tests for voice extraction and resolution logic."""
import pytest
from bodhan_mcp.tools.tts import resolve_voice


def test_resolve_voice_by_gender():
    assert resolve_voice("female", "gu") == "Nisha"
    assert resolve_voice("male", "gu") == "Kabir"
    assert resolve_voice("female", "hi") == "Priya"
    assert resolve_voice("male", "hi") == "Amit"
    assert resolve_voice("female", "bn") == "Ananya"
    assert resolve_voice("male", "bn") == "Ravi"
    assert resolve_voice("female", "ta") == "Devi"
    assert resolve_voice("female", "te") == "Kavya"


def test_resolve_voice_case_and_variations():
    assert resolve_voice("nisha", "gu") == "Nisha"
    assert resolve_voice("kabir", "gu") == "Kabir"
    assert resolve_voice("female voice", "gu") == "Nisha"
    assert resolve_voice("Nisha (female)", "gu") == "Nisha"
    assert resolve_voice("woman", "hi") == "Priya"
    assert resolve_voice("man", "hi") == "Amit"


def test_resolve_voice_cross_language_and_fallback():
    # If a user mentions Priya (Hindi female) for Gujarati, it maps to female Gujarati voice
    assert resolve_voice("Priya", "gu") == "Nisha"
    # If completely unrecognized, it defaults to valid voice for that language
    assert resolve_voice("unknown_robot_voice_99", "gu") in ["Kabir", "Nisha"]
