"""Budget and cost enforcement tracker for the task agent."""

from __future__ import annotations

import threading
import time
from typing import Optional


class BudgetExceededException(Exception):
    """Raised when steps, cost, or time exceeds configured limits."""
    pass


class BudgetTracker:
    """Thread-safe tracker for step count, accumulated INR cost, and elapsed execution time."""

    def __init__(
        self,
        max_steps: int = 30,
        max_cost_inr: float = 20.0,
        per_call_timeout_sec: float = 120.0,
        total_timeout_sec: float = 300.0,
    ):
        self.max_steps = max_steps
        self.max_cost_inr = max_cost_inr
        self.per_call_timeout_sec = per_call_timeout_sec
        self.total_timeout_sec = total_timeout_sec

        self.current_step: int = 0
        self.total_cost_inr: float = 0.0
        self.start_time: float = time.time()
        self.is_stopped: bool = False
        self.stop_reason: Optional[str] = None
        self._lock = threading.Lock()

    @property
    def elapsed_seconds(self) -> float:
        return round(time.time() - self.start_time, 2)

    def check_can_afford(self, estimated_cost_inr: float) -> None:
        """Fail before executing a paid call if its estimated cost would exceed max_cost_inr."""
        with self._lock:
            projected = round(self.total_cost_inr + max(0.0, estimated_cost_inr), 4)
            if projected > self.max_cost_inr:
                self.is_stopped = True
                self.stop_reason = (
                    f"Projected cost budget exceeded (₹{self.total_cost_inr:.4f} + est. "
                    f"₹{estimated_cost_inr:.4f} = ₹{projected:.4f} > ₹{self.max_cost_inr:.2f})"
                )
                raise BudgetExceededException(self.stop_reason)

    def add_cost(self, cost_inr: float) -> None:
        """Accumulate cost from auxiliary paid calls (e.g. verification) without incrementing step count."""
        with self._lock:
            self.total_cost_inr = round(self.total_cost_inr + max(0.0, cost_inr), 4)
            if self.total_cost_inr > self.max_cost_inr:
                self.is_stopped = True
                self.stop_reason = f"Cost budget exceeded (₹{self.total_cost_inr:.2f} > ₹{self.max_cost_inr:.2f})"
                raise BudgetExceededException(self.stop_reason)

    def record_step(self, cost_inr: float = 0.0) -> None:
        """Increment step count and accumulate cost, checking limits."""
        with self._lock:
            self.current_step += 1
            self.total_cost_inr = round(self.total_cost_inr + max(0.0, cost_inr), 4)

            if self.current_step > self.max_steps:
                self.is_stopped = True
                self.stop_reason = f"Step limit exceeded ({self.current_step} > {self.max_steps})"
                raise BudgetExceededException(self.stop_reason)

            if self.total_cost_inr > self.max_cost_inr:
                self.is_stopped = True
                self.stop_reason = f"Cost budget exceeded (₹{self.total_cost_inr:.2f} > ₹{self.max_cost_inr:.2f})"
                raise BudgetExceededException(self.stop_reason)

            if self.elapsed_seconds > self.total_timeout_sec:
                self.is_stopped = True
                self.stop_reason = f"Execution timeout exceeded ({self.elapsed_seconds}s > {self.total_timeout_sec}s)"
                raise BudgetExceededException(self.stop_reason)

    def can_proceed(self) -> bool:
        """Check if agent can run another step."""
        if self.is_stopped:
            return False
        if self.current_step >= self.max_steps:
            return False
        if self.total_cost_inr >= self.max_cost_inr:
            return False
        if self.elapsed_seconds >= self.total_timeout_sec:
            return False
        return True

    def check_budget(self) -> None:
        """Raise BudgetExceededException if budget or time limit has been reached."""
        if not self.can_proceed():
            reason = self.stop_reason or f"Limit reached: steps={self.current_step}/{self.max_steps}, cost=₹{self.total_cost_inr:.2f}/₹{self.max_cost_inr:.2f}"
            self.is_stopped = True
            self.stop_reason = reason
            raise BudgetExceededException(reason)
