from bodhan_dub import timing as tm


def test_ladder_order():
    assert tm.plan_fit(3.0, 3.0, 1.0, 1.0).action == "fit"
    assert tm.plan_fit(3.8, 3.0, 1.0, 1.0).action == "borrow_gap"
    p = tm.plan_fit(4.1, 3.0, 1.0, 1.0)
    assert p.action == "lead" and p.stretch == 1.0 and -0.25 <= p.start_shift < 0
    p = tm.plan_fit(5.0, 3.0, 1.0, 0.2)
    assert p.action == "stretch" and 1.0 < p.stretch <= 1.2
    assert tm.plan_fit(9.0, 3.0, 1.0, 1.0).action == "condense"


def test_stretch_never_exceeds_limit_and_start_stays_in_tolerance():
    for d in [x / 10 for x in range(10, 120)]:
        p = tm.plan_fit(d, 3.0, 0.5, 0.4)
        assert p.stretch <= 1.2 + 1e-9
        assert abs(p.start_shift) <= 0.3


def test_condense_ratio_bounds():
    assert 0.3 <= tm.condense_ratio(20.0, 3.0, 0.5, 0.5) <= 0.95
    assert tm.condense_ratio(3.1, 3.0, 0.5, 0.5) == 0.95


def test_srt_format():
    s = tm.make_srt([(0.0, 1.5, "a"), (3661.25, 3662.0, "b")])
    assert "00:00:00,000 --> 00:00:01,500" in s and "01:01:01,250 --> 01:01:02,000" in s


def test_add_costs_sums_nested_meters():
    from bodhan_dub.pipeline import add_costs
    a = {"transcribe": {"calls": 1, "cost_inr": 0.1}, "total_cost_inr": 0.1}
    b = {"transcribe": {"calls": 2, "cost_inr": 0.2}, "speak": {"calls": 1, "cost_inr": 1.0}, "total_cost_inr": 1.2}
    assert add_costs(a, b) == {"transcribe": {"calls": 3, "cost_inr": 0.3}, "speak": {"calls": 1, "cost_inr": 1.0}, "total_cost_inr": 1.3}
