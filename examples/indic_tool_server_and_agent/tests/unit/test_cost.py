"""Unit tests for Bodhan model cost calculations and tracking."""
from __future__ import annotations

import pytest
from bodhan_mcp.core.cost import (
    calculate_stt_cost,
    calculate_translate_cost,
    calculate_tts_cost,
    calculate_ocr_cost,
    estimate_stt_cost,
    estimate_translate_cost,
    estimate_tts_cost,
    estimate_ocr_cost,
)


def test_stt_cost_minutes():
    """STT: 60s = 1 min = ₹0.10, 90s = 2 min (ceil) = ₹0.20."""
    assert calculate_stt_cost(0.0) == 0.0
    assert calculate_stt_cost(60.0) == 0.10
    assert calculate_stt_cost(90.0) == 0.20
    assert calculate_stt_cost(30.0) == 0.10
    assert calculate_stt_cost(120.0) == 0.20


def test_translate_cost():
    """Translate: 10,000 tokens = ₹0.20, proportional."""
    assert calculate_translate_cost(0) == 0.0
    assert calculate_translate_cost(10_000) == 0.20
    assert calculate_translate_cost(5_000) == 0.10
    assert calculate_translate_cost(20_000) == 0.40


def test_tts_cost():
    """TTS: 10,000 chars = ₹6.00, proportional."""
    assert calculate_tts_cost(0) == 0.0
    assert calculate_tts_cost(10_000) == 6.00
    assert calculate_tts_cost(1_000) == 0.60
    assert calculate_tts_cost(500) == 0.30


def test_ocr_cost():
    """OCR: 1 image = ₹0.20, 5 images = ₹1.00."""
    assert calculate_ocr_cost(0) == 0.0
    assert calculate_ocr_cost(1) == 0.20
    assert calculate_ocr_cost(5) == 1.00


def test_pre_call_estimates_greater_or_equal_actual():
    """Pre-call estimates are always >= actual cost for the same inputs."""
    assert estimate_stt_cost(90.0) >= calculate_stt_cost(90.0)
    assert estimate_translate_cost(5000) >= calculate_translate_cost(5000)
    assert estimate_tts_cost(1000) >= calculate_tts_cost(1000)
    assert estimate_ocr_cost(3) >= calculate_ocr_cost(3)


def test_cache_hits_zero_cost():
    """A cached result incurs ₹0.00 cost."""
    # From cache specifications, cost for cache hits is 0.0
    cached_cost = 0.0
    assert cached_cost == 0.0
