"""INR cost accounting with a hard budget for NEW (uncached) spend."""
from __future__ import annotations

import threading
from collections import defaultdict

PRICE_TRANSLATE_PER_10K_TOKENS = 0.20
PRICE_SPEAK_PER_10K_CHARS = 6.0
PRICE_TRANSCRIBE_PER_MIN = 0.10


class BudgetExceeded(RuntimeError):
    pass


def est_translate_cost(text: str) -> float:
    tokens = max(1, len(text) // 2)  # conservative for Indic scripts
    return tokens * PRICE_TRANSLATE_PER_10K_TOKENS / 10_000


def est_speak_cost(text: str) -> float:
    return len(text) * PRICE_SPEAK_PER_10K_CHARS / 10_000


def est_transcribe_cost(seconds: float) -> float:
    return seconds / 60.0 * PRICE_TRANSCRIBE_PER_MIN


class CostTracker:
    def __init__(self, max_inr: float | None):
        self.max_inr = max_inr
        self._lock = threading.Lock()
        self.new_spend = 0.0                     # money actually spent this run
        self.full_cost = defaultdict(float)      # what the run costs uncached (deterministic)
        self.calls = defaultdict(int)
        self.cache_hits = defaultdict(int)
        self.llm_tokens = defaultdict(int)

    def check(self, estimate: float) -> None:
        with self._lock:
            if self.max_inr is not None and self.new_spend + estimate > self.max_inr:
                raise BudgetExceeded(f"budget {self.max_inr} INR would be exceeded")

    def record(self, kind: str, cost: float, cached: bool) -> None:
        with self._lock:
            self.calls[kind] += 1
            self.full_cost[kind] += cost
            if cached:
                self.cache_hits[kind] += 1
            else:
                self.new_spend += cost

    def add_tokens(self, prompt: int, completion: int) -> None:
        with self._lock:
            self.llm_tokens["prompt"] += prompt
            self.llm_tokens["completion"] += completion

    def summary(self) -> dict:
        kinds = sorted(set(self.calls) | set(self.full_cost))
        return {
            "uncached_cost_inr": {k: round(self.full_cost[k], 4) for k in kinds},
            "uncached_total_inr": round(sum(self.full_cost.values()), 4),
            "calls": {k: self.calls[k] for k in kinds},
        }
