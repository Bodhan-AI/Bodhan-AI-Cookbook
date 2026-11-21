"""OpenAI-compatible async LLM wrapper with JSON-mode fallback and message recording for CaMeL auditing."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
from typing import Any, Protocol

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)

logger = logging.getLogger("bodhan_agent.llm")


class LLMProtocol(Protocol):
    recorded_calls: list[dict[str, Any]]

    async def complete_json(
        self,
        *,
        role_tag: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
    ) -> dict[str, Any]: ...

    async def complete_text(
        self,
        *,
        role_tag: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
    ) -> str: ...


def extract_first_json_object(raw: str) -> dict[str, Any]:
    """Extract and parse the first top-level JSON object from an LLM response string."""
    text = raw.strip()
    # Strip markdown code fences if present.
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # Balanced-brace scanner for first valid JSON object.
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    try:
                        parsed = json.loads(candidate)
                        if isinstance(parsed, dict):
                            return parsed
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)

    raise ValueError(f"Could not extract a valid JSON object from LLM response: {raw[:200]!r}")


class OpenAICompatibleLLM:
    """OpenAI-compatible client reading LLM_BASE_URL, LLM_MODEL, LLM_API_KEY.

    Avoids provider-only features: tries response_format={"type":"json_object"} and
    transparently falls back to plain JSON extraction if the provider rejects it.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout_s: float = 150.0,
        max_retries: int = 3,
        base_backoff_s: float = 2.0,
    ) -> None:
        self.base_url = (base_url or os.environ.get("LLM_BASE_URL") or "").strip()
        self.model = (model or os.environ.get("LLM_MODEL") or "").strip()
        self.api_key = (api_key or os.environ.get("LLM_API_KEY") or "").strip()
        self.timeout_s = max(120.0, float(timeout_s)) if timeout_s > 0 else 150.0
        self.max_retries = max_retries
        self.base_backoff_s = base_backoff_s
        self.recorded_calls: list[dict[str, Any]] = []
        self._json_mode_supported = True
        self._client: AsyncOpenAI | None = None

    def is_configured(self) -> bool:
        return bool(self.model and self.api_key)

    def _get_client(self) -> AsyncOpenAI:
        if not self.is_configured():
            raise RuntimeError(
                "LLM credentials not configured. Set LLM_BASE_URL, LLM_MODEL, and LLM_API_KEY."
            )
        if self._client is None:
            kwargs: dict[str, Any] = {
                "api_key": self.api_key,
                "timeout": self.timeout_s,
                "max_retries": 0,  # Retries handled explicitly with backoff/jitter below.
            }
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    async def _create_with_retry(self, **kwargs: Any) -> Any:
        client = self._get_client()
        for attempt in range(self.max_retries + 1):
            try:
                return await client.chat.completions.create(**kwargs)
            except (RateLimitError, InternalServerError, APITimeoutError, APIConnectionError) as exc:
                if attempt >= self.max_retries:
                    raise
                delay = self.base_backoff_s * (2**attempt) + random.uniform(0, 0.5)
                resp = getattr(exc, "response", None)
                if resp is not None:
                    ra = resp.headers.get("retry-after")
                    if ra and ra.replace(".", "", 1).isdigit():
                        delay = min(float(ra), 45.0)
                logger.warning("Transient LLM error (%s); retry %d in %.1fs", type(exc).__name__, attempt + 1, delay)
                await asyncio.sleep(delay)
            except APIStatusError as exc:
                if exc.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    delay = self.base_backoff_s * (2**attempt) + random.uniform(0, 0.5)
                    logger.warning("LLM HTTP %d; retry %d in %.1fs", exc.status_code, attempt + 1, delay)
                    await asyncio.sleep(delay)
                    continue
                raise

    async def complete_json(
        self,
        *,
        role_tag: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        self.recorded_calls.append(
            {
                "role_tag": role_tag,
                "mode": "json",
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
            }
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        raw_content = ""
        if self._json_mode_supported:
            try:
                resp = await self._create_with_retry(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    response_format={"type": "json_object"},
                )
                raw_content = resp.choices[0].message.content or ""
                return extract_first_json_object(raw_content)
            except BadRequestError:
                # Provider does not support response_format json_object -> fall back cleanly.
                self._json_mode_supported = False
            except ValueError:
                # Response arrived but was not valid JSON -> repair directly without repeating full prompt.
                repair_messages = [
                    {"role": "system", "content": "Return ONLY a valid JSON object matching the schema. No prose."},
                    {"role": "user", "content": f"Fix this into valid JSON:\n{raw_content[:3000]}"},
                ]
                repair_resp = await self._create_with_retry(
                    model=self.model,
                    messages=repair_messages,
                    temperature=0.0,
                )
                return extract_first_json_object(repair_resp.choices[0].message.content or "")

        # Fallback: plain completion + JSON extraction + 1 repair retry if malformed.
        resp = await self._create_with_retry(
            model=self.model,
            messages=messages,
            temperature=temperature,
        )
        raw_content = resp.choices[0].message.content or ""
        try:
            return extract_first_json_object(raw_content)
        except ValueError:
            repair_messages = [
                {"role": "system", "content": "Return ONLY a valid JSON object matching the user's schema. No prose."},
                {"role": "user", "content": f"Fix this into valid JSON:\n{raw_content[:3000]}"},
            ]
            repair_resp = await self._create_with_retry(
                model=self.model,
                messages=repair_messages,
                temperature=0.0,
            )
            return extract_first_json_object(repair_resp.choices[0].message.content or "")

    async def complete_text(
        self,
        *,
        role_tag: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
    ) -> str:
        self.recorded_calls.append(
            {
                "role_tag": role_tag,
                "mode": "text",
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
            }
        )
        resp = await self._create_with_retry(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
        )
        return (resp.choices[0].message.content or "").strip()
