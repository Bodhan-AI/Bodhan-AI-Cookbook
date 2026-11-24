"""OpenAI-compatible LLM client wrapper for Bodhan Task Agent.
Reads ONLY:
- LLM_BASE_URL
- LLM_MODEL
- LLM_API_KEY

Enforces exponential backoff retries, function/tool calling, and token tracking.
Strictly calls the configured remote LLM endpoint; never contains or falls back
to offline mocks or simulators. Test doubles are restricted strictly to tests/.
"""

import json
import os
import random
import time
from typing import Any, Dict, List, Optional, Tuple
from openai import OpenAI
from bodhan_mcp.config import settings
from bodhan_mcp.types import ToolError


class LLMClient:
    """Production client for OpenAI-compatible LLM inference (e.g. Gemini, OpenAI)."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: Optional[float] = None,
    ):
        raw_base = base_url or settings.LLM_BASE_URL or ""
        self.base_url = raw_base if raw_base.endswith("/") else (raw_base + "/") if raw_base else ""
        self.model = model or settings.LLM_MODEL or "gemini-flash-lite-latest"
        self.api_key = api_key or settings.LLM_API_KEY or ""
        self.timeout = timeout or settings.REQUEST_TIMEOUT_S

        # Initialize underlying OpenAI client
        self.client = OpenAI(
            base_url=self.base_url if self.base_url else None,
            api_key=self.api_key if self.api_key else "unconfigured",
            timeout=self.timeout,
        )

    def _verify_configuration(self) -> None:
        """Verify that LLM environment configuration is present before calling."""
        if not self.api_key or self.api_key == "unconfigured":
            raise ToolError(
                "MISSING_LLM_CONFIG",
                "LLM_API_KEY is not configured in environment or .env file. "
                "Real LLM calls require a valid LLM_API_KEY.",
            )
        if not self.base_url:
            raise ToolError(
                "MISSING_LLM_CONFIG",
                "LLM_BASE_URL is not configured in environment or .env file.",
            )
        if not self.model:
            raise ToolError(
                "MISSING_LLM_CONFIG",
                "LLM_MODEL is not configured in environment or .env file.",
            )

    def sanitize_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Ensure provider portability:
        If base_url is NOT a Google Gemini endpoint (e.g. OpenAI, Groq, Together, Ollama, DeepSeek),
        strip Gemini-specific fields ('extra_content', 'thought_signature', etc.) so standard
        OpenAI-compatible providers do not reject the payload. If it is Gemini, preserve extra_content.
        """
        is_gemini = "googleapis.com" in self.base_url.lower() or "gemini" in self.base_url.lower()
        if is_gemini:
            return messages

        sanitized: List[Dict[str, Any]] = []
        for m in messages:
            m_copy = dict(m)
            m_copy.pop("extra_content", None)
            if "tool_calls" in m_copy and m_copy["tool_calls"]:
                clean_tcs = []
                for tc in m_copy["tool_calls"]:
                    if isinstance(tc, dict):
                        tc_dict = dict(tc)
                        tc_dict.pop("extra_content", None)
                        clean_tcs.append(tc_dict)
                    else:
                        clean_tcs.append(tc)
                m_copy["tool_calls"] = clean_tcs
            sanitized.append(m_copy)
        return sanitized

    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        temperature: float = 0.1,
        max_retries: int = 4,
    ) -> str:
        """Execute simple text chat completion with exponential backoff retries.

        Fails loudly with ToolError if configuration is missing or if retries fail.
        """
        self._verify_configuration()
        clean_messages = self.sanitize_messages(messages)

        attempt = 0
        backoff = 1.0
        last_error: Optional[Exception] = None

        while attempt < max_retries:
            attempt += 1
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=clean_messages,
                    temperature=temperature,
                )
                if resp.choices:
                    return resp.choices[0].message.content or ""
                return ""
            except Exception as e:
                last_error = e
                if attempt >= max_retries:
                    break
                jitter = random.uniform(0.1, 0.5)
                time.sleep(backoff + jitter)
                backoff *= 2.0

        raise ToolError(
            "LLM_CALL_FAILED",
            f"LLM chat completion to model '{self.model}' at '{self.base_url}' "
            f"failed after {max_retries} attempts: {last_error}",
        )

    def chat_completion_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        tool_choice: str = "auto",
        temperature: float = 0.1,
        max_retries: int = 4,
    ) -> Tuple[Any, Dict[str, int]]:
        """Execute chat completion with function calling and token tracking.

        Fails loudly with ToolError if configuration is missing or if retries fail.
        """
        self._verify_configuration()
        clean_messages = self.sanitize_messages(messages)

        attempt = 0
        backoff = 1.0
        last_error: Optional[Exception] = None

        while attempt < max_retries:
            attempt += 1
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=clean_messages,
                    tools=tools if tools else None,
                    tool_choice=tool_choice if tools else None,
                    temperature=temperature,
                )
                msg = resp.choices[0].message
                usage = {
                    "prompt_tokens": resp.usage.prompt_tokens if resp.usage else 0,
                    "completion_tokens": resp.usage.completion_tokens if resp.usage else 0,
                    "total_tokens": resp.usage.total_tokens if resp.usage else 0,
                }
                return msg, usage
            except Exception as e:
                last_error = e
                if attempt >= max_retries:
                    break
                jitter = random.uniform(0.1, 0.5)
                time.sleep(backoff + jitter)
                backoff *= 2.0

        raise ToolError(
            "LLM_CALL_FAILED",
            f"LLM tool calling to model '{self.model}' at '{self.base_url}' "
            f"failed after {max_retries} attempts: {last_error}",
        )


llm_client = LLMClient()
