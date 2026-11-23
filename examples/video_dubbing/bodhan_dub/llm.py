"""The companion LLM, used for exactly one decision.

The model never touches an Indian-language string. Its single job is to
**shorten English** when a translated segment cannot fit its time slot even at
the maximum allowed speed. The shortened English is then re-translated by
Indic-Translate, so the Indic text still comes from the Bodhan model — which is
the rule the assignment is strictest about.

Everything here is defensive, because an LLM is the least predictable part of the
pipeline:

* output is rejected if it contains any non-Latin character, so a model that
  "helpfully" answers in Hindi cannot leak Indic text into the pipeline;
* output is rejected if any number or protected term from the input went missing;
* output is rejected if it is not actually shorter;
* every failure returns ``None`` and the caller keeps its deterministic result.

The client is only constructed when ``LLM_BASE_URL``, ``LLM_MODEL`` and
``LLM_API_KEY`` are all set. Without them the stage is skipped and logged, and
the duration ladder stops at "keep the overrun and report it".
"""

from __future__ import annotations

import re
from typing import Any

import requests
from requests import exceptions as rq_exceptions

from .config import LLMConfig
from .log import event

SYSTEM_PROMPT = (
    "You shorten English sentences for video dubbing. "
    "Return only the rewritten sentence: no quotes, no explanation, no preamble. "
    "Keep the meaning. Never add information that is not in the original. "
    "Copy every number, name and technical term exactly as written. "
    "Write in English only."
)

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_MAX_LATIN = 0x0530  # anything at or above this is not Latin script


class LLMRefused(Exception):
    """The model's answer was unusable; the caller falls back."""


class LLMClient:
    def __init__(
        self,
        config: LLMConfig,
        *,
        logger: Any = None,
        session: requests.Session | None = None,
        timeout_s: float = 60.0,
        max_attempts: int = 2,
    ) -> None:
        self.config = config
        self.log = logger
        self._session = session or requests.Session()
        self._timeout_s = timeout_s
        self._max_attempts = max(1, max_attempts)

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    # -- public -----------------------------------------------------------

    def condense(
        self,
        text: str,
        *,
        target_chars: int,
        keep: list[str] | None = None,
    ) -> str | None:
        """Return a shorter English version, or None if nothing safe came back."""
        text = text.strip()
        if not text:
            return None
        target = max(20, int(target_chars))
        if len(text) <= target:
            return None

        user = (
            f"Rewrite this so it is at most {target} characters long "
            f"and keeps the same meaning:\n\n{text}"
        )
        try:
            answer = self._chat(user)
        except (LLMRefused, rq_exceptions.RequestException) as exc:
            event(self.log, "llm.refused", level=30, stage="llm", reason=str(exc)[:200])
            return None

        reason = self._rejection_reason(text, answer, target, keep or [])
        if reason:
            event(self.log, "llm.rejected", level=30, stage="llm", reason=reason)
            return None
        return answer

    # -- internals --------------------------------------------------------

    def _chat(self, user: str) -> str:
        url = f"{self.config.base_url.rstrip('/')}/chat/completions"
        body = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
        }
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

        last: Exception | None = None
        for _ in range(self._max_attempts):
            try:
                response = self._session.post(
                    url, headers=headers, json=body, timeout=self._timeout_s
                )
            except rq_exceptions.RequestException as exc:
                last = exc
                continue
            if response.status_code >= 500 or response.status_code == 429:
                last = LLMRefused(f"upstream {response.status_code}")
                continue
            if not response.ok:
                raise LLMRefused(f"{response.status_code}: {response.text[:160]}")
            payload = response.json()
            choices = payload.get("choices") or []
            if not choices:
                raise LLMRefused("no choices in response")
            content = (choices[0].get("message") or {}).get("content")
            if not isinstance(content, str) or not content.strip():
                raise LLMRefused("empty content")
            return content.strip()

        raise LLMRefused(f"transport failed: {last}")

    @staticmethod
    def _rejection_reason(
        original: str, answer: str, target: int, keep: list[str]
    ) -> str | None:
        cleaned = answer.strip().strip('"').strip()
        if not cleaned:
            return "empty answer"
        if any(ord(char) >= _MAX_LATIN for char in cleaned):
            return "answer was not Latin script; refusing to accept non-English text"
        if len(cleaned) > max(target * 1.4, target + 30):
            return f"answer is {len(cleaned)} chars, target was {target}"
        if len(cleaned) >= len(original):
            return "answer was not shorter than the input"

        missing = [value for value in _NUMBER.findall(original) if value not in cleaned]
        if missing:
            return f"dropped number(s): {', '.join(missing[:3])}"
        for term in keep:
            if term and term.casefold() not in cleaned.casefold():
                return f"dropped protected term: {term!r}"
        return None
