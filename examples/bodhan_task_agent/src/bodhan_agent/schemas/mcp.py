from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

AGENT_ERROR_CODES: frozenset[str] = frozenset({"tool_timeout", "server_unavailable", "unknown_tool", "protocol_error"})


class McpModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class McpToolSpec(McpModel):
    name: str = Field(min_length=1)
    description: str = ""
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None


class McpError(McpModel):
    code: str = Field(min_length=1)
    message: str
    status: int | None = None
    hint: str | None = None
    retryable: bool = False
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Server error details; a failed call's usage, when known, is in details.usage.",
    )
    uncertain: bool = Field(default=False, description="A paid request may have run even though no result came back.")

    @property
    def from_server(self) -> bool:
        return self.code not in AGENT_ERROR_CODES


class McpResult(McpModel):
    tool: str = Field(min_length=1)
    ok: bool
    data: dict[str, Any] | None = Field(default=None, description="Structured result of a successful call only.")
    error: McpError | None = None
    latency_ms: float = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _check(self) -> McpResult:
        if self.ok and (self.data is None or self.error is not None):
            raise ValueError("a successful result has data and no error.")
        if not self.ok and (self.error is None or self.data is not None):
            raise ValueError("a failed result has an error and no data; its usage belongs in error.details.")
        return self

    @property
    def usage(self) -> dict[str, Any] | None:
        source = self.data if self.ok else self.error.details if self.error else None
        usage = (source or {}).get("usage")
        return usage if isinstance(usage, dict) else None
