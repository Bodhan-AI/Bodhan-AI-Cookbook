"""Per-request Bodhan cost accounting for agent runs."""

from contextvars import ContextVar
from decimal import Decimal
from threading import Lock

from httpx import Response
from mcp.server.fastmcp.exceptions import ToolError

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
        self._lock = Lock()
        self._responses: dict[str, Response | None] = {}

    def start_request(self, fingerprint: str) -> Response | None:
        with self._lock:
            if fingerprint not in self._responses:
                self._responses[fingerprint] = None
                return None
            cached = self._responses[fingerprint]
            if cached is None:
                raise ToolError(
                    "duplicate_request: The same Bodhan request is running or already attempted "
                    "without a reusable response. Use an existing artifact or a different input."
                )
            return cached

    def cache_response(self, fingerprint: str, response: Response) -> None:
        with self._lock:
            self._responses[fingerprint] = response

    def forget_request(self, fingerprint: str) -> None:
        with self._lock:
            del self._responses[fingerprint]

    def reserve(self, model: str, cost: Decimal) -> int:
        with self._lock:
            if self.total + cost > self.limit:
                raise BudgetExhausted(f"Insufficient Bodhan budget for the next {model} request.")
            self.total += cost
            self.requests.append({"model": model, "cost_inr": cost})
            return len(self.requests) - 1

    def settle(self, index: int, cost: Decimal) -> None:
        with self._lock:
            self.total += cost - self.requests[index]["cost_inr"]
            self.requests[index]["cost_inr"] = cost
            if self.total > self.limit:
                raise BudgetExhausted("Bodhan reported usage above the requested output cap.")


active_budget: ContextVar[RequestBudget | None] = ContextVar("active_budget", default=None)
