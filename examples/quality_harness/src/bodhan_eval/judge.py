"""LLM judge: a blind, rubric-driven pass over (source, translation) that returns categorized,
localized, verbatim-quoted errors.

Reliability measures baked in:
- temperature 0 (+ seed when supported) and on-disk caching keyed by prompt + repeat index.
- The judge NEVER provides offsets; the harness locates each verbatim quote itself and
  discards quotes that are not exact substrings (guards against the LLM inventing text).
- `--judge-repeats N` re-runs the judge and the pipeline reports verdict/category agreement.
The judge is treated as a NOISY corroborator by the combiner, not an oracle.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

from .cache import Cache, canonical_key
from .concurrency import ConcurrencyLimiter, RetryableError, with_retries
from .cost import CostLedger
from .models import ALL_CATEGORIES, Evidence, Item

_RUBRIC_PATH = Path(__file__).resolve().parent / "rubric.md"


@lru_cache(maxsize=1)
def load_rubric() -> tuple[str, str]:
    text = _RUBRIC_PATH.read_text(encoding="utf-8")
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


@dataclass
class JudgeError:
    category: str
    severity: str
    translation_quote: str
    source_quote: str
    explanation: str
    confidence: float


@dataclass
class JudgeResult:
    verdict: str
    errors: list[JudgeError]
    alignment: list[dict] = field(default_factory=list)
    raw: str = ""
    parse_ok: bool = True


class JudgeClient(Protocol):
    async def judge(self, item: Item, lang_name: str, repeat_index: int = 0) -> JudgeResult: ...


def build_messages(rubric: str, item: Item, lang_name: str) -> list[dict]:
    user = {
        "role": "user",
        "content": (
            f"LANGUAGE: {lang_name}\n\n"
            f"SOURCE (English):\n{item.source}\n\n"
            f"TRANSLATION ({lang_name}):\n{item.translation or ''}\n"
            + (f"\nREFERENCE ({lang_name}):\n{item.reference}\n" if item.reference else "")
        ),
    }
    return [{"role": "system", "content": rubric}, user]


def _extract_json(text: str) -> dict | None:
    """Best-effort JSON recovery: direct parse, then first {...} block, then light repair."""
    for candidate in (text, _first_brace_block(text)):
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            repaired = re.sub(r",\s*([}\]])", r"\1", candidate)  # trailing commas
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                continue
    return None


def _first_brace_block(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def parse_judge_output(raw: str) -> JudgeResult:
    data = _extract_json(raw)
    if not isinstance(data, dict):
        return JudgeResult(verdict="error", errors=[], raw=raw, parse_ok=False)
    errors: list[JudgeError] = []
    for e in data.get("errors", []) or []:
        cat = str(e.get("category", "")).strip()
        if cat not in ALL_CATEGORIES:
            cat = "mistranslation"  # keep but coerce unknown category
        try:
            conf = float(e.get("confidence", 0.5))
        except (TypeError, ValueError):
            conf = 0.5
        errors.append(JudgeError(
            category=cat,
            severity=str(e.get("severity", "major")).strip() or "major",
            translation_quote=str(e.get("translation_quote", "") or ""),
            source_quote=str(e.get("source_quote", "") or ""),
            explanation=str(e.get("explanation", "") or ""),
            confidence=max(0.0, min(1.0, conf)),
        ))
    verdict = "error" if errors or str(data.get("verdict")) == "error" else "ok"
    return JudgeResult(verdict=verdict, errors=errors, alignment=data.get("alignment", []) or [],
                       raw=raw, parse_ok=True)


def judge_to_evidence(result: JudgeResult, item: Item) -> list[Evidence]:
    """Convert judge errors to Evidence, locating each verbatim quote in the ORIGINAL text.
    Quotes that are not exact substrings are DEMOTED to unlocated evidence (lower weight)."""
    out: list[Evidence] = []
    tr = item.translation or ""
    src = item.source or ""
    for e in result.errors:
        target_span = _locate(tr, e.translation_quote)
        source_span = _locate(src, e.source_quote)
        located = target_span is not None or source_span is not None
        out.append(Evidence(
            check="judge", category=e.category, strength="medium", noisy=True,
            score=e.confidence, severity_hint=_severity(e.severity),
            source_span=source_span, target_span=target_span,
            details={
                "explanation": e.explanation,
                "located": located,
                "verdict": result.verdict,
                "parse_ok": result.parse_ok,
                "quote_verbatim": located,
            },
        ))
    return out


def _severity(s: str) -> str:
    return s if s in ("minor", "major", "critical") else "major"


def _locate(text: str, quote: str) -> tuple[int, int] | None:
    if not quote or not text:
        return None
    idx = text.find(quote)
    if idx < 0:
        return None
    return (idx, idx + len(quote))


def _is_transient(exc: Exception) -> bool:
    if type(exc).__name__ in ("RateLimitError", "APITimeoutError", "APIConnectionError",
                              "InternalServerError", "APIError"):
        status = getattr(exc, "status_code", None)
        return status is None or status == 429 or (isinstance(status, int) and status >= 500)
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and (status == 429 or status >= 500)


def _retry_after(exc: Exception) -> float | None:
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    ra = headers.get("retry-after")
    if ra:
        try:
            return float(ra)
        except (TypeError, ValueError):
            pass
    reset = headers.get("x-ratelimit-reset") or headers.get("X-RateLimit-Reset")
    if reset:
        try:
            import time
            # OpenRouter returns an epoch in milliseconds
            delta = float(reset) / 1000.0 - time.time()
            return max(1.0, min(delta, 65.0))
        except (TypeError, ValueError):
            pass
    return None


# ---- real OpenAI-compatible judge -----------------------------------------------------


class OpenAIJudge:
    def __init__(self, base_url: str, api_key: str, model: str, cache: Cache,
                 limiter: ConcurrencyLimiter, logger, seed: int | None = 7,
                 ledger: CostLedger | None = None, input_rate: float | None = None,
                 output_rate: float | None = None):
        self.base_url, self.api_key, self.model = base_url, api_key, model
        self.cache, self.limiter, self.logger, self.seed = cache, limiter, logger, seed
        self.ledger, self.input_rate, self.output_rate = ledger, input_rate, output_rate
        self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                      "usage_missing": 0, "uncached_calls": 0,
                      "uncached_prompt_tokens": 0, "uncached_completion_tokens": 0}

    def _record_usage(self, counts: dict, cached: bool) -> None:
        self.usage["calls"] += 1
        prompt, completion = counts.get("prompt_tokens"), counts.get("completion_tokens")
        if prompt is None or completion is None:
            self.usage["usage_missing"] += 1
        self.usage["prompt_tokens"] += int(prompt or 0)
        self.usage["completion_tokens"] += int(completion or 0)
        if not cached:
            self.usage["uncached_calls"] += 1
            self.usage["uncached_prompt_tokens"] += int(prompt or 0)
            self.usage["uncached_completion_tokens"] += int(completion or 0)

    async def judge(self, item: Item, lang_name: str, repeat_index: int = 0) -> JudgeResult:
        rubric, rubric_hash = load_rubric()
        messages = build_messages(rubric, item, lang_name)
        params = {"model": self.model, "rubric": rubric_hash, "repeat": repeat_index,
                  "lang": lang_name, "seed": self.seed, "base_url": self.base_url,
                  "max_tokens": 1200}
        payload = json.dumps(messages, ensure_ascii=False, sort_keys=True)
        key = canonical_key(self.model, "/chat/completions:judge", params, payload)
        cached = self.cache.get_json(key)
        if cached is not None:
            self._record_usage(cached.get("usage", {}), cached=True)
            self.logger.info("judge_usage", item=item.id, cached=True, **cached.get("usage", {}))
            return parse_judge_output(cached["raw"])

        async def call():
            try:
                from openai import AsyncOpenAI

                client = AsyncOpenAI(base_url=self.base_url, api_key=self.api_key)
                kwargs: dict[str, Any] = {"model": self.model, "messages": messages,
                                          "temperature": 0, "max_tokens": 1200}
                if self.seed is not None:
                    kwargs["seed"] = self.seed + repeat_index
                try:
                    return await client.chat.completions.create(
                        response_format={"type": "json_object"}, **kwargs)
                except Exception as exc:  # noqa: BLE001
                    # only fall back to no-json-mode when the provider rejects response_format;
                    # transient errors (429/5xx/timeout) must propagate to the retry layer.
                    if _is_transient(exc) or getattr(exc, "status_code", None) != 400:
                        raise
                    try:
                        return await client.chat.completions.create(**kwargs)
                    except Exception as fallback_exc:  # noqa: BLE001
                        if getattr(fallback_exc, "status_code", None) != 400 or "seed" not in kwargs:
                            raise
                        kwargs.pop("seed")
                        return await client.chat.completions.create(**kwargs)
            except Exception as exc:  # noqa: BLE001
                if _is_transient(exc):
                    raise RetryableError(str(exc), retry_after=_retry_after(exc)) from exc
                raise

        priced = self.ledger is not None and self.input_rate is not None and self.output_rate is not None
        # A UTF-8-byte bound plus protocol overhead is conservative for common tokenizers;
        # provider billing, retries, and tokenizers may differ, so this is an estimate.
        reserve = ((len(payload.encode("utf-8")) + 256) * (self.input_rate or 0)
                   + 1200 * (self.output_rate or 0)) / 1_000_000 if priced else 0.0
        if priced:
            self.ledger.reserve(reserve)
        try:
            resp = await self.limiter.run(lambda: with_retries(call))
        except BaseException:
            if priced:
                self.ledger.settle("judge:" + self.model, reserve)
            raise
        usage = getattr(resp, "usage", None)
        counts = {"prompt_tokens": getattr(usage, "prompt_tokens", None),
                  "completion_tokens": getattr(usage, "completion_tokens", None)}
        if priced:
            missing = counts["prompt_tokens"] is None or counts["completion_tokens"] is None
            actual = reserve if missing else (
                counts["prompt_tokens"] * self.input_rate + counts["completion_tokens"] * self.output_rate
            ) / 1_000_000
            self.ledger.settle("judge:" + self.model, reserve, actual, estimated=True)
        self._record_usage(counts, cached=False)
        self.logger.info("judge_usage", item=item.id, cached=False, **counts)
        raw = resp.choices[0].message.content or ""
        self.cache.put_json(key, {"raw": raw, "usage": counts})
        return parse_judge_output(raw)
