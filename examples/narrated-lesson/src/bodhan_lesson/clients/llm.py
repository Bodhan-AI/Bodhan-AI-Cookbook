"""OpenAI-compatible chat client for the workflow LLM.

Only generic chat-completions features are used (no provider-specific parameters), so
any OpenAI-compatible endpoint works. Compliance guards live here, in code:

* the LLM may never write Indian-language text: every response is scanned for Indic
  and Perso-Arabic script and rejected (`IndicTextError`);
* images may only be sent for an allowed purpose (figure crops), never a whole page.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from bodhan_lesson import config
from bodhan_lesson.utils.retry import backoff_delay

log = logging.getLogger(__name__)

ALLOWED_IMAGE_PURPOSES = frozenset({"figure"})
# Devanagari..Sinhala/Malayalam block, Arabic, Ol Chiki, Meetei Mayek, Arabic presentation forms.
_INDIC_RANGES = ((0x0900, 0x0DFF), (0x0600, 0x06FF), (0x0750, 0x077F), (0x1C50, 0x1C7F),
                 (0xABC0, 0xABFF), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF))
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)
_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class LlmError(RuntimeError):
    """The LLM call failed or returned unusable output. `status` is the HTTP status, if any."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class TruncatedError(LlmError):
    """The model used its whole token budget (often on reasoning) and returned no answer."""


class IndicTextError(LlmError):
    """The LLM wrote Indian-language text, which the task forbids."""


def find_indic(text: str) -> str | None:
    """First character of `text` that belongs to an Indian-language script, if any."""
    for char in text:
        code = ord(char)
        if any(low <= code <= high for low, high in _INDIC_RANGES):
            return char
    return None


def clean_content(content: str) -> str:
    """Remove reasoning blocks some models put inline in the answer."""
    return _THINK_BLOCK.sub("", content).strip()


def extract_json(text: str) -> dict[str, Any]:
    """Parse the JSON object in `text`, tolerating code fences and surrounding prose."""
    stripped = _CODE_FENCE.sub("", text.strip())
    start, end = stripped.find("{"), stripped.rfind("}")
    if start == -1 or end <= start:
        raise LlmError("response contains no JSON object")
    try:
        data = json.loads(stripped[start:end + 1])
    except json.JSONDecodeError as exc:
        raise LlmError(f"response is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise LlmError("response JSON is not an object")
    return data


def text_message(role: str, text: str) -> dict[str, Any]:
    return {"role": role, "content": text}


def image_message(text: str, image_path: Path, *, purpose: str) -> dict[str, Any]:
    """A user message holding `text` and one image. Only figure crops are allowed."""
    if purpose not in ALLOWED_IMAGE_PURPOSES:
        raise ValueError(f"images may only be sent for {sorted(ALLOWED_IMAGE_PURPOSES)}, not {purpose!r}")
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return {"role": "user", "content": [
        {"type": "text", "text": text},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
    ]}


class LlmClient:
    def __init__(
        self,
        settings: config.LlmSettings,
        *,
        http: httpx.Client | None = None,
        cache_dir: Path | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self._http = http or httpx.Client(timeout=config.LLM_TIMEOUT_S)
        self._cache_dir = cache_dir
        self._sleep = sleep
        self.calls = 0  # network calls actually made (cache hits excluded)

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        max_tokens: int = config.LLM_MAX_TOKENS_NARRATION,
        temperature: float = config.LLM_TEMPERATURE,
    ) -> str:
        """Return the assistant text. Responses are cached on disk by content hash."""
        model = model or self.settings.model
        payload = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        if self.settings.reasoning_effort and model == self.settings.model:
            payload["reasoning_effort"] = self.settings.reasoning_effort
        cache_path = self._cache_path(payload)
        if cache_path and cache_path.is_file():
            return self._checked(json.loads(cache_path.read_text(encoding="utf-8"))["content"])
        content = self._checked(self._post(payload))
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps({"model": model, "content": content}, ensure_ascii=False), encoding="utf-8")
        return content

    @staticmethod
    def _checked(content: str) -> str:
        offending = find_indic(content)
        if offending:
            raise IndicTextError(f"LLM wrote Indian-language text (found {offending!r})")
        return content

    def _cache_path(self, payload: dict[str, Any]) -> Path | None:
        if self._cache_dir is None:
            return None
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return self._cache_dir / config.LLM_CACHE_SUBDIR / f"{digest}.json"

    def _post(self, payload: dict[str, Any]) -> str:
        url = f"{self.settings.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.settings.api_key}"}
        last_status: int | None = None
        for attempt in range(config.LLM_MAX_ATTEMPTS):
            try:
                self.calls += 1
                response = self._http.post(url, headers=headers, json=payload)
            except httpx.TransportError as exc:
                log.warning("LLM transport error: %s", exc)
                self._sleep(backoff_delay(attempt, config.LLM_BACKOFF_BASE_S, config.LLM_BACKOFF_MAX_S))
                continue
            if response.status_code == 200:
                return self._content(response.json())
            if response.status_code == 429 or response.status_code >= 500:
                last_status = response.status_code
                log.warning("LLM HTTP %d; retrying", response.status_code)
                self._sleep(self._retry_delay(response, attempt))
                continue
            raise LlmError(f"LLM request rejected (HTTP {response.status_code}): {response.text[:300]}",
                           status=response.status_code)
        raise LlmError(f"LLM unavailable after {config.LLM_MAX_ATTEMPTS} attempts", status=last_status)

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        try:
            return min(float(response.headers["retry-after"]) + 0.5, config.LLM_BACKOFF_MAX_S)
        except (KeyError, ValueError):
            return backoff_delay(attempt, config.LLM_BACKOFF_BASE_S, config.LLM_BACKOFF_MAX_S)

    @staticmethod
    def _content(data: dict[str, Any]) -> str:
        choice = (data.get("choices") or [{}])[0]
        content = clean_content((choice.get("message") or {}).get("content") or "")
        if not content:
            reason = choice.get("finish_reason")
            message = f"LLM returned no content (finish_reason={reason}); the token budget may be too small"
            raise (TruncatedError if reason == "length" else LlmError)(message)
        return content
