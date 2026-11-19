import pytest
from bodhan_mcp.server import mcp_server, get_voice_library
from bodhan_mcp.pricing import (
    calculate_transcribe_cost,
    calculate_translate_cost,
    calculate_speak_cost,
    calculate_ocr_cost,
)


def test_tool_registration():
    tool_names = list(mcp_server._tool_manager._tools.keys())
    expected_tools = [
        "transcribe_audio",
        "translate_text",
        "synthesize_speech",
        "extract_text_ocr",
        "inspect_media",
        "detect_language",
    ]
    for exp in expected_tools:
        assert exp in tool_names, f"Expected tool {exp} not registered"


def test_voice_library_resource():
    lib_json = get_voice_library()
    assert "Bodhan Indic-Speak" in lib_json
    assert "voices" in lib_json


def test_pricing_calculations():
    # ₹0.10 per audio min
    assert calculate_transcribe_cost(60.0) == 0.10
    assert calculate_transcribe_cost(30.0) == 0.05

    # ₹0.20 per 10k tokens
    assert calculate_translate_cost(10000) == 0.20
    assert calculate_translate_cost(5000) == 0.10

    # ₹6 per 10k chars
    assert calculate_speak_cost(10000) == 6.00
    assert calculate_speak_cost(1000) == 0.60

    # ₹0.20 per image
    assert calculate_ocr_cost(1) == 0.20
    assert calculate_ocr_cost(5) == 1.00
