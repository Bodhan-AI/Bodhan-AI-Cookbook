from __future__ import annotations

import pytest

from bodhan_dub.duration import DEFAULT_CHARS_PER_SECOND, SpeakingRate, fit


def test_fit_ok_when_it_fits() -> None:
    result = fit(2.0, 3.0)

    assert result.status == "ok"
    assert result.tempo == 1.0
    assert result.overrun_s == 0.0


def test_fit_stretches_within_the_limit() -> None:
    result = fit(3.0, 2.5)

    assert result.status == "stretched"
    assert result.tempo == pytest.approx(1.2)
    assert result.spill_s == 0.0


def test_fit_spills_into_following_silence() -> None:
    result = fit(3.6, 2.5)

    assert result.status == "spilled"
    assert result.tempo == pytest.approx(1.2)
    assert result.overrun_s > 0


def test_fit_reports_a_miss_rather_than_hiding_it() -> None:
    result = fit(8.0, 2.0)

    assert result.status == "miss"
    assert result.tempo == pytest.approx(1.2)
    assert result.overrun_s > 1.0


def test_tempo_is_never_outside_the_allowed_range() -> None:
    for actual, budget in [(0.5, 1.0), (1.0, 1.0), (1.3, 1.0), (5.0, 1.0), (10.0, 1.0)]:
        result = fit(actual, budget, max_tempo=1.2, max_spill_s=0.8)
        assert result.tempo <= 1.2 + 1e-9
        assert result.tempo >= 1.0 - 1e-9


def test_zero_budget_is_a_miss_not_a_crash() -> None:
    result = fit(1.0, 0.0)

    assert result.status == "miss"


def test_every_language_has_a_prior() -> None:
    for lang in ("hi", "ta", "bn", "te", "ml", "kn", "mr", "gu", "pa", "or", "as", "ur"):
        assert lang in DEFAULT_CHARS_PER_SECOND


def test_rate_uses_the_prior_until_calibrated() -> None:
    rate = SpeakingRate("hi")
    assert rate.samples == 0
    assert rate.rate == DEFAULT_CHARS_PER_SECOND["hi"]


def test_rate_calibrates_from_observations() -> None:
    rate = SpeakingRate("hi")
    prior = rate.rate
    # Observed speech is much faster than the prior.
    for _ in range(4):
        rate.observe(200, 10.0)

    assert rate.rate > prior


def test_observations_are_ignored_when_degenerate() -> None:
    rate = SpeakingRate("ta")
    rate.observe(0, 5.0)
    rate.observe(100, 0.0)

    assert rate.samples == 0


def test_prediction_uses_the_calibrated_rate() -> None:
    rate = SpeakingRate("bn", default=10.0)
    assert rate.predict_seconds(100) == pytest.approx(10.0)

    rate.observe(100, 5.0)
    rate.observe(100, 5.0)
    assert rate.predict_seconds(100) < 10.0


def test_stats_are_serialisable() -> None:
    rate = SpeakingRate("hi")
    rate.observe(100, 8.0)

    payload = rate.to_dict()
    assert payload["samples"] == 1
    assert payload["chars_per_second"] > 0
