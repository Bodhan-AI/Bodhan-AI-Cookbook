from bodhan_eval.config import COSTS


def test_cost_constants():
    assert COSTS == {
        "translate": 0.20 / 10_000,
        "speak": 6.00 / 10_000,
        "transcribe": 0.10 / 60,
    }
