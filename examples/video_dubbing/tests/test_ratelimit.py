from __future__ import annotations

import threading
import time

from bodhan_dub.ratelimit import (
    RECOVERY_EVERY,
    RECOVERY_STEP,
    SAFETY_FACTOR,
    ConcurrencyGate,
    RequestPacer,
)


def test_starts_below_the_advertised_ceiling() -> None:
    pacer = RequestPacer(120)

    assert pacer.ceiling == 120
    assert pacer.rpm == int(120 * SAFETY_FACTOR)


def test_requests_are_spaced_to_the_quota() -> None:
    # 6000 rpm ceiling -> ~5100 effective -> ~12 ms between request starts.
    pacer = RequestPacer(6_000)

    assert pacer.acquire() is True
    started = time.monotonic()
    assert pacer.acquire() is True

    assert time.monotonic() - started >= 0.005


def test_a_rejection_lowers_the_rate_and_raises_a_cooldown() -> None:
    pacer = RequestPacer(120)
    before = pacer.rpm

    pacer.on_rate_limited()

    stats = pacer.stats()
    assert pacer.rpm < before
    assert stats["rejections"] == 1
    assert stats["rejection_streak"] == 1
    assert stats["last_cooldown_s"] >= 2.0


def test_consecutive_rejections_double_the_cooldown() -> None:
    pacer = RequestPacer(120, base_cooldown_s=2.0)

    pacer.on_rate_limited()
    first = pacer.stats()["last_cooldown_s"]
    pacer.on_rate_limited()
    second = pacer.stats()["last_cooldown_s"]

    # Each step doubles the base, with up to 20% jitter either way.
    assert second >= first * 1.5


def test_the_cooldown_is_capped() -> None:
    pacer = RequestPacer(120, base_cooldown_s=2.0, max_cooldown_s=8.0)
    for _ in range(8):
        pacer.on_rate_limited()

    assert pacer.stats()["last_cooldown_s"] <= 8.0 * 1.25


def test_a_success_resets_the_streak() -> None:
    pacer = RequestPacer(120)
    pacer.on_rate_limited()
    pacer.on_rate_limited()
    assert pacer.stats()["rejection_streak"] == 2

    for _ in range(RECOVERY_EVERY):
        pacer.on_success()

    assert pacer.stats()["rejection_streak"] == 0


def test_one_success_does_not_reset_the_backoff() -> None:
    # A single success after a rejection must not clear the streak, or the pacer
    # oscillates: climb, burst, collapse, climb again.
    pacer = RequestPacer(120)
    pacer.on_rate_limited()
    pacer.on_success()

    assert pacer.stats()["rejection_streak"] == 1


def test_recovery_is_slow_and_bounded() -> None:
    pacer = RequestPacer(100)
    pacer.on_rate_limited()
    lowered = pacer.rpm

    for _ in range(RECOVERY_EVERY):
        pacer.on_success()

    assert pacer.rpm == lowered + RECOVERY_STEP


def test_a_success_never_exceeds_the_ceiling() -> None:
    pacer = RequestPacer(100)
    for _ in range(RECOVERY_EVERY * 200):
        pacer.on_success()

    assert pacer.rpm == 100


def test_the_rate_never_falls_below_the_floor() -> None:
    pacer = RequestPacer(120, min_rpm=10)
    for _ in range(20):
        pacer.on_rate_limited()

    assert pacer.rpm == 10


def test_the_server_ceiling_is_adopted() -> None:
    pacer = RequestPacer(120)
    pacer.set_ceiling(60)

    assert pacer.ceiling == 60
    assert pacer.rpm <= 60


def test_stop_releases_waiters_and_refuses_new_work() -> None:
    # Slow enough that the second request is still waiting when we stop.
    pacer = RequestPacer(60)
    assert pacer.acquire() is True

    result: list[bool] = []

    def waiter() -> None:
        result.append(pacer.acquire())

    thread = threading.Thread(target=waiter, daemon=True)
    thread.start()
    time.sleep(0.05)
    pacer.stop()

    thread.join(timeout=2.0)
    assert result == [False]
    assert pacer.acquire() is False


def test_stats_shape() -> None:
    pacer = RequestPacer(120)

    stats = pacer.stats()

    assert set(stats) == {
        "rpm_ceiling",
        "rpm_effective",
        "interval_s",
        "rejections",
        "rejection_streak",
        "last_cooldown_s",
        "waited_s",
    }


# -- concurrency gate ------------------------------------------------------


def test_gate_starts_conservatively_and_recovers_slowly() -> None:
    gate = ConcurrencyGate(ceiling=4, initial=2, recovery_every=3)

    assert gate.limit == 2
    gate.on_success()
    gate.on_success()
    assert gate.limit == 2
    gate.on_success()
    assert gate.limit == 3


def test_gate_drops_a_slot_on_every_rejection() -> None:
    gate = ConcurrencyGate(ceiling=4, initial=2)

    gate.on_throttled()
    assert gate.limit == 1
    gate.on_throttled()
    assert gate.limit == 1
    assert gate.stats()["rejections"] == 2


def test_gate_never_exceeds_its_ceiling() -> None:
    gate = ConcurrencyGate(ceiling=3, initial=1, recovery_every=1)
    for _ in range(50):
        gate.on_success()

    assert gate.limit == 3


def test_gate_blocks_at_the_limit() -> None:
    gate = ConcurrencyGate(ceiling=1, initial=1)
    assert gate.acquire() is True

    granted = threading.Event()

    def waiter() -> None:
        if gate.acquire():
            granted.set()
            gate.release()

    thread = threading.Thread(target=waiter, daemon=True)
    thread.start()
    assert not granted.wait(0.2)

    gate.release()
    assert granted.wait(2.0)


def test_gate_stop_releases_waiters() -> None:
    gate = ConcurrencyGate(ceiling=1, initial=1)
    assert gate.acquire() is True

    result: list[bool] = []

    def waiter() -> None:
        result.append(gate.acquire())

    thread = threading.Thread(target=waiter, daemon=True)
    thread.start()
    time.sleep(0.05)
    gate.stop()

    thread.join(timeout=2.0)
    assert result == [False]
