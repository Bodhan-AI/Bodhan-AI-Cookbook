from dataclasses import dataclass
from typing import Protocol

import httpx

from .budget import Budget
from .client.bodhan import TranslateResult
from .client.errors import BodhanAPIError, BudgetExhausted
from .config import (
    GENERATED_TRANSLATION_MAX_OUTPUT_TOKENS,
    GENERATED_TRANSLATION_TRUNCATION_RETRIES,
)
from .dataset import DatasetResult


class Translator(Protocol):
    async def translate(
        self,
        text: str,
        target_language: str,
        *,
        script: str = "native",
        max_output_tokens: int | None = None,
    ) -> TranslateResult: ...


@dataclass(frozen=True)
class GenerationOutcome:
    status: str
    attempts: int
    reason: str | None = None
    completion_tokens: int | None = None


def _failure_reason(error: Exception) -> str:
    if isinstance(error, BudgetExhausted):
        return "budget_limit" if error.code == "budget_exhausted" else "provider_credit_exhausted"
    if isinstance(error, BodhanAPIError):
        code = error.code or "unknown"
        return f"api_error:{error.status}:{code}"
    if isinstance(error, httpx.HTTPError):
        return "transport_error"
    if isinstance(error, ValueError):
        return "invalid_response"
    raise TypeError(f"unsupported generation error: {type(error).__name__}")


async def generate_missing_translations(
    result: DatasetResult,
    translator: Translator,
    budget: Budget,
) -> dict[tuple[str, str], GenerationOutcome]:
    outcomes: dict[tuple[str, str], GenerationOutcome] = {}
    provider_credit_exhausted = False

    for item in result.items:
        if item.translation and item.translation.strip():
            continue
        key = (item.id, item.lang)
        if not item.source.strip():
            outcomes[key] = GenerationOutcome("failed", 0, "empty_source")
            continue
        if provider_credit_exhausted:
            outcomes[key] = GenerationOutcome("failed", 0, "provider_credit_exhausted")
            continue

        max_tokens = GENERATED_TRANSLATION_MAX_OUTPUT_TOKENS
        max_attempts = GENERATED_TRANSLATION_TRUNCATION_RETRIES + 1
        for attempt in range(1, max_attempts + 1):
            estimated_cost = budget.estimate_translate(max_tokens)
            if not budget.can_afford(estimated_cost):
                budget.record_skip("generated_translation_budget_limit", estimated_cost)
                outcomes[key] = GenerationOutcome("failed", attempt - 1, "budget_limit")
                break
            try:
                translated = await translator.translate(
                    item.source,
                    item.lang,
                    max_output_tokens=max_tokens,
                )
            except (BudgetExhausted, BodhanAPIError, httpx.HTTPError, ValueError) as error:
                reason = _failure_reason(error)
                if reason == "provider_credit_exhausted":
                    provider_credit_exhausted = True
                if reason == "budget_limit":
                    budget.record_skip("generated_translation_budget_limit", estimated_cost)
                outcomes[key] = GenerationOutcome("failed", attempt, reason)
                break

            if translated.truncated:
                if attempt < max_attempts:
                    max_tokens *= 2
                    continue
                outcomes[key] = GenerationOutcome("failed", attempt, "translation_truncated")
                break
            if not translated.text.strip():
                outcomes[key] = GenerationOutcome("failed", attempt, "empty_translation")
                break

            item.translation = translated.text
            completion_tokens = translated.usage.get("completion_tokens")
            outcomes[key] = GenerationOutcome(
                "generated",
                attempt,
                completion_tokens=(
                    completion_tokens if isinstance(completion_tokens, int) else None
                ),
            )
            break
    return outcomes


def attach_generation_outcomes(
    records: list[dict],
    outcomes: dict[tuple[str, str], GenerationOutcome],
) -> None:
    for record in records:
        key = (record["id"], record["lang"])
        outcome = outcomes.get(key)
        if outcome is None:
            continue
        record["generation"] = {
            "status": outcome.status,
            "attempts": outcome.attempts,
            "reason": outcome.reason,
            "completion_tokens": outcome.completion_tokens,
        }
        record["decisions"].insert(
            0,
            {
                "check": "translation_generation",
                "reason": (
                    "generated with Bodhan Indic-Translate"
                    if outcome.status == "generated"
                    else f"generation_failed:{outcome.reason}"
                ),
            },
        )
        if outcome.status == "failed":
            record["terminal_state"] = "failed_checks"
            record["skipped_reason"] = None
            record["failed_checks"] = [f"generation:{outcome.reason}"]
            record["checks_run"] = []
            record["flags"] = []
            record["reference_scores"] = None
