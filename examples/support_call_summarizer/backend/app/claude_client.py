"""Every call to the Anthropic (Claude) API lives in this file.

Used as the fallback LLM for the call analysis when Sarvam fails. It takes the
same OpenAI-style ``messages`` and ``json_schema`` response format as the Sarvam
client and maps them onto the Messages API: the system message becomes
``system`` and the schema becomes ``output_config.format``. Claude Opus 5.5
always thinks; ``effort`` sets how much. A refused request is retried on another
model by the server (``fallbacks: "default"``). The SDK retries 429, 5xx and
network errors itself.
"""

from __future__ import annotations

import logging

import anthropic

from .llm import ChatResult, LLMError

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ClaudeClient:
    provider = "Claude"

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_MODEL,
        effort: str = "medium",
        max_tokens: int = 16000,
        timeout: float = 120.0,
        max_retries: int = 4,
        client: anthropic.AsyncAnthropic | None = None,
    ):
        self.model = model
        self._key = api_key
        self._effort = effort
        self._max_tokens = max_tokens
        self._client = client or anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout, max_retries=max_retries)

    async def aclose(self) -> None:
        await self._client.close()

    async def chat(self, messages: list[dict], *, response_format: dict | None = None) -> ChatResult:
        if not self._key:
            raise LLMError(None, "missing_api_key", "No Anthropic API key is set.")
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        turns = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] != "system"]
        output_config: dict = {"effort": self._effort}
        if response_format and response_format.get("type") == "json_schema":
            output_config["format"] = {"type": "json_schema", "schema": response_format["json_schema"]["schema"]}

        try:
            response = await self._client.beta.messages.create(
                model=self.model,
                max_tokens=self._max_tokens,
                system=system,
                messages=turns,
                output_config=output_config,
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.APIConnectionError as exc:  # includes APITimeoutError
            code = "timeout" if isinstance(exc, anthropic.APITimeoutError) else "network_error"
            raise LLMError(None, code, f"Could not reach Anthropic ({type(exc).__name__}).") from exc
        except anthropic.APIStatusError as exc:
            raise _status_error(exc) from exc

        log.info(
            "claude %s stop=%s id=%s tokens=%s/%s",
            response.model,
            response.stop_reason,
            response._request_id,
            response.usage.input_tokens,
            response.usage.output_tokens,
        )
        if response.stop_reason == "refusal":
            raise LLMError(200, "refusal", "Claude declined to analyse this call.", response._request_id)
        # after a server-side fallback, only the text after the last switch belongs to the final answer
        blocks = list(response.content)
        last_switch = max((i for i, b in enumerate(blocks) if b.type == "fallback"), default=-1)
        text = "".join(b.text for b in blocks[last_switch + 1 :] if b.type == "text").strip()
        return ChatResult(content=text, truncated=response.stop_reason == "max_tokens")


def _status_error(exc: anthropic.APIStatusError) -> LLMError:
    err = exc.body.get("error") if isinstance(exc.body, dict) else None
    code = str(err.get("type")) if isinstance(err, dict) and err.get("type") else f"http_{exc.status_code}"
    message = str(err.get("message")) if isinstance(err, dict) and err.get("message") else str(exc.message)
    if "credit balance" in message.lower():
        code = "billing_error"
    return LLMError(exc.status_code, code, message, exc.request_id)
