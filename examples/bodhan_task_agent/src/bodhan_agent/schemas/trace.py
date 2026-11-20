from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Phase = Literal["preflight", "plan", "gate", "act", "verify", "finalize"]
EventKind = Literal[
    "preflight", "plan", "gate", "llm", "tool", "guard", "verify", "budget", "assumption", "uncertain_retry",
    "finish", "stop",
]  # fmt: skip

TOOL_KINDS: frozenset[str] = frozenset({"tool", "uncertain_retry"})


class TraceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LlmTokens(TraceModel):
    prompt: int | None = Field(default=None, ge=0, description="Prompt tokens; null when the provider did not say.")
    completion: int | None = Field(default=None, ge=0, description="Completion tokens; null when unknown.")

    @property
    def total(self) -> int | None:
        if self.prompt is None or self.completion is None:
            return None
        return self.prompt + self.completion


class TraceError(TraceModel):
    code: str = Field(min_length=1)
    message: str
    hint: str | None = None
    retryable: bool | None = None


class TraceEvent(TraceModel):
    seq: int = Field(ge=0, description="Position of the event in the run, starting at 0.")
    ts: datetime = Field(description="When the event was recorded, timezone-aware.")
    phase: Phase
    kind: EventKind
    message: str | None = Field(default=None, description="Brief decision rationale or action summary, in English.")
    tool_call_id: str | None = None
    tool: str | None = None
    reason: str | None = Field(default=None, description="The LLM's stated reason for a tool call.")
    plan_step: str | None = None
    args: dict[str, Any] | None = Field(default=None, description="Arguments as sent.")
    result_summary: dict[str, Any] | None = Field(
        default=None,
        description="Key result fields and paths; never full transcripts, translations or OCR text.",
    )
    error: TraceError | None = None
    artifacts: list[str] = Field(default_factory=list, description="Ledger ids created or used by this event.")
    latency_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    cost_inr: float = Field(default=0.0, ge=0, allow_inf_nan=False, description="Bodhan cost of this event.")
    cum_cost_inr: float = Field(default=0.0, ge=0, allow_inf_nan=False, description="Bodhan cost so far in the run.")
    llm_tokens: LlmTokens | None = None

    @field_validator("ts")
    @classmethod
    def _check_ts(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("ts must be timezone-aware.")
        return value

    @model_validator(mode="after")
    def _check(self) -> TraceEvent:
        if self.kind in TOOL_KINDS and not self.tool:
            raise ValueError(f"a '{self.kind}' event must name its tool.")
        if self.kind == "llm" and self.llm_tokens is None:
            raise ValueError("an 'llm' event must carry llm_tokens, with null counts when usage is unknown.")
        if self.cum_cost_inr + 1e-9 < self.cost_inr:
            raise ValueError("cum_cost_inr cannot be lower than cost_inr.")
        return self

    def to_line(self) -> str:
        return self.model_dump_json(exclude_none=True)
