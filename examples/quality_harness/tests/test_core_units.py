from pathlib import Path

from bodhan_eval.cache import Cache, canonical_key
from bodhan_eval.cost import BudgetExceeded, CostLedger, translate_cost_inr
from bodhan_eval.text_utils import char_error_rate, word_error_rate


def test_cache_key_stable_and_order_independent():
    k1 = canonical_key("m", "/e", {"a": 1, "b": 2}, "hello")
    k2 = canonical_key("m", "/e", {"b": 2, "a": 1}, "hello")
    k3 = canonical_key("m", "/e", {"a": 1, "b": 2}, "world")
    assert k1 == k2
    assert k1 != k3


def test_cache_roundtrip(tmp_path: Path):
    c = Cache(tmp_path)
    assert c.get_json("k") is None
    c.put_json("k", {"x": 1})
    assert c.get_json("k") == {"x": 1}
    assert c.hits == 1 and c.misses == 1


def test_cost_ledger_cap():
    led = CostLedger(max_inr=1.0)
    led.charge("m", 0.6)
    assert led.can_afford(0.3)
    assert not led.can_afford(0.5)
    try:
        led.charge("m", 0.5)
        raised = False
    except BudgetExceeded:
        raised = True
    assert raised


def test_translate_cost_rate():
    assert abs(translate_cost_inr(10000, 0.20) - 0.20) < 1e-9


def test_cer_wer():
    assert char_error_rate("abcd", "abcd").rate == 0.0
    assert abs(char_error_rate("abcd", "abce").rate - 0.25) < 1e-9
    assert word_error_rate("a b c", "a b").rate > 0
