from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Recovery = Literal["retryable", "fixable", "fatal"]


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = Field(min_length=1)
    ok: bool
    content: str = Field(min_length=1, description="Full observation sent to the LLM as the tool message.")
    short: str = Field(min_length=1, description="One-line form used once the observation is old history.")
    artifacts: list[str] = Field(default_factory=list, description="Ledger ids mentioned in the observation.")
    error_code: str | None = None
    recovery: Recovery | None = None
    redactions: int = Field(default=0, ge=0, description="Indic passages hidden, counted once at creation.")
    injection_flags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> Observation:
        if self.ok and (self.error_code is not None or self.recovery is not None):
            raise ValueError("a successful observation has no error_code or recovery.")
        if not self.ok and (self.error_code is None or self.recovery is None):
            raise ValueError("a failed observation needs error_code and recovery.")
        if "\n" in self.short or "\r" in self.short:
            raise ValueError("short must be a single line.")
        return self
