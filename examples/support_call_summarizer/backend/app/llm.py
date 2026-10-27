"""What the call analysis needs from an LLM provider (Sarvam, Claude).

Each provider client turns OpenAI-style ``messages`` plus a ``json_schema``
response format into its own API call, and raises ``LLMError`` on failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class LLMError(Exception):
    def __init__(self, status: int | None, code: str, message: str, request_id: str | None = None):
        super().__init__(f"{code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.request_id = request_id

    @property
    def is_auth_error(self) -> bool:
        return self.status in (401, 403) or self.code in {
            "invalid_api_key_error",
            "authentication_error",
            "permission_error",
            "missing_api_key",
        }

    @property
    def is_out_of_credit(self) -> bool:
        return self.status == 402 or self.code in {"insufficient_quota_error", "billing_error"}


@dataclass(frozen=True)
class ChatResult:
    content: str
    truncated: bool


class ChatClient(Protocol):
    provider: str
    model: str

    async def chat(self, messages: list[dict], *, response_format: dict | None = None) -> ChatResult: ...

    async def aclose(self) -> None: ...
