import asyncio
import time

from app.bodhan.ratelimit import RateLimiter


def test_rpm_window_blocks_extra_requests():
    async def run():
        limiter = RateLimiter(rpm=3, concurrency=10, window_s=0.4)
        start = time.monotonic()
        stamps = []
        for _ in range(4):
            async with limiter.slot():
                stamps.append(time.monotonic() - start)
        return stamps

    stamps = asyncio.run(run())
    assert stamps[2] < 0.1          # first three go immediately
    assert stamps[3] >= 0.39        # fourth waits for the window to slide


def test_concurrency_cap():
    async def run():
        limiter = RateLimiter(rpm=100, concurrency=2)
        active = peak = 0

        async def job():
            nonlocal active, peak
            async with limiter.slot():
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0.05)
                active -= 1

        await asyncio.gather(*(job() for _ in range(6)))
        return peak

    assert asyncio.run(run()) == 2


def test_cooldown_pauses_requests():
    async def run():
        limiter = RateLimiter(rpm=100, concurrency=2)
        limiter.cool_down(0.3)
        start = time.monotonic()
        async with limiter.slot():
            return time.monotonic() - start

    assert asyncio.run(run()) >= 0.29


def test_headroom_counts_the_window_and_cooldown():
    now = [0.0]

    async def run():
        limiter = RateLimiter(rpm=3, concurrency=10, window_s=60, clock=lambda: now[0])
        assert limiter.headroom() == 3
        for _ in range(2):
            async with limiter.slot():
                pass
        assert limiter.headroom() == 1
        now[0] = 61.0  # both requests left the window
        assert limiter.headroom() == 3
        limiter.cool_down(10)  # after a 429 nothing optional should start
        assert limiter.headroom() == 0

    asyncio.run(run())
