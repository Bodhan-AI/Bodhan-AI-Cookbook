from bodhan_eval.cache import Cache
from bodhan_eval.cost import BudgetExceeded, CostMeter


def test_cache_roundtrip_and_key_stability(tmp_path):
    c = Cache(tmp_path / "x.sqlite")
    payload = {"b": 2, "a": "हिंदी"}
    assert c.get_json("ns", payload) is None
    c.put_json("ns", payload, {"out": "ok"}, {"rid": "1"})
    assert c.get_json("ns", {"a": "हिंदी", "b": 2}) == (
        {"out": "ok"},
        {"rid": "1"},
    )  # key order-free
    assert c.get_json("other", payload) is None
    assert c.hits == 1 and c.misses == 2
    c.close()
    # persists across instances
    assert Cache(tmp_path / "x.sqlite").get_json("ns", payload)[0] == {"out": "ok"}


def test_cost_reserve_settle_release():
    m = CostMeter(max_inr=1.0)
    r = m.reserve("speak", 0.6)
    try:
        m.reserve("speak", 0.6)
        raise AssertionError("expected BudgetExceeded")
    except BudgetExceeded:
        pass
    m.settle("speak", r, units=1000, inr=0.6)
    assert m.spent == 0.6 and m.reserved == 0
    r2 = m.reserve("translate", 0.3)
    m.release(r2)
    assert m.remaining() == 0.4
    m.record_cached("speak")
    s = m.summary()["services"]["speak"]
    assert s == {"calls": 1, "cached": 1, "units": 1000, "inr": 0.6}
