"""
Cost tracking ledger and budget guard.
Tracks rupee costs per model and token metrics, with pre-call budget checks.
"""

from __future__ import annotations

import asyncio
from typing import Dict, Optional

from pydantic import BaseModel


class BudgetExceededError(Exception):
    """Raised when an operation would exceed the configured max-cost-inr budget."""

    pass


class CostBreakdown(BaseModel):
    transcribe_inr: float = 0.0
    translate_inr: float = 0.0
    speak_inr: float = 0.0
    ocr_inr: float = 0.0
    total_inr: float = 0.0

    # Usage counters
    audio_minutes_transcribed: float = 0.0
    tokens_translated: int = 0
    chars_synthesized: int = 0
    images_ocr: int = 0

    # LLM metrics (tracked separately per spec)
    llm_prompt_tokens: int = 0
    llm_completion_tokens: int = 0
    llm_total_tokens: int = 0

    # Cache metrics
    cache_hits: int = 0
    cost_saved_inr: float = 0.0


class CostLedger:
    """Thread-safe cost tracking ledger."""

    def __init__(
        self,
        cost_asr_per_min: float = 0.10,
        cost_translate_per_10k: float = 0.20,
        cost_tts_per_10k: float = 6.00,
        cost_ocr_per_image: float = 0.20,
    ):
        self.cost_asr_per_min = cost_asr_per_min
        self.cost_translate_per_10k = cost_translate_per_10k
        self.cost_tts_per_10k = cost_tts_per_10k
        self.cost_ocr_per_image = cost_ocr_per_image

        self.breakdown = CostBreakdown()
        self._lock = asyncio.Lock()

    def calculate_transcribe_cost(self, duration_seconds: float) -> float:
        minutes = max(0.0, duration_seconds) / 60.0
        return round(minutes * self.cost_asr_per_min, 4)

    def calculate_translate_cost(self, output_tokens: int) -> float:
        return round((max(0, output_tokens) / 10000.0) * self.cost_translate_per_10k, 4)

    def calculate_speak_cost(self, char_count: int) -> float:
        return round((max(0, char_count) / 10000.0) * self.cost_tts_per_10k, 4)

    async def record_transcribe(self, duration_seconds: float, from_cache: bool = False) -> float:
        cost = self.calculate_transcribe_cost(duration_seconds)
        async with self._lock:
            if from_cache:
                self.breakdown.cache_hits += 1
                self.breakdown.cost_saved_inr += cost
                return 0.0
            self.breakdown.audio_minutes_transcribed += duration_seconds / 60.0
            self.breakdown.transcribe_inr += cost
            self.breakdown.total_inr += cost
        return cost

    async def record_translate(self, output_tokens: int, from_cache: bool = False) -> float:
        cost = self.calculate_translate_cost(output_tokens)
        async with self._lock:
            if from_cache:
                self.breakdown.cache_hits += 1
                self.breakdown.cost_saved_inr += cost
                return 0.0
            self.breakdown.tokens_translated += output_tokens
            self.breakdown.translate_inr += cost
            self.breakdown.total_inr += cost
        return cost

    async def record_speak(self, char_count: int, from_cache: bool = False) -> float:
        cost = self.calculate_speak_cost(char_count)
        async with self._lock:
            if from_cache:
                self.breakdown.cache_hits += 1
                self.breakdown.cost_saved_inr += cost
                return 0.0
            self.breakdown.chars_synthesized += char_count
            self.breakdown.speak_inr += cost
            self.breakdown.total_inr += cost
        return cost

    async def record_llm_tokens(self, prompt_tokens: int, completion_tokens: int) -> None:
        async with self._lock:
            self.breakdown.llm_prompt_tokens += prompt_tokens
            self.breakdown.llm_completion_tokens += completion_tokens
            self.breakdown.llm_total_tokens += prompt_tokens + completion_tokens

    async def get_total_inr(self) -> float:
        async with self._lock:
            return round(self.breakdown.total_inr, 4)

    def get_summary_sync(self) -> Dict[str, float]:
        return {
            "transcribe_inr": round(self.breakdown.transcribe_inr, 4),
            "translate_inr": round(self.breakdown.translate_inr, 4),
            "speak_inr": round(self.breakdown.speak_inr, 4),
            "total_inr": round(self.breakdown.total_inr, 4),
            "total_cost_inr": round(self.breakdown.total_inr, 4),
            "cache_hits": self.breakdown.cache_hits,
            "cost_saved_inr": round(self.breakdown.cost_saved_inr, 4),
            "llm_prompt_tokens": self.breakdown.llm_prompt_tokens,
            "llm_completion_tokens": self.breakdown.llm_completion_tokens,
            "llm_total_tokens": self.breakdown.llm_total_tokens,
        }

    async def get_summary(self) -> Dict[str, float]:
        async with self._lock:
            return self.get_summary_sync()


class BudgetGuard:
    """Pre-call budget gate preventing spend beyond max_cost_inr with reservation support."""

    def __init__(self, ledger: CostLedger, max_cost_inr: Optional[float] = None):
        self.ledger = ledger
        self.max_cost_inr = max_cost_inr
        self.reserved_inr: float = 0.0
        self._lock = asyncio.Lock()

    def set_budget(self, max_cost_inr: Optional[float]) -> None:
        self.max_cost_inr = max_cost_inr

    async def check_budget(self, estimated_next_cost: float = 0.0) -> None:
        """Raises BudgetExceededError if current spend + reserved + estimated next call exceeds budget."""
        if self.max_cost_inr is None or self.max_cost_inr <= 0.0:
            return

        current_total = await self.ledger.get_total_inr()
        async with self._lock:
            effective_total = current_total + self.reserved_inr + estimated_next_cost
            if effective_total > self.max_cost_inr:
                raise BudgetExceededError(
                    f"Budget limit exceeded! Current spend: ₹{current_total:.2f}, "
                    f"Reserved: ₹{self.reserved_inr:.2f}, "
                    f"Attempted call estimate: ₹{estimated_next_cost:.2f}, "
                    f"Budget ceiling: ₹{self.max_cost_inr:.2f}."
                )

    def reserve(self, estimated_cost: float):
        """C2: Context manager reserving estimated cost before call and reconciling after."""
        class _ReservationContext:
            def __init__(self, guard: BudgetGuard, amount: float):
                self.guard = guard
                self.amount = amount

            async def __aenter__(self):
                if self.guard.max_cost_inr is not None and self.guard.max_cost_inr > 0.0:
                    current_total = await self.guard.ledger.get_total_inr()
                    async with self.guard._lock:
                        effective_total = current_total + self.guard.reserved_inr + self.amount
                        if effective_total > self.guard.max_cost_inr:
                            raise BudgetExceededError(
                                f"Budget limit exceeded! Current spend: ₹{current_total:.2f}, "
                                f"Reserved: ₹{self.guard.reserved_inr:.2f}, "
                                f"Attempted call estimate: ₹{self.amount:.2f}, "
                                f"Budget ceiling: ₹{self.guard.max_cost_inr:.2f}."
                            )
                        self.guard.reserved_inr += self.amount
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                if self.guard.max_cost_inr is not None and self.guard.max_cost_inr > 0.0:
                    async with self.guard._lock:
                        self.guard.reserved_inr = max(0.0, self.guard.reserved_inr - self.amount)

        return _ReservationContext(self, estimated_cost)
