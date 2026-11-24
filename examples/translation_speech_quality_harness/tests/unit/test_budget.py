"""Reserve-before-spend budget (TESTING.md §1)."""

from __future__ import annotations

import pytest

from bodhan_eval.budget import Budget, BudgetExceeded


def test_refuses_before_crossing_the_cap() -> None:
    b = Budget(1.0)
    b.reserve("translate", 0.5)
    b.settle("translate", 0.5, 0.5)
    with pytest.raises(BudgetExceeded):
        b.reserve("speak", 0.6)
    assert b.refused == {"speak": 1}
    assert b.total_spent == pytest.approx(0.5)


def test_spending_exactly_up_to_the_cap_is_allowed() -> None:
    b = Budget(1.0)
    b.reserve("translate", 1.0)
    b.settle("translate", 1.0, 1.0)
    assert b.remaining() == pytest.approx(0.0)


def test_outstanding_reservations_count_against_the_cap() -> None:
    b = Budget(1.0)
    b.reserve("speak", 0.7)  # in flight, not yet settled
    with pytest.raises(BudgetExceeded):
        b.reserve("speak", 0.4)


def test_reservation_released_on_failure() -> None:
    b = Budget(1.0)
    b.reserve("speak", 0.9)
    b.release(0.9)  # the call failed: nothing paid
    assert b.total_spent == 0.0
    assert b.remaining() == pytest.approx(1.0)
    b.reserve("speak", 0.9)  # the money is available again


def test_settle_moves_reservation_to_actual_spend() -> None:
    b = Budget(10.0)
    b.reserve("translate", 0.5)
    b.settle("translate", 0.5, 0.2)  # actual cheaper than the estimate
    assert b.reserved == 0.0
    assert b.spent["translate"] == pytest.approx(0.2)
    assert b.used["translate"] == pytest.approx(0.2)
    assert b.calls["translate"] == 1


def test_cache_hits_count_in_used_not_spent() -> None:
    b = Budget(1.0)
    b.record_cache_hit("speak", 0.3)
    b.record_cache_hit("speak", 0.3)
    assert b.total_spent == 0.0
    assert b.used["speak"] == pytest.approx(0.6)
    assert b.cache_hits["speak"] == 2


def test_no_cap_never_refuses() -> None:
    b = Budget(None)
    b.reserve("speak", 1e9)
    assert b.remaining() is None


def test_llm_tokens_accumulate() -> None:
    b = Budget(None)
    b.add_llm_tokens(100, 20)
    b.add_llm_tokens(5, 1)
    assert b.llm_tokens == {"prompt": 105, "completion": 21}
