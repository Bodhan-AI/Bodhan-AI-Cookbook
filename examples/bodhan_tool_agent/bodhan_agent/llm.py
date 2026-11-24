
from __future__ import annotations

from typing import Any

import requests
from bodhan_tool_agent.config import ConfigurationError, Settings


class LLMError(RuntimeError):
    """Raised when the configured planning LLM cannot complete a request."""


class LLMClient:
    """Small OpenAI-compatible chat-completions client."""

    def __init__(
        self,
        settings: Settings,
        *,
        session: requests.Session | None = None,
    ) -> None:
        self.settings = settings
        self.session = session or requests.Session()
        self.last_usage: dict[str, Any] | None = None

        if not settings.llm_base_url:
            raise ConfigurationError("Set LLM_BASE_URL in the environment")
        if not settings.llm_model:
            raise ConfigurationError("Set LLM_MODEL in the environment")
        if not settings.llm_api_key:
            raise ConfigurationError("Set LLM_API_KEY in the environment")

        self.base_url = settings.llm_base_url.rstrip("/")
        self.model = settings.llm_model

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        self.last_usage = None

        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
        }

        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        request_timeout = self.settings.timeout
        if timeout_seconds is not None:
            request_timeout = min(request_timeout, timeout_seconds)
        if request_timeout <= 0:
            raise LLMError("The planning LLM deadline has expired")

        try:
            response = self.session.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.settings.llm_api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=request_timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise LLMError("The planning LLM request failed") from exc
        except ValueError as exc:
            raise LLMError("The planning LLM returned invalid JSON") from exc

        if not isinstance(payload, dict):
            raise LLMError("The planning LLM returned an invalid response")

        usage = payload.get("usage")
        if isinstance(usage, dict):
            self.last_usage = usage.copy()

        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMError("The planning LLM response contained no choices")

        choice = choices[0]
        if not isinstance(choice, dict):
            raise LLMError("The planning LLM returned an invalid choice")

        message = choice.get("message")
        if not isinstance(message, dict):
            raise LLMError("The planning LLM response contained no message")

        return message
