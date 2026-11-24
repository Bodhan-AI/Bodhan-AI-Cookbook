"""Rupee accounting. Prices are the hosted prices from the assignment."""
from __future__ import annotations

import contextlib
import contextvars
import os
from collections import defaultdict

from .errors import BudgetExceeded

PRICE_TRANSCRIBE_PER_MIN = 0.10
PRICE_TRANSLATE_PER_10K_OUT_TOKENS = 0.20
PRICE_SPEAK_PER_10K_CHARS = 6.0
PRICE_OCR_PER_IMAGE = 0.20


class Ledger:
    """Per-tool-call cost accumulator (a ContextVar, so concurrent calls do not mix)."""

    def __init__(self) -> None:
        self.total = 0.0
        self.cached_calls = 0
        self.api_calls = 0


_current: contextvars.ContextVar[Ledger | None] = contextvars.ContextVar("ledger", default=None)


@contextlib.contextmanager
def ledger():
    led = Ledger()
    token = _current.set(led)
    try:
        yield led
    finally:
        _current.reset(token)


class CostTracker:
    def __init__(self) -> None:
        self.total = 0.0
        self.by_service: dict[str, float] = defaultdict(float)
        self.calls: dict[str, int] = defaultdict(int)
        self.seen: dict[str, int] = defaultdict(int)  # request fingerprint -> times sent to the API
        self.reserved_total = 0.0  # money promised to calls that are still in flight
        self._limit: float | None = None

    def reset(self, limit: float | None = None) -> None:
        self.__init__()  # type: ignore[misc]
        self._limit = limit

    def set_limit(self, limit: float | None) -> None:
        self._limit = limit

    @property
    def limit(self) -> float | None:
        if self._limit is not None:
            return self._limit
        env = os.environ.get("BODHAN_MAX_COST_INR")
        try:
            return float(env) if env else None
        except ValueError:
            return None

    def remaining(self) -> float | None:
        lim = self.limit
        return None if lim is None else max(0.0, lim - self.total)

    def precheck(self, estimate: float) -> None:
        lim = self.limit
        if lim is not None and self.total + self.reserved_total + estimate > lim + 1e-9:
            raise BudgetExceeded(
                f"Budget would be exceeded: spent ₹{self.total:.3f} (+₹{self.reserved_total:.3f} in flight), "
                f"this call needs about ₹{estimate:.3f}, limit is ₹{lim:.2f}.")

    @contextlib.contextmanager
    def reserved(self, estimate: float):
        """Check the budget AND hold the money while the call is in flight (concurrent calls see it)."""
        self.precheck(estimate)
        self.reserved_total += estimate
        try:
            yield
        finally:
            self.reserved_total = max(0.0, self.reserved_total - estimate)

    def charge(self, service: str, inr: float) -> None:
        self.total += inr
        self.by_service[service] += inr
        led = _current.get()
        if led is not None:
            led.total += inr

    def note_call(self, service: str, fingerprint: str, cached: bool) -> None:
        led = _current.get()
        if cached:
            if led:
                led.cached_calls += 1
            return
        self.calls[service] += 1
        self.seen[f"{service}:{fingerprint}"] += 1
        if led:
            led.api_calls += 1

    def duplicate_requests(self) -> int:
        return sum(n - 1 for n in self.seen.values() if n > 1)


tracker = CostTracker()
