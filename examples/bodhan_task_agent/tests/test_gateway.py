from bodhan_shared.gateway import Budget, Gateway
from bodhan_shared.store import Workspace


def test_exact_input_cache_and_budget(fake_gateway, tmp_path):
    gw = Gateway(Workspace(tmp_path), Budget(0.1))
    a, cached_a = gw.translate("Hello 20", "hi")
    b, cached_b = gw.translate("Hello 20", "hi")
    assert a == b == "Hello 20"
    assert not cached_a and cached_b
    assert len(fake_gateway) == 1
    assert gw.budget.spent > 0
    assert gw.budget.events[1]["estimated_inr"] == 0


def test_budget_can_refuse_call_before_any_network(fake_gateway, tmp_path):
    from bodhan_shared.errors import TaskError

    gw = Gateway(Workspace(tmp_path), Budget(.000001))
    try:
        gw.speak("Hello", "Kavya", "hi")
    except TaskError as exc:
        assert exc.code == "BUDGET_EXCEEDED"
    else:
        assert False, "expected budget rejection"
    assert not fake_gateway
