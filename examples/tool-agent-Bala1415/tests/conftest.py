"""Shared test fixtures for the Bodhan test suite."""

import os
import pytest
from pathlib import Path


@pytest.fixture
def test_data_dir() -> Path:
    """Path to the test_data directory."""
    return Path(__file__).parent.parent / "test_data"


@pytest.fixture
def tmp_output_dir(tmp_path: Path) -> Path:
    """Temporary output directory for test artifacts."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "artifacts").mkdir()
    return out


@pytest.fixture
def mock_env(monkeypatch):
    """Set mock environment variables for testing without real API keys."""
    monkeypatch.setenv("BODHAN_TRANSCRIBE_KEY", "sk-test-transcribe")
    monkeypatch.setenv("BODHAN_TRANSLATE_KEY", "sk-test-translate")
    monkeypatch.setenv("BODHAN_SPEAK_KEY", "sk-test-speak")
    monkeypatch.setenv("BODHAN_OCR_KEY", "sk-test-ocr")
    monkeypatch.setenv("BODHAN_API_BASE_URL", "https://api.bodhan.ai/v1")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_API_KEY", "sk-test-llm")


# Integration Test Fixtures


import pytest
from _pytest.monkeypatch import MonkeyPatch


@pytest.fixture
def mock_mcp_responses():
    """Mock responses from Bodhan API tools for testing."""
    return {
        "transcribe_audio": {
            "text": "नमस्ते यह एक हिंदी आवाज़ है। मैं आपको कहानी सुना रहा हूँ।",
            "segments": [{"start_seconds": 0.0, "end_seconds": 5.0, "text": "नमस्ते यह हिंदी है"}, {"start_seconds": 5.0, "end_seconds": 10.0, "text": "यह कहानी सुना रहा हूँ"}],
            "language": "hi",
            "duration_seconds": 10.0,
            "num_chunks": 1,
        },
        "detect_language": {
            "language": "hi",
            "confidence": 0.95,
        },
        "translate_text": {
            "translated_text": "Hello, this is a Hindi voice. I am telling you a story.",
            "source_language": "hi",
            "target_language": "en",
            "num_chunks": 1,
            "char_count": 45,
        },
        "synthesize_speech": {
            "output_path": "artifacts/speech_output.wav",
            "audio_duration_seconds": 3.0,
            "text_length": 60,
        },
        "extract_text_ocr": {
            "text": "नमस्ते दुनिया। यह एक हिंदी दस्तावेज़ है।",
            "avg_confidence": 0.92,
            "num_pages": 1,
        },
    }


@pytest.fixture
def mock_executor(monkeypatch):
    """Mock executor that simulates MCP tool calls."""
    
    def make_execute(tool_name: str, arguments: dict) -> dict:
        mock_responses = mock_mcp_responses
        
        result = {}
        
        if tool_name == "inspect_media":
            result = {
                "file_path": arguments["file_path"],
                "format": arguments["file_path"].split(".")[-1],
                "size_bytes": 1024 * 100,
                "duration_seconds": arguments.get("duration_seconds", 10),
            }
        
        elif tool_name == "detect_language":
            result = {**mock_responses["detect_language"], **arguments}
        
        elif tool_name == "transcribe_audio":
            result = mock_responses["transcribe_audio"]
        
        elif tool_name == "translate_text":
            result = mock_responses["translate_text"]
        
        elif tool_name == "synthesize_speech":
            result = mock_responses["synthesize_speech"]
        
        elif tool_name == "extract_text_ocr":
            result = mock_responses["extract_text_ocr"]
        
        elif tool_name == "task_complete":
            result = {**arguments}
        
        else:
            result = {"error": True, "message": f"Unknown tool: {tool_name}"}
        
        return result
    
    monkeypatch.setattr("src.bodhan_agent.executor.Executor.execute", make_execute)
    
    return make_execute


@pytest.fixture
def mock_verifier():
    """Mock verifier with predefined verification results."""
    
    def make_verify(audio_path, expected_language=None):
        return {
            "check": "audio_verification",
            "file": audio_path,
            "passed": True,
            "details": "Back-transcription successful",
            "transcription": "नमस्ते हिंदी है। कहानी सुना रहा हूँ।",
        }
    
    return make_verify


@pytest.fixture
def mock_planner_response(plan):
    """Mock planner response for deterministic test planning."""
    pass

