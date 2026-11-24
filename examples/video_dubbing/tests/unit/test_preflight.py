"""
Unit tests for preflight checks and cost estimation.
"""

import pytest

from bodhan_dub.config import Settings
from bodhan_dub.preflight import PreflightError, estimate_pipeline_cost, validate_duration


def test_validate_duration_bounds():
    # Sane duration (2 minutes)
    ok, warn = validate_duration(120.0)
    assert ok is True
    assert warn is None

    # Warning for slightly short (< 60s)
    ok, warn = validate_duration(45.0)
    assert ok is True
    assert "under the 1-minute recommendation" in warn

    # Warning for slightly long (> 900s)
    ok, warn = validate_duration(1000.0)
    assert ok is True
    assert "exceeds the 15-minute recommendation" in warn

    # Refusal for absurdly short
    with pytest.raises(PreflightError, match="absurdly short"):
        validate_duration(5.0)

    # Refusal for absurdly long (> 30 min)
    with pytest.raises(PreflightError, match="absurdly long"):
        validate_duration(2000.0)


def test_estimate_pipeline_cost():
    settings = Settings(
        cost_asr_per_minute=0.10,
        cost_translate_per_10k_tokens=0.20,
        cost_tts_per_10k_chars=6.00,
    )
    # 5 minutes, 3 languages
    estimates = estimate_pipeline_cost(300.0, 3, settings)
    assert estimates["duration_minutes"] == 5.0
    assert estimates["languages_count"] == 3
    assert estimates["total_estimate_inr"] > 0.0
    # Cost should be roughly ₹5-12 for 5 min x 3 languages
    assert 5.0 <= estimates["total_estimate_inr"] <= 15.0
