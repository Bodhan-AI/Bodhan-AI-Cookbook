from dataclasses import dataclass, field
from typing import Any

from .config import COSTS


class BudgetExceeded(Exception):
    pass


@dataclass
class Budget:
    max_inr: float
    spent: float = 0.0
    ledger: list[dict[str, Any]] = field(default_factory=list)
    skips: list[dict[str, Any]] = field(default_factory=list)

    def estimate_translate(self, output_tokens: int) -> float:
        return output_tokens * COSTS["translate"]

    def estimate_speak(self, characters: int) -> float:
        return characters * COSTS["speak"]

    def estimate_transcribe(self, seconds: float) -> float:
        return seconds * COSTS["transcribe"]

    def can_afford(self, cost: float) -> bool:
        return cost >= 0 and self.spent + cost <= self.max_inr + 1e-9

    def charge(self, cost: float, stage: str) -> bool:
        if not self.can_afford(cost):
            return False
        self.spent += cost
        self.spent = round(self.spent, 9)
        self.ledger.append({"stage": stage, "cost_inr": round(cost, 9)})
        return True

    def record_skip(self, reason: str, estimated_savings: float) -> None:
        self.skips.append({
            "reason": reason,
            "estimated_savings_inr": round(max(0.0, estimated_savings), 9),
        })

    def degradation_order(self) -> tuple[str, ...]:
        return (
            "skip_speech_generation",
            "reduce_speech_sample",
            "skip_back_transcription_lowest_priority",
            "skip_judge_confirmation",
            "finish_with_free_checks",
        )
