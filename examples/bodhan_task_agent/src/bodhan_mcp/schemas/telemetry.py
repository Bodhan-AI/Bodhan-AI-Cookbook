from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True)
class CallRecord:
    model: str
    endpoint: str
    started_at: str
    latency_ms: float
    attempts: int
    outcome: Literal["ok", "error"]
    http_status: int | None
    error_code: str | None
    request_id: str | None
    estimated_cost_inr: float | None
    billing_uncertain: bool
    possibly_billed_failed_attempts: int
    rate_limit_wait_s: float
    cached: bool = False


class UsageSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_calls: int = Field(default=0, ge=0, description="Logical Bodhan model calls made by this tool invocation.")
    http_attempts: int = Field(default=0, ge=0, description="HTTP attempts across those calls, including retries.")
    estimated_cost_inr: float = Field(
        default=0.0,
        ge=0,
        allow_inf_nan=False,
        description=(
            "Sum of known per-call estimates at published Bodhan rates, in rupees. "
            "Excludes costs that could not be determined; see billing_uncertain."
        ),
    )
    billing_uncertain: bool = Field(
        default=False,
        description="True when some cost is unknown or a failed attempt may also have been billed.",
    )
    possibly_billed_failed_attempts: int = Field(
        default=0,
        ge=0,
        description="Failed attempts that reached Bodhan and may have been billed.",
    )
    cache_hits: int = Field(
        default=0,
        ge=0,
        description="Model calls answered from the result cache instead of Bodhan, at no cost.",
    )
    latency_ms: float = Field(
        default=0.0,
        ge=0,
        allow_inf_nan=False,
        description="Wall-clock time of the tool invocation in milliseconds.",
    )
