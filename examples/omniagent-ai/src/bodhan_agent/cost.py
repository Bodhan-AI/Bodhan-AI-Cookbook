
from decimal import Decimal, InvalidOperation


class CostTracker:
    """Track API usage and known costs in INR."""

    def __init__(self, price_per_call=None):
        self.price_per_call = {}

        for tool, price in (price_per_call or {}).items():
            try:
                amount = Decimal(str(price))
            except (InvalidOperation, ValueError):
                raise ValueError(
                    f"Invalid price for {tool}"
                )

            if not amount.is_finite() or amount < 0:
                raise ValueError(
                    f"Price must be non-negative: {tool}"
                )

            self.price_per_call[tool] = amount

        self.calls = {}
        self.total_known_cost = Decimal("0")
        self.unknown_cost_calls = 0

    def record_call(self, tool):
        """Record one attempted API call."""

        self.calls[tool] = self.calls.get(tool, 0) + 1

        if tool in self.price_per_call:
            self.total_known_cost += (
                self.price_per_call[tool]
            )
        else:
            self.unknown_cost_calls += 1

    def report(self):
        """Return an honest usage and cost report."""

        return {
            "currency": "INR",
            "total_calls": sum(self.calls.values()),
            "calls_by_tool": dict(self.calls),
            "known_cost_inr": str(
                self.total_known_cost
            ),
            "unknown_cost_calls": self.unknown_cost_calls,
            "total_cost_inr": (
                str(self.total_known_cost)
                if self.unknown_cost_calls == 0
                else None
            ),
            "cost_complete": (
                self.unknown_cost_calls == 0
            ),
        }
