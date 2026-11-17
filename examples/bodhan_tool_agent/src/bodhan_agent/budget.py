import time


class Budget:
    """Tracks step count, cumulative cost (INR) and wall-clock time for a single run."""

    def __init__(
        self,
        max_steps: int,
        max_cost_inr: float,
        max_wall_secs: float = 300.0,
    ) -> None:
        self.max_steps = max_steps
        self.max_cost_inr = max_cost_inr
        self.max_wall_secs = max_wall_secs
        self.steps_used: int = 0
        self.cost_used: float = 0.0
        self._start: float = time.monotonic()

    # ------------------------------------------------------------------
    # Mutators
    # ------------------------------------------------------------------

    def tick(self) -> None:
        """Increment the step counter by 1."""
        self.steps_used += 1

    def add_cost(self, inr: float) -> None:
        """Accumulate tool cost in INR."""
        self.cost_used += inr

    # ------------------------------------------------------------------
    # Predicates
    # ------------------------------------------------------------------

    def steps_ok(self) -> bool:
        return self.steps_used < self.max_steps

    def cost_ok(self, estimated: float = 0.0) -> bool:
        return (self.cost_used + estimated) <= self.max_cost_inr

    def time_ok(self) -> bool:
        return (time.monotonic() - self._start) < self.max_wall_secs

    def ok(self, estimated_next_cost: float = 0.0) -> bool:
        """Return True only when all three limits are within bounds."""
        return self.steps_ok() and self.cost_ok(estimated_next_cost) and self.time_ok()

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def reason_stopped(self) -> str:
        if not self.steps_ok():
            return f"max_steps ({self.max_steps}) reached"
        if not self.cost_ok():
            return (
                f"max_cost_inr ({self.max_cost_inr}) reached "
                f"(used \u20b9{self.cost_used:.2f})"
            )
        if not self.time_ok():
            return "wall-clock time limit reached"
        return "unknown"

    def snapshot(self) -> dict:
        return {
            "steps_used": self.steps_used,
            "max_steps": self.max_steps,
            "cost_inr": self.cost_used,
            "max_cost_inr": self.max_cost_inr,
            "elapsed_secs": round(time.monotonic() - self._start, 1),
        }
