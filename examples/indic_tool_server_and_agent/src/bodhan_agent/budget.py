"""Budget enforcement: steps, cost in INR, wall-clock time."""
import logging
import time

logger = logging.getLogger(__name__)


class BudgetExceeded(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class BudgetTracker:
    def __init__(
        self,
        max_steps: int,
        max_cost_inr: float,
        max_time_s: float = 300.0,
    ):
        self.max_steps = max_steps
        self.max_cost_inr = max_cost_inr
        self.max_time_s = max_time_s
        self._steps = 0
        self._cost = 0.0
        self._start = time.time()

    def check(self, estimated_cost: float = 0.0) -> None:
        """Raise BudgetExceeded if any limit would be hit."""
        elapsed = time.time() - self._start
        if self._steps >= self.max_steps:
            msg = f"Max steps ({self.max_steps}) reached"
            logger.warning("Budget exceeded: %s", msg)
            raise BudgetExceeded(msg)
        if self._cost + estimated_cost > self.max_cost_inr:
            msg = (
                f"Cost budget (₹{self.max_cost_inr:.2f}) would be exceeded "
                f"(current ₹{self._cost:.4f} + estimated ₹{estimated_cost:.4f})"
            )
            logger.warning("Budget exceeded: %s", msg)
            raise BudgetExceeded(msg)
        if elapsed > self.max_time_s:
            msg = f"Time limit ({self.max_time_s:.0f}s) exceeded (elapsed {elapsed:.1f}s)"
            logger.warning("Budget exceeded: %s", msg)
            raise BudgetExceeded(msg)

    def reserve(self, estimated_cost: float) -> None:
        """Check but don't record — used to pre-flight parallel steps."""
        self.check(estimated_cost)

    def record_step(self, cost_inr: float = 0.0) -> None:
        """Record one completed step and its cost."""
        self._steps += 1
        self._cost += cost_inr
        logger.debug(
            "Budget: steps=%d/%d  cost=₹%.4f/₹%.2f",
            self._steps,
            self.max_steps,
            self._cost,
            self.max_cost_inr,
        )

    @property
    def spent_inr(self) -> float:
        return self._cost

    @property
    def steps_used(self) -> int:
        return self._steps

    @property
    def elapsed_s(self) -> float:
        return time.time() - self._start
