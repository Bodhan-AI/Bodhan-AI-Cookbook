from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass
class CostEstimate:
    amount_inr: Decimal | None
    reason: str | None = None

    @property
    def known(self) -> bool:
        return self.amount_inr is not None


@dataclass
class UsageTracker:
    """Tracks Bodhan API usage and estimated costs."""

    transcribe_rate_per_minute: Decimal = Decimal("0.10")
    translate_rate_per_10k_tokens: Decimal = Decimal("0.20")
    speak_rate_per_10k_characters: Decimal = Decimal("6.00")
    ocr_rate_per_page: Decimal = Decimal("0.20")

    totals: dict[str, Decimal] = field(
        default_factory=lambda: {
            "transcribe": Decimal("0"),
            "translate": Decimal("0"),
            "speak": Decimal("0"),
            "ocr": Decimal("0"),
        }
    )
    calls: dict[str, int] = field(
        default_factory=lambda: {
            "transcribe": 0,
            "translate": 0,
            "speak": 0,
            "ocr": 0,
        }
    )
    unknown_attempts: dict[str, int] = field(
        default_factory=lambda: {
            "transcribe": 0,
            "translate": 0,
            "speak": 0,
            "ocr": 0,
        }
    )
    unknown_costs: list[str] = field(default_factory=list)
    reconciled_totals: dict[str, Decimal] = field(
        default_factory=lambda: {
            "transcribe": Decimal("0"),
            "translate": Decimal("0"),
            "speak": Decimal("0"),
            "ocr": Decimal("0"),
        }
    )
    estimated_totals: dict[str, Decimal] = field(
        default_factory=lambda: {
            "transcribe": Decimal("0"),
            "translate": Decimal("0"),
            "speak": Decimal("0"),
            "ocr": Decimal("0"),
        }
    )
    reserved_total_inr: Decimal = field(
        default=Decimal("0"),
        init=False,
        repr=False,
    )

    @staticmethod
    def _non_negative_decimal(value: Any, name: str) -> Decimal:
        try:
            result = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be a valid number") from exc

        if not result.is_finite() or result < 0:
            raise ValueError(f"{name} must be a finite non-negative number")

        return result

    @staticmethod
    def _non_negative_int(value: Any, name: str) -> int:
        if isinstance(value, bool):
            raise ValueError(f"{name} must be a non-negative integer")

        try:
            result = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be a non-negative integer") from exc

        if result != value or result < 0:
            raise ValueError(f"{name} must be a non-negative integer")

        return result

    def record_transcription(self, duration_seconds: float) -> CostEstimate:
        duration = self._non_negative_decimal(
            duration_seconds, "duration_seconds"
        )
        cost = duration * self.transcribe_rate_per_minute / Decimal("60")
        self._record("transcribe", cost)
        self.estimated_totals["transcribe"] += cost
        return CostEstimate(cost)

    def record_translation(self, usage: dict[str, Any]) -> CostEstimate:
        tokens = self._extract_output_tokens(usage)

        if tokens is None:
            reason = "Translation output-token usage was not provided"
            self.unknown_costs.append(reason)
            self.calls["translate"] += 1
            return CostEstimate(None, reason)

        cost = (
            Decimal(tokens)
            * self.translate_rate_per_10k_tokens
            / Decimal("10000")
        )
        self._record("translate", cost)
        self.reconciled_totals["translate"] += cost
        return CostEstimate(cost)

    def record_speech(self, input_text: str) -> CostEstimate:
        if not isinstance(input_text, str):
            raise ValueError("input_text must be a string")

        characters = len(input_text)
        cost = (
            Decimal(characters)
            * self.speak_rate_per_10k_characters
            / Decimal("10000")
        )
        self._record("speak", cost)
        self.estimated_totals["speak"] += cost
        return CostEstimate(cost)

    def record_ocr(self, pages: int = 1) -> CostEstimate:
        page_count = self._non_negative_int(pages, "pages")
        cost = Decimal(page_count) * self.ocr_rate_per_page
        self._record("ocr", cost)
        self.estimated_totals["ocr"] += cost
        return CostEstimate(cost)

    def _record(self, service: str, cost: Decimal) -> None:
        self.totals[service] += cost
        self.calls[service] += 1

    @staticmethod
    def _extract_output_tokens(usage: dict[str, Any]) -> int | None:
        if not isinstance(usage, dict):
            return None

        for key in ("completion_tokens", "output_tokens"):
            if key in usage:
                value = usage[key]
                if isinstance(value, bool):
                    return None
                if isinstance(value, int):
                    token_count = value
                elif isinstance(value, str):
                    stripped = value.strip()
                    if not stripped or not stripped.isdigit():
                        return None
                    token_count = int(stripped)
                else:
                    return None
                if token_count < 0:
                    return None
                return token_count

        return None

    @property
    def bodhan_total_inr(self) -> Decimal:
        return sum(self.totals.values(), Decimal("0"))

    def can_spend(self, estimated_cost_inr: Decimal, limit_inr: Decimal) -> bool:
        estimate = self._non_negative_decimal(
            estimated_cost_inr, "estimated_cost_inr"
        )
        limit = self._non_negative_decimal(limit_inr, "limit_inr")
        return (
            self.bodhan_total_inr
            + self.reserved_total_inr
            + estimate
            <= limit
        )

    def reserve(self, estimated_cost_inr: Decimal) -> Decimal:
        estimate = self._non_negative_decimal(
            estimated_cost_inr, "estimated_cost_inr"
        )
        self.reserved_total_inr += estimate
        return estimate

    def release(self, estimated_cost_inr: Decimal) -> Decimal:
        estimate = self._non_negative_decimal(
            estimated_cost_inr, "estimated_cost_inr"
        )
        if estimate > self.reserved_total_inr:
            raise ValueError("Cannot release more than the reserved Bodhan cost")
        self.reserved_total_inr -= estimate
        return estimate

    def reconcile(
        self,
        reserved_cost_inr: Decimal,
        actual_cost: CostEstimate,
    ) -> CostEstimate:
        self.release(reserved_cost_inr)

        if actual_cost.known:
            if actual_cost.amount_inr is None:
                raise ValueError("Known cost estimate has no amount")
            service = actual_cost.reason
            if service is not None:
                raise ValueError("Known cost estimate must not carry an unknown reason")

        return actual_cost

    def mark_unknown(self, reason: str, *, service: str | None = None) -> None:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("Unknown-cost reason must not be empty")
        if reason not in self.unknown_costs:
            self.unknown_costs.append(reason)
        if service is not None:
            if service not in self.unknown_attempts:
                raise ValueError("Unknown Bodhan service")
            self.unknown_attempts[service] += 1

    def reset(self) -> None:
        for service in self.totals:
            self.totals[service] = Decimal("0")
            self.calls[service] = 0
            self.unknown_attempts[service] = 0
            self.reconciled_totals[service] = Decimal("0")
            self.estimated_totals[service] = Decimal("0")
        self.unknown_costs.clear()
        self.reserved_total_inr = Decimal("0")

    def report(self) -> dict[str, Any]:
        reconciled_total = sum(
            self.reconciled_totals.values(),
            Decimal("0"),
        )
        estimated_total = sum(
            self.estimated_totals.values(),
            Decimal("0"),
        )
        return {
            "currency": "INR",
            "bodhan_cost_estimate": {
                service: str(amount)
                for service, amount in self.totals.items()
            },
            "bodhan_total_estimate_inr": str(self.bodhan_total_inr),
            "reserved_bodhan_cost_inr": str(self.reserved_total_inr),
            "reconciled_bodhan_cost_inr": str(reconciled_total),
            "reconciled_bodhan_cost_by_service": {
                service: str(amount)
                for service, amount in self.reconciled_totals.items()
            },
            "estimated_bodhan_cost_inr": str(estimated_total),
            "estimated_bodhan_cost_by_service": {
                service: str(amount)
                for service, amount in self.estimated_totals.items()
            },
            "unknown_bodhan_usage_or_cost": list(self.unknown_costs),
            "unknown_attempts_by_service": dict(self.unknown_attempts),
            "calls": dict(self.calls),
            "unknown_costs": list(self.unknown_costs),
        }