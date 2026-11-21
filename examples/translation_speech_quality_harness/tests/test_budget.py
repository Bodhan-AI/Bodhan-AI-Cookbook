import random

from bodhan_eval.budget import Budget


def test_hard_budget_randomized():
    rng = random.Random(20261119)
    budget = Budget(50.0)
    spent = 0.0
    for _ in range(1000):
        cost = round(rng.uniform(0.0, 2.0), 6)
        if budget.can_afford(cost):
            assert budget.charge(cost, "random") is True
            spent += cost
            assert spent <= 50.000001
        else:
            break
    assert budget.spent <= 50.000001


def test_estimates_and_degradation():
    budget = Budget(1.0)
    assert budget.estimate_translate(1000) == 1000 * (0.20 / 10_000)
    assert budget.estimate_speak(1000) == 1000 * (6.00 / 10_000)
    assert budget.estimate_transcribe(30) == 30 * (0.10 / 60)
    assert budget.degradation_order() == (
        "skip_speech_generation",
        "reduce_speech_sample",
        "skip_back_transcription_lowest_priority",
        "skip_judge_confirmation",
        "finish_with_free_checks",
    )


def test_charge_never_raises_overrun():
    budget = Budget(0.01)
    assert budget.charge(0.006, "test")
    assert budget.charge(0.006, "test") is False
