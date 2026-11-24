"""Cost accounting and the ``--max-cost-inr`` hard stop.

Two numbers are tracked separately:

* ``spent``: money actually paid in this run (cache misses only). Stopping
  before this crosses the budget is the guarantee ``--max-cost-inr`` gives.
* ``used``: the price of every model output the report relied on, whether
  fresh or cached. This goes in the report, so a rerun from cache produces
  the same numbers (and the same report) while ``spent`` is 0.
"""

from __future__ import annotations

import threading
from collections import defaultdict


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    def __init__(self, max_inr: float | None):
        self.max_inr = max_inr
        self._lock = threading.Lock()
        self.spent: dict[str, float] = defaultdict(float)
        self.used: dict[str, float] = defaultdict(float)
        self.calls: dict[str, int] = defaultdict(int)
        self.cache_hits: dict[str, int] = defaultdict(int)
        self.reserved = 0.0
        self.llm_tokens = {"prompt": 0, "completion": 0}  # of every answer used, cached or not
        self.llm_fresh_tokens = {"prompt": 0, "completion": 0}  # of answers paid for in this run
        self.llm_cost_usd = 0.0  # what the LLM provider reported charging in this run (cache misses only), if it does
        self.refused: dict[str, int] = defaultdict(int)
        self._local = threading.local()  # per-thread cache-hit count, so the event log can attribute hits to a check

    @property
    def total_spent(self) -> float:
        return sum(self.spent.values())

    def reserve(self, service: str, estimate: float) -> None:
        """Claim ``estimate`` before a paid call; raise if it would cross the budget."""
        with self._lock:
            if self.max_inr is not None and self.total_spent + self.reserved + estimate > self.max_inr:
                self.refused[service] += 1
                raise BudgetExceeded(
                    f"{service}: estimated ₹{estimate:.4f} would exceed --max-cost-inr {self.max_inr} "
                    f"(spent ₹{self.total_spent:.4f})"
                )
            self.reserved += estimate

    def settle(self, service: str, estimate: float, actual: float) -> None:
        with self._lock:
            self.reserved = max(0.0, self.reserved - estimate)
            self.spent[service] += actual
            self.used[service] += actual
            self.calls[service] += 1

    def release(self, estimate: float) -> None:
        with self._lock:
            self.reserved = max(0.0, self.reserved - estimate)

    def record_cache_hit(self, service: str, cost: float) -> None:
        with self._lock:
            self.used[service] += cost
            self.cache_hits[service] += 1
        self._local.hits = self.thread_cache_hits() + 1

    def thread_cache_hits(self) -> int:
        """Cache hits recorded by the calling thread so far (a check runs in one thread, so a delta is per check)."""
        return int(getattr(self._local, "hits", 0))

    def remaining(self) -> float | None:
        if self.max_inr is None:
            return None
        with self._lock:
            return self.max_inr - self.total_spent - self.reserved

    def add_llm_tokens(self, prompt: int, completion: int, fresh: bool = False) -> None:
        with self._lock:
            self.llm_tokens["prompt"] += prompt
            self.llm_tokens["completion"] += completion
            if fresh:
                self.llm_fresh_tokens["prompt"] += prompt
                self.llm_fresh_tokens["completion"] += completion

    def add_llm_cost_usd(self, usd: float) -> None:
        with self._lock:
            self.llm_cost_usd += usd
