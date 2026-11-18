"""Per-request Bodhan cost accounting for agent runs."""

from contextvars import ContextVar
from decimal import Decimal

OCR_RATE = Decimal("0.20")
SPEECH_RATE = Decimal("0.0006")
TRANSCRIBE_PER_SECOND = Decimal("0.1") / 60
TRANSLATE_PER_TOKEN = Decimal("0.00002")
TRANSLATION_OUTPUT_LIMIT = 2048


class BudgetExhausted(RuntimeError):
    pass


class RequestBudget:
    def __init__(self, limit: Decimal) -> None:
        self.limit = limit
        self.total = Decimal(0)
        self.requests: list[dict] = []

    def reserve(self, model: str, cost: Decimal) -> int:
        if self.total + cost > self.limit:
            raise BudgetExhausted(f"Insufficient Bodhan budget for the next {model} request.")
        self.total += cost
        self.requests.append({"model": model, "cost_inr": cost})
        return len(self.requests) - 1

    def settle(self, index: int, cost: Decimal) -> None:
        self.total += cost - self.requests[index]["cost_inr"]
        self.requests[index]["cost_inr"] = cost
        if self.total > self.limit:
            raise BudgetExhausted("Bodhan reported usage above the requested output cap.")


active_budget: ContextVar[RequestBudget | None] = ContextVar("active_budget", default=None)


def reserve(model: str, amount: Decimal) -> None:
    budget = active_budget.get()
    if budget is not None:
        budget.reserve(model, amount)
