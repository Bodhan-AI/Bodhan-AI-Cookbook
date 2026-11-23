"""The general-purpose LLM 'brain'.

Strictly text-side orchestration: build a terminology glossary and condense the
ENGLISH source when a dub overruns its slot. It never writes Indian-language
text, never transcribes, never synthesizes — those are Bodhan-only. Target text
always comes back from Indic-Translate re-running on the edited English.
"""

from __future__ import annotations

import json
import re

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from ..logging_setup import get_logger

log = get_logger("llm")


def _is_retryable(e: BaseException) -> bool:
    """Retry only transient failures — transport errors, timeouts, 429, and 5xx.

    4xx (auth/bad-request) are permanent, so we fail fast and let the caller fall
    back to its deterministic path instead of burning the runtime budget on retries.
    """
    if isinstance(e, httpx.TransportError):  # includes TimeoutException
        return True
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        return code == 429 or code >= 500
    return False

_GLOSSARY_SYS = (
    "You extract a terminology glossary from an English transcript that will be "
    "translated into Indian languages. Return STRICT JSON: a list of objects with "
    "keys 'term' and 'keep' (true if the term, e.g. a name, acronym, number, or "
    "technical word, should be rendered consistently across the whole video). "
    "Do not translate anything. JSON only, no prose."
)

_CONDENSE_SYS = (
    "You shorten English narration so its spoken translation fits a fixed time "
    "slot. Preserve every fact, name, number, and technical term; drop only "
    "filler and redundancy. Keep it natural and fluent. Return ONLY the shortened "
    "English text, no quotes or commentary. Aim for about {pct}% of the original length."
)


class LLMBrain:
    def __init__(self, settings):
        self.s = settings

    async def _chat(self, system: str, user: str, *, temperature: float = 0.2) -> str:
        # Build the full URL explicitly: httpx base_url joining mangles bases that carry
        # a path (e.g. OpenRouter's /api/v1), so we don't rely on it. Body uses only
        # standard chat-completions fields — no provider-only features.
        url = f"{self.s.llm_base_url}/chat/completions"
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": user}]

        @retry(retry=retry_if_exception(_is_retryable),
               wait=wait_exponential_jitter(max=15), stop=stop_after_attempt(4))
        async def _call(include_temperature: bool) -> str:
            body: dict = {"model": self.s.llm_model, "messages": messages}
            if include_temperature:
                body["temperature"] = temperature
            async with httpx.AsyncClient(timeout=90.0) as c:
                r = await c.post(
                    url, json=body,
                    headers={"Authorization": f"Bearer {self.s.llm_api_key}"})
                r.raise_for_status()
                content = r.json()["choices"][0]["message"].get("content") or ""
                return content.strip()

        try:
            return await _call(True)
        except httpx.HTTPStatusError as e:
            # Some reasoning models (o1/o3-style) reject a non-default temperature with a
            # 400; retry once with the fully standard body before giving up.
            if e.response.status_code == 400:
                return await _call(False)
            raise

    # ---- glossary ----
    async def build_glossary(self, transcript: str) -> dict[str, str]:
        """Return {term: canonical_form} the translate stage should keep consistent."""
        if self.s.mock or not self.s.llm_base_url:
            return self._mock_glossary(transcript)
        try:
            raw = await self._chat(_GLOSSARY_SYS, transcript[:12000])
            items = json.loads(_strip_fence(raw))
            return {str(it["term"]): str(it["term"]) for it in items if it.get("keep", True)}
        except Exception:  # glossary is best-effort; never fail the run over it
            log.warning("glossary build failed, falling back to heuristic", extra={"event": "glossary_fallback"})
            return self._mock_glossary(transcript)

    # ---- condensing (English side only) ----
    async def condense(self, text: str, target_ratio: float, keep_hint: str = "") -> str:
        """Shorten English to ~target_ratio of its length, preserving meaning."""
        target_ratio = max(0.5, min(0.95, target_ratio))
        if self.s.mock or not self.s.llm_base_url:
            return self._mock_condense(text, target_ratio)
        try:
            sys = _CONDENSE_SYS.format(pct=int(target_ratio * 100))
            user = f"{keep_hint}\n\n{text}" if keep_hint else text
            out = await self._chat(sys, user, temperature=0.3)
            out = _strip_fence(out).strip().strip('"')
            return out or self._mock_condense(text, target_ratio)
        except Exception:
            return self._mock_condense(text, target_ratio)

    # ---- deterministic fallbacks (also used in --mock) ----
    @staticmethod
    def _mock_glossary(transcript: str) -> dict[str, str]:
        terms: dict[str, str] = {}
        for m in re.findall(r"\b[A-Z][A-Za-z0-9]{2,}\b", transcript):
            terms[m] = m
        for m in re.findall(r"\b\d+(?:\.\d+)?\b", transcript):
            terms[m] = m
        return terms

    @staticmethod
    def _mock_condense(text: str, target_ratio: float) -> str:
        """Drop trailing clauses/words to approximate the target length."""
        words = text.split()
        keep = max(1, int(len(words) * target_ratio))
        if keep >= len(words):
            return text
        trimmed = " ".join(words[:keep]).rstrip(",;:- ")
        if not trimmed.endswith((".", "!", "?")):
            trimmed += "."
        return trimmed


def _strip_fence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s)
    return s.strip()
