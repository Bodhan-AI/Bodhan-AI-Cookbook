"""
Tests for security and architecture policy enforcement.
Ensures zero forbidden dependencies, strict host allowlist, and English-only LLM boundaries.
"""

from pathlib import Path

import httpx
import pytest

from bodhan_dub.policy import (
    FORBIDDEN_DEPENDENCIES,
    HostAllowlistGuard,
    PolicyViolation,
    contains_indic_or_arabic,
    validate_llm_text,
)


def test_no_forbidden_dependencies_in_pyproject():
    """Verify pyproject.toml does not declare any forbidden speech or translation libraries."""
    pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    assert pyproject_path.exists(), "pyproject.toml must exist"
    content = pyproject_path.read_text(encoding="utf-8").lower()

    for dep in FORBIDDEN_DEPENDENCIES:
        assert dep not in content, f"Forbidden dependency '{dep}' found in pyproject.toml!"


def test_indic_unicode_detector():
    """Verify Indic and Arabic Unicode character detection."""
    # Pure English / Latin / numbers
    assert not contains_indic_or_arabic("Hello world 123! This is English text.")
    assert not contains_indic_or_arabic("Einstein's equation E = mc^2")

    # Hindi (Devanagari)
    assert contains_indic_or_arabic("नमस्ते")
    assert contains_indic_or_arabic("Hello नमस्ते")

    # Bengali
    assert contains_indic_or_arabic("বোধন এআই")

    # Tamil
    assert contains_indic_or_arabic("வணக்கம்")

    # Urdu / Arabic
    assert contains_indic_or_arabic("کلاس")

    # Santali (Ol Chiki)
    assert contains_indic_or_arabic("ᱥᱟᱱᱛᱟᱲᱤ")


def test_validate_llm_text_guard():
    """Verify that validate_llm_text passes clean English and raises PolicyViolation on Indic."""
    clean = "Please summarize this sentence into 15 words."
    assert validate_llm_text(clean) == clean

    with pytest.raises(PolicyViolation, match="contains non-English"):
        validate_llm_text("Translate this: कक्षा सुबह नौ बजे शुरू होती है।")


def test_host_allowlist_enforcement():
    """Verify HostAllowlistGuard blocks unauthorized network endpoints."""
    guard = HostAllowlistGuard(["api.bodhan.ai", "api.openai.com"], allow_test_hosts=True)

    # Allowed hosts
    guard.check_url("https://api.bodhan.ai/v1/audio/speech")
    guard.check_url("https://api.openai.com/v1/chat/completions")
    guard.check_url("http://localhost:8000/test")
    guard.check_url("http://127.0.0.1:5000/v1")

    # Disallowed hosts
    with pytest.raises(PolicyViolation, match="forbidden host 'api.elevenlabs.io'"):
        guard.check_url("https://api.elevenlabs.io/v1/text-to-speech")

    with pytest.raises(PolicyViolation, match="forbidden host 'translate.google.com'"):
        guard.check_url("https://translate.google.com/api")

    # B19: In production mode (allow_test_hosts=False), localhost is blocked
    prod_guard = HostAllowlistGuard(["api.bodhan.ai", "api.openai.com"], allow_test_hosts=False)
    with pytest.raises(PolicyViolation, match="forbidden host 'localhost'"):
        prod_guard.check_url("http://localhost:8000/test")


@pytest.mark.asyncio
async def test_httpx_event_hook_blocks_unauthorized():
    """Verify httpx client with event hook raises PolicyViolation on forbidden request."""
    guard = HostAllowlistGuard(["api.bodhan.ai"])
    client = httpx.AsyncClient(
        event_hooks={"request": [guard.async_request_hook]},
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json={})),
    )

    # Allowed host via mock
    resp = await client.get("https://api.bodhan.ai/v1/health")
    assert resp.status_code == 200

    # Forbidden host
    with pytest.raises(PolicyViolation):
        await client.get("https://forbidden-thirdparty.com/v1")
