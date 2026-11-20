from dataclasses import dataclass, field


# Placeholder estimates only — replace with verified Bodhan pricing when available.
DEFAULT_TOOL_ESTIMATES = {
    "indic_transcribe": 0.25,
    "indic_translate": 0.25,
    "indic_speak": 0.25,
    "indic_ocr": 0.25,
}


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class CostBudget:
    max_cost_inr: float
    tool_estimates: dict = field(
        default_factory=lambda: DEFAULT_TOOL_ESTIMATES.copy()
    )
    estimated_spent_inr: float = 0.0
    calls: list = field(default_factory=list)

    def estimate(self, tool_name):
        return float(self.tool_estimates.get(tool_name, 0.25))

    def check(self, tool_name):
        estimated_cost = self.estimate(tool_name)
        projected_total = self.estimated_spent_inr + estimated_cost

        if projected_total > self.max_cost_inr:
            raise BudgetExceeded(
                f"Budget limit reached. "
                f"Spent estimate: ₹{self.estimated_spent_inr:.2f}, "
                f"next call estimate: ₹{estimated_cost:.2f}, "
                f"budget: ₹{self.max_cost_inr:.2f}."
            )

        return estimated_cost

    def record(self, tool_name, estimated_cost):
        self.estimated_spent_inr += estimated_cost

        self.calls.append({
            "tool": tool_name,
            "estimated_cost_inr": estimated_cost,
            "estimated_total_inr": round(
                self.estimated_spent_inr, 4
            ),
        })

    @property
    def remaining_inr(self):
        return max(
            0.0,
            self.max_cost_inr - self.estimated_spent_inr
        )

    def summary(self):
        return {
            "max_cost_inr": self.max_cost_inr,
            "estimated_spent_inr": round(
                self.estimated_spent_inr, 4
            ),
            "estimated_remaining_inr": round(
                self.remaining_inr, 4
            ),
            "calls": self.calls,
            "cost_type": "estimated",
        }