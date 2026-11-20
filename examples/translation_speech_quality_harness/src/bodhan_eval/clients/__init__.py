import os

from bodhan_eval.clients.bodhan import BodhanClient
from bodhan_eval.clients.claude import ClaudeLLMClient, LLMOutputError
from bodhan_eval.clients.http import APIError, CacheMiss
from bodhan_eval.clients.llm import LLMClient


def make_llm_client(cache, cost, **kw):
    """Claude when LLM_PROVIDER=anthropic, or when ANTHROPIC_API_KEY is set and no
    OpenAI-compatible endpoint is configured; otherwise the OpenAI-compatible client."""
    provider = (os.environ.get("LLM_PROVIDER") or "").lower()
    if provider == "anthropic" or (
        not provider and os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("LLM_BASE_URL")
    ):
        return ClaudeLLMClient(cache, cost, **kw)
    return LLMClient(cache, cost, **kw)


__all__ = [
    "APIError",
    "BodhanClient",
    "CacheMiss",
    "ClaudeLLMClient",
    "LLMClient",
    "LLMOutputError",
    "make_llm_client",
]
