"""Cached, budget-aware wrapper around :class:`BodhanClient.translate`."""

from __future__ import annotations

from bodhan_eval.cache import JsonCache, stable_hash
from bodhan_eval.cost import CostTracker
from bodhan_eval.ratelimit import RateLimiter

TRANSLATE_MODEL = "indic-translate"
TRANSLATE_ENDPOINT = "/v1/chat/completions"


class CachedTranslator:
    """Translate through Bodhan Indic-Translate with a persistent cache.

    The cache key covers everything that changes the output: model id,
    endpoint, input text, target language and target script. Truncated
    responses are not cached, so they are retried on the next run.
    """

    def __init__(
        self,
        client,
        cache: JsonCache | None = None,
        cost: CostTracker | None = None,
        limiter: RateLimiter | None = None,
    ):
        self.client = client
        self.cache = cache or JsonCache(None)
        self.cost = cost
        self.limiter = limiter or RateLimiter(TRANSLATE_MODEL)

    @staticmethod
    def cache_key(text: str, target_language: str, script: str = "native") -> str:
        return stable_hash({
            "model": TRANSLATE_MODEL,
            "endpoint": TRANSLATE_ENDPOINT,
            "text": text,
            "target_language": target_language,
            "script": script,
        })

    def translate(
        self,
        text: str,
        target_language: str,
        script: str = "native",
    ) -> dict:
        key = self.cache_key(text, target_language, script)
        cached = self.cache.get(key)

        if cached is not None:
            return {**cached, "cached": True}

        if self.cost:
            self.cost.reserve(TRANSLATE_MODEL, self.cost.estimate_translate(text))

        result = self.limiter.call(self.client.translate, text, target_language, script)

        if self.cost:
            self.cost.record(
                TRANSLATE_MODEL,
                self.cost.translate_cost(result.get("usage", {}), text),
            )

        entry = {
            "translation": result["translation"],
            "truncated": bool(result.get("truncated", False)),
            "usage": result.get("usage", {}),
        }

        if not entry["truncated"]:
            self.cache.set(key, entry)

        return {**entry, "cached": False}
