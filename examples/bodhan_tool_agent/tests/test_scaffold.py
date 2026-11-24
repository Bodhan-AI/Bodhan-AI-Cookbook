"""Basic scaffold verification tests."""

from pathlib import Path

import pytest


def test_packages_importable():
    """Verify both bodhan_mcp and bodhan_agent are importable."""
    import bodhan_agent
    import bodhan_mcp

    assert bodhan_mcp.__version__ == "0.1.0"
    assert bodhan_agent.__version__ == "0.1.0"


def test_fixtures_generate_valid_files(short_wav: Path, small_pdf: Path, sample_image: Path):
    """Verify conftest media fixtures create valid non-empty files in tmp_path."""
    assert short_wav.exists() and short_wav.stat().st_size > 0
    assert small_pdf.exists() and small_pdf.stat().st_size > 0
    assert sample_image.exists() and sample_image.stat().st_size > 0


def test_fake_fixtures(fake_mcp_transport, fake_llm):
    """Verify conftest mock objects initialize cleanly."""
    assert fake_mcp_transport is not None
    assert fake_llm is not None
    res = fake_llm.complete("test prompt")
    assert "steps" in res


def test_http_blocker_raises_on_bodhan_call():
    """Verify that unmocked Bodhan HTTP calls raise RuntimeError per Rule F2."""
    import httpx

    with pytest.raises(RuntimeError, match="Real HTTP call to .* attempted in pytest"):
        client = httpx.Client()
        client.get("https://api.bodhan.ai/v1/health")
