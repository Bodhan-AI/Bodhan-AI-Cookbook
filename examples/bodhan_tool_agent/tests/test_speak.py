"""Tests for synthesize_speech tool, voice validation, and AI labeling (Slice 7)."""

from pathlib import Path

import httpx
import pytest

from bodhan_mcp import config, errors
from bodhan_mcp.clients.base import BaseBodhanClient
from bodhan_mcp.clients.bodhan import BodhanClient
from bodhan_mcp.schemas import SpeakOut
from bodhan_mcp.tools.speak import synthesize_speech


@pytest.mark.asyncio
async def test_speak_unsupported_language_and_english_exclusion():
    """Verify speech synthesis rejects unsupported languages and excludes English (Rule F6)."""
    # English TTS is explicitly forbidden
    err_en = await synthesize_speech(text="Hello world", language="en", voice_id="female_1")
    assert isinstance(err_en, errors.ToolErrorPayload)
    assert err_en.code == errors.UNSUPPORTED_LANGUAGE

    # French is unsupported
    err_fr = await synthesize_speech(text="Bonjour", language="fr", voice_id="female_1")
    assert isinstance(err_fr, errors.ToolErrorPayload)
    assert err_fr.code == errors.UNSUPPORTED_LANGUAGE


@pytest.mark.asyncio
async def test_speak_invalid_voice():
    """Verify speech synthesis rejects voices not in the voice library."""
    err = await synthesize_speech(text="नमस्ते", language="hi", voice_id="non_existent_voice")
    assert isinstance(err, errors.ToolErrorPayload)
    assert err.code == errors.INVALID_ARGUMENT
    assert "Invalid" in err.message or "invalid" in err.message


@pytest.mark.asyncio
async def test_speak_character_cap_exceeded():
    """Verify synthesize_speech rejects input > 5000 chars with INPUT_TOO_LONG and cost estimate."""
    long_text = "क" * (config.SPEAK_MAX_CHARS + 100)
    err = await synthesize_speech(text=long_text, language="hi", voice_id="hi_female_1")
    assert isinstance(err, errors.ToolErrorPayload)
    assert err.code == errors.INPUT_TOO_LONG
    assert "estimated_cost_inr" in err.details
    assert err.details["estimated_cost_inr"] > 0.0


@pytest.mark.asyncio
async def test_speak_success_and_ai_generated_sidecar(tmp_path: Path):
    """Verify successful speech synthesis stamps AI sidecar and metadata."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok", "audio": "base64_payload"})

    transport = httpx.MockTransport(mock_handler)
    base_client = BaseBodhanClient(base_url="https://mock.bodhan.ai/v1")
    base_client._client = httpx.AsyncClient(transport=transport)
    bodhan = BodhanClient(base_client=base_client)

    out = await synthesize_speech(
        text="नमस्ते दुनिया",
        language="hi",
        voice_id="hi_female_1",
        out_dir=tmp_path / "tts_out",
        client=bodhan,
    )
    assert isinstance(out, SpeakOut)
    assert out.ai_generated is True
    assert out.chars == len("नमस्ते दुनिया")
    assert Path(out.path).exists()

    # Check .ai.txt sidecar
    sidecar_path = Path(out.path).with_suffix(".ai.txt")
    assert sidecar_path.exists()
    sidecar_text = sidecar_path.read_text()
    assert "ai_generated=True" in sidecar_text
    assert "model=bodhan-speak" in sidecar_text
    await base_client.close()
