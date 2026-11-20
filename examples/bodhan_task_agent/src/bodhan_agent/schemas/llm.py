from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bodhan_agent.schemas.trace import LlmTokens

FinishReason = Literal["stop", "tool_calls", "length", "content_filter", "other"]


class LlmModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolCall(LlmModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    raw_arguments: str = Field(description="Arguments exactly as the model sent them.")
    arguments: dict[str, Any] | None = Field(default=None, description="Parsed arguments; null when parsing failed.")
    parse_error: str | None = Field(default=None, description="Why the arguments could not be parsed as a JSON object.")

    @model_validator(mode="after")
    def _check(self) -> ToolCall:
        if (self.arguments is None) == (self.parse_error is None):
            raise ValueError("a tool call has either parsed arguments or a parse_error, not both or neither.")
        return self


class LlmReply(LlmModel):
    content: str | None = Field(default=None, description="Visible assistant text.")
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tokens: LlmTokens = Field(default_factory=LlmTokens)
    finish_reason: FinishReason = "other"
    model: str | None = None
    latency_ms: float = Field(ge=0, allow_inf_nan=False)
    attempts: int = Field(ge=1)
    dropped_params: list[str] = Field(
        default_factory=list,
        description="Optional request settings removed because the provider rejected them.",
    )

    @model_validator(mode="after")
    def _check(self) -> LlmReply:
        if not self.tool_calls and not (self.content and self.content.strip()):
            raise ValueError("LLM reply has neither tool calls nor usable content.")
        ids = [call.id for call in self.tool_calls]
        if len(ids) != len(set(ids)):
            raise ValueError("LLM reply contains duplicate tool_call ids.")
        return self

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)

    @property
    def truncated(self) -> bool:
        return self.finish_reason == "length"

    def assistant_message(self) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [
                {"id": call.id, "type": "function", "function": {"name": call.name, "arguments": call.raw_arguments}}
                for call in self.tool_calls
            ]
        return message
