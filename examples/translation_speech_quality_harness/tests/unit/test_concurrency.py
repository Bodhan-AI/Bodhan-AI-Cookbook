"""K11: adaptive concurrency (AIMD) and the shared retry policy for Bodhan and the judge LLM.

The limiter is tested on its own first (deterministic, no HTTP), then the same scenarios run through the stub
server for both clients, so the Bodhan and LLM paths are shown to behave the same way.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path

import pytest

from bodhan_eval import clients
from bodhan_eval.budget import Budget
from bodhan_eval.cache import Cache
from bodhan_eval.clients import AdaptiveLimiter, ApiError, BodhanClient, LLMClient
from bodhan_eval.config import Settings
from bodhan_eval.testing.stub_server import StubServer

# ---------------------------------------------------------------------------
# the limiter alone
# ---------------------------------------------------------------------------


def test_rate_limit_halves_down_to_one() -> None:
    lim = AdaptiveLimiter("translate", max_limit=8, increase_after=20)
    seen = []
    for _ in range(5):
        with lim.slot() as ticket:
            lim.failure(ticket, rate_limited=True)
        seen.append(lim.limit)
    assert seen == [4, 2, 1, 1, 1]


def test_successes_add_one_after_the_streak_up_to_the_maximum() -> None:
    lim = AdaptiveLimiter("llm", max_limit=4, increase_after=3)
    with lim.slot() as ticket:
        lim.failure(ticket, rate_limited=True)
    assert lim.limit == 2
    seen = []
    for _ in range(9):
        with lim.slot():
            pass
        lim.success()
        seen.append(lim.limit)
    assert seen == [2, 2, 3, 3, 3, 4, 4, 4, 4]  # +1 per 3 successes, capped at the configured maximum


def test_a_burst_of_429s_from_one_window_halves_once() -> None:
    """Six requests in flight all get 429: one congestion event, not six halvings (8 -> 4, not 8 -> 1)."""
    lim = AdaptiveLimiter("speak", max_limit=8, increase_after=20)
    with ExitStack() as stack:
        tickets = [stack.enter_context(lim.slot()) for _ in range(6)]
        for t in tickets:
            lim.failure(t, rate_limited=True)
    assert lim.limit == 4


def test_server_errors_reset_the_streak_but_do_not_halve() -> None:
    lim = AdaptiveLimiter("transcribe", max_limit=4, increase_after=2)
    with lim.slot() as t:
        lim.failure(t, rate_limited=True)
    lim.success()
    with lim.slot() as t:
        lim.failure(t, rate_limited=False)  # 5xx / timeout: back off, keep the limit
    lim.success()
    assert lim.limit == 2  # the 5xx broke the streak, so one success is not enough to grow
    lim.success()
    assert lim.limit == 3


def test_a_pause_holds_every_new_request_of_the_service(waits: list[float]) -> None:
    """One worker's 429 pauses the service: the others wait out the Retry-After instead of earning 429s too."""
    lim = AdaptiveLimiter("transcribe", max_limit=4, increase_after=20)
    lim.pause(8.0)
    with lim.slot():
        pass
    assert waits == [8.0]
    lim.pause(3.0)
    lim.pause(1.0)  # a shorter pause does not cut a longer one short
    with lim.slot():
        pass
    with lim.slot():  # the pause is over: no more waiting
        pass
    assert waits == [8.0, 3.0]


def test_a_lowered_limit_blocks_new_requests_until_a_slot_frees() -> None:
    lim = AdaptiveLimiter("translate", max_limit=2, increase_after=20)
    with lim.slot() as t:
        lim.failure(t, rate_limited=True)
    assert lim.limit == 1
    entered = threading.Event()

    def worker() -> None:
        with lim.slot():
            entered.set()

    with lim.slot():
        th = threading.Thread(target=worker)
        th.start()
        assert not entered.wait(0.2)  # limit 1, one in flight: the second request waits
    assert entered.wait(2.0)
    th.join()
    assert lim.in_flight == 0


# ---------------------------------------------------------------------------
# both clients through the stub
# ---------------------------------------------------------------------------

FIXTURE = {"translate": {"hi": {}}, "chat": [{"contains": "verdict", "content": '{"errors": []}'}]}
FAULT_PATH = {"bodhan": "/translate", "llm": "/chat/completions"}


@pytest.fixture
def stub() -> Iterator[StubServer]:
    with StubServer(FIXTURE) as s:
        yield s


@pytest.fixture
def cache(tmp_path: Path) -> Iterator[Cache]:
    c = Cache(tmp_path / "cache")
    yield c
    c.close()


@pytest.fixture
def waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record every wait instead of sleeping, on a fake clock that a wait moves forward.

    The clients wait only through ``clients._sleep`` and read the time only through ``clients._now``.
    """
    seen: list[float] = []
    clock = [1000.0]

    def sleep(seconds: float) -> None:
        seen.append(round(seconds, 6))
        clock[0] += seconds

    monkeypatch.setattr(clients, "_sleep", sleep)
    monkeypatch.setattr(clients, "_now", lambda: clock[0])
    return seen


def make(kind: str, stub: StubServer, cache: Cache, budget: Budget | None = None, **kw):
    """(client, limiter, call(i)) for one client kind; every call(i) is a cache miss."""
    base = {
        "bodhan_base_url": stub.url,
        "translate_key": "stub-key",
        "llm_base_url": f"{stub.url}/v1",
        "llm_model": "stub-judge",
        "llm_api_key": "stub-key",
        "concurrency": 4,
        "max_retries": 3,
        "request_timeout_s": 10.0,
        "retry_base_s": 0.001,
        "aimd_increase_after": 2,
    }
    s = Settings(**{**base, **kw})
    b = budget or Budget(None)
    if kind == "bodhan":
        bc = BodhanClient(s, cache, b)
        return bc, bc.limiters["translate"], lambda i: bc.translate(f"text number {i}", "hi")
    lc = LLMClient(s, cache, b, inr_per_1k_tokens=0.1)  # a price, so a reservation is visible
    return lc, lc.limiter, lambda i: lc.chat_json([{"role": "user", "content": f"verdict {i}"}])


KINDS = pytest.mark.parametrize("kind", ["bodhan", "llm"])


@KINDS
def test_429_halves_then_recovers(kind: str, stub: StubServer, cache: Cache, waits: list[float]) -> None:
    _, lim, call = make(kind, stub, cache)
    stub.add_fault(FAULT_PATH[kind], 429, times=1, retry_after="0")
    limits = []
    for i in range(5):
        call(i)
        limits.append(lim.limit)
    # call 0: 429 -> 4 halves to 2, its retry succeeds (streak 1); every 2 successes add 1, back to the maximum 4
    assert limits == [2, 3, 3, 4, 4]
    assert [r["status"] for r in stub.requests] == [429, 200, 200, 200, 200, 200]


@KINDS
def test_retry_after_is_honoured(kind: str, stub: StubServer, cache: Cache, waits: list[float]) -> None:
    _, _, call = make(kind, stub, cache)
    stub.add_fault(FAULT_PATH[kind], 429, times=2, retry_after="0.25")
    call(0)
    assert waits == [0.25, 0.25]


@KINDS
def test_retry_after_is_capped(kind: str, stub: StubServer, cache: Cache, waits: list[float]) -> None:
    _, _, call = make(kind, stub, cache, retry_max_wait_s=5.0)
    stub.add_fault(FAULT_PATH[kind], 429, times=1, retry_after="3600")
    call(0)
    assert waits == [5.0]


@KINDS
def test_server_error_backs_off_with_jitter_without_halving(
    kind: str, stub: StubServer, cache: Cache, waits: list[float]
) -> None:
    _, lim, call = make(kind, stub, cache, retry_base_s=1.0)
    stub.add_fault(FAULT_PATH[kind], 503, times=2)
    call(0)
    assert lim.limit == 4
    assert len(waits) == 2
    assert 0.5 <= waits[0] <= 1.0 and 1.0 <= waits[1] <= 2.0  # base * 2^i, jittered into [50 %, 100 %]


@KINDS
def test_retries_exhausted_cleanly(kind: str, stub: StubServer, cache: Cache, waits: list[float]) -> None:
    budget = Budget(5.0)
    _, lim, call = make(kind, stub, cache, budget, max_retries=3)
    stub.add_fault(FAULT_PATH[kind], 503, times=-1)
    with pytest.raises(ApiError) as err:
        call(0)
    assert err.value.code == "retries_exhausted"
    assert len(stub.requests) == 3  # max_retries is the number of attempts; the SDK adds none of its own
    assert budget.reserved == 0.0 and budget.total_spent == 0.0 and budget.calls == {}
    assert lim.in_flight == 0 and lim.limit == 4


@KINDS
def test_rate_limiting_is_waited_out_beyond_max_retries(
    kind: str, stub: StubServer, cache: Cache, waits: list[float]
) -> None:
    """429 asks us to slow down; it is not a failure. Only errors count against ``max_retries``."""
    _, lim, call = make(kind, stub, cache, max_retries=3)
    stub.add_fault(FAULT_PATH[kind], 429, times=5, retry_after="20")
    call(0)
    assert [r["status"] for r in stub.requests] == [429] * 5 + [200]
    assert waits == [20.0] * 5 and lim.in_flight == 0


@KINDS
def test_rate_limited_past_the_patience_gives_up(kind: str, stub: StubServer, cache: Cache, waits: list[float]) -> None:
    budget = Budget(5.0)
    _, lim, call = make(kind, stub, cache, budget, rate_limit_patience_s=50.0)
    stub.add_fault(FAULT_PATH[kind], 429, times=-1, retry_after="20")
    with pytest.raises(ApiError) as err:
        call(0)
    assert err.value.code == "retries_exhausted" and "rate limited" in str(err.value)
    assert len(stub.requests) == 3  # 429 at 0 s and 20 s; the one at 40 s would need waiting until 60 s > 50 s
    assert waits == [20.0, 20.0]
    assert budget.reserved == 0.0 and budget.total_spent == 0.0 and lim.in_flight == 0


@KINDS
def test_retries_do_not_double_count_cost(kind: str, stub: StubServer, cache: Cache, waits: list[float]) -> None:
    budget = Budget(None)
    _, _, call = make(kind, stub, cache, budget)
    stub.add_fault(FAULT_PATH[kind], 429, times=2, retry_after="0")
    call(0)
    service = "translate" if kind == "bodhan" else "llm"
    assert budget.calls == {service: 1}


def test_llm_sdk_retries_are_off(stub: StubServer, cache: Cache, waits: list[float]) -> None:
    """No patience for 429s: one must surface after exactly one request (the SDK would otherwise retry it)."""
    _, _, call = make("llm", stub, cache, max_retries=1, rate_limit_patience_s=0.0)
    stub.add_fault("/chat/completions", 429, times=1, retry_after="1")
    with pytest.raises(ApiError) as err:
        call(0)
    assert err.value.code == "retries_exhausted"
    assert len(stub.requests) == 1


def test_each_service_has_its_own_limiter(stub: StubServer, cache: Cache, waits: list[float]) -> None:
    bc, lim, call = make("bodhan", stub, cache)
    stub.add_fault("/translate", 429, times=1, retry_after="0")
    call(0)
    assert lim.limit < 4
    assert {s: x.limit for s, x in bc.limiters.items() if s != "translate"} == {
        "transliterate": 4,
        "speak": 4,
        "transcribe": 4,
    }


def test_concurrent_requests_never_exceed_the_limit(stub: StubServer, cache: Cache, waits: list[float]) -> None:
    from concurrent.futures import ThreadPoolExecutor

    _, lim, call = make("bodhan", stub, cache, concurrency=3)
    peak = [0]
    original = lim.slot

    def watching_slot():  # wraps the real slot to record the in-flight high-water mark
        cm = original()

        class Watch:
            def __enter__(self):
                t = cm.__enter__()
                peak[0] = max(peak[0], lim.in_flight)
                return t

            def __exit__(self, *exc):
                return cm.__exit__(*exc)

        return Watch()

    lim.slot = watching_slot  # type: ignore[method-assign]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(call, range(24)))
    assert 1 <= peak[0] <= 3
