"""Price arithmetic, the per-call budget and the content-addressed cache."""
from __future__ import annotations

import pytest

from bodhan_mcp.core import cache, costs
from bodhan_mcp.core.errors import BodhanError


def test_asr_cost_rounds_up_to_a_paisa():
    assert costs.asr_cost(60) == 0.10
    assert costs.asr_cost(61) == 0.11
    assert costs.asr_cost(0.5) == 0.01
    assert costs.asr_cost(0) == 0


def test_tts_cost_is_exact():
    assert costs.tts_cost(10_000) == 6.0
    assert costs.tts_cost(1_000) == pytest.approx(0.6)
    assert costs.tts_cost(0) == 0


def test_mt_cost_from_tokens_and_estimate():
    assert costs.mt_cost(10_000) == pytest.approx(0.20)
    assert costs.mt_cost(5_000) == pytest.approx(0.10)
    assert costs.mt_estimate(10_000) == 0.24  # 1.2 tokens per source char, rounded up
    assert costs.mt_estimate(1) == 0.01


def test_ocr_cost_per_page():
    assert costs.ocr_cost(8) == pytest.approx(1.6)


def test_reserve_refuses_before_spending_and_names_rupees():
    b = costs.CallBudget(1.0)
    b.reserve(0.9, "first")
    b.add(0.9)
    with pytest.raises(BodhanError) as exc:
        b.reserve(0.2, "second")
    assert exc.value.code == "BUDGET_EXCEEDED"
    assert "Rs 0.20" in exc.value.message and "Rs 0.10" in exc.value.message
    assert b.spent == 0.9


def test_uncapped_budget_never_refuses():
    costs.CallBudget(None).reserve(1e9, "anything")


def test_digest_is_stable_and_sensitive_to_every_part():
    base = cache.digest("mt", "ta", "native", "hello")
    assert base == cache.digest("mt", "ta", "native", "hello")
    for variant in (("asr", "ta", "native", "hello"), ("mt", "hi", "native", "hello"),
                    ("mt", "ta", "roman", "hello"), ("mt", "ta", "native", "hello!")):
        assert cache.digest(*variant) != base
    assert cache.digest("ab", "c") != cache.digest("a", "bc")
    assert cache.digest(b"x") == cache.digest(b"x") != cache.digest(b"y")


def test_json_and_bytes_round_trip():
    key = cache.digest("k")
    assert cache.get_json(key) is None and cache.get_bytes(key) is None
    cache.put_json(key, {"text": "héllo", "n": 2})
    assert cache.get_json(key) == {"text": "héllo", "n": 2}
    cache.put_bytes(key, b"\x00\x01")
    assert cache.get_bytes(key) == b"\x00\x01"


def test_corrupt_json_entry_is_a_miss():
    key = cache.digest("bad")
    cache.put_json(key, {})
    cache._path(key, "json").write_text("{not json")
    assert cache.get_json(key) is None
