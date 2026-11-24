"""Indic-Translate client: validation, chunking for any input size, retry with backoff, cost, structured errors.

The API documents a context window but no numeric input limit, so chunk size is configurable
(``TRANSLATE_MAX_CHARS``, default 1500) and the tool adapts when the API pushes back: a chunk that is
rejected as too large (HTTP 413) or comes back ``truncated`` is halved and retried.
"""

from __future__ import annotations

import asyncio
import os
from typing import Literal

import httpx
from pydantic import BaseModel

from bodhan_core.cache import ResultCache
from bodhan_core.chunking import split_text
from bodhan_core.client import retry_delay
from bodhan_core.config import section
from bodhan_core.errors import ToolError
from bodhan_core.langid import describe
from bodhan_core.runtime import API_SLOTS, BACKOFF_S, base_url, make_client
from bodhan_core.textio import inline, resolve_text, save_to

_CFG = section("translate")
_HTTP = section("http")

# Hosted price: Rs 0.20 per 10,000 output tokens (input tokens are free).
INR_PER_OUTPUT_TOKEN = _CFG["inr_per_10k_output_tokens"] / 10_000

# From scripts/bodhan_api_rules.json. bhb/bho are listed for speech-to-text only.
TRANSLATE_LANGUAGES = {
    "as", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "ks-Deva", "mai", "ml",
    "mni", "mni-Beng", "mr", "ne", "or", "pa", "sa", "sat", "sd", "sd-Arab", "ta", "te", "ur",
}  # fmt: skip

MODEL = "indic-translate"
ENDPOINT = "/v1/chat/completions"  # the documented POST /translate route returns 404 on api.bodhan.ai
MAX_ATTEMPTS = _HTTP["max_attempts"]
TIMEOUT_S = float(_CFG["timeout_s"])
DEFAULT_MAX_CHARS = _CFG["max_chars"]
MAX_TOTAL_CHARS = _CFG["max_total_chars"]  # hard ceiling so one call cannot create a surprise bill
MIN_SPLIT_CHARS = _CFG["min_split_chars"]  # stop halving below this size

# In-process cache: the same (text, target, script) is never sent to the model twice per server run.
_CACHE: ResultCache[tuple[str, int]] = ResultCache()


class TranslateResult(BaseModel):
    ok: bool
    translation: str | None = None  # the full translation, or (English only) its first characters, or empty, when `translation_is_preview`
    translation_is_preview: bool = False  # true: read the whole translation from `translation_path`
    translation_path: str | None = None  # file holding the full translation (set for long output or when output_path is given)
    source_language: str | None = None  # guessed from the input's script (Devanagari may be hi or mr); null if no letters
    target_language: str | None = None
    truncated: bool = False
    chunks: int = 0
    output_tokens: int = 0
    cost_inr: float = 0.0
    error: ToolError | None = None


class _Chunk(BaseModel):
    """Outcome of translating one piece of text (internal)."""

    ok: bool
    translation: str = ""
    truncated: bool = False
    too_large: bool = False
    output_tokens: int = 0
    chunks: int = 0
    error: ToolError | None = None


def _fail(code: str, message: str, retryable: bool = False, cost_inr: float = 0.0) -> TranslateResult:
    return TranslateResult(ok=False, cost_inr=cost_inr, error=ToolError(code=code, message=message, retryable=retryable))  # cost_inr: chunks already billed


async def _post(client: httpx.AsyncClient, url: str, key: str, body: dict, backoff_s: float) -> _Chunk:
    """One API call with retries on timeouts, HTTP 429 and 5xx (exponential backoff)."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            async with API_SLOTS:
                resp = await client.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
        except httpx.TransportError as exc:  # timeouts, connection resets
            if attempt == MAX_ATTEMPTS:
                return _Chunk(ok=False, error=ToolError(code="network_error", retryable=True,
                              message=f"Could not reach Bodhan after {attempt} attempts: {exc!r}"))  # fmt: skip
            await asyncio.sleep(backoff_s * 2 ** (attempt - 1))
            continue

        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt == MAX_ATTEMPTS:
                return _Chunk(ok=False, error=ToolError(code="upstream_unavailable", retryable=True,
                              message=f"Bodhan returned HTTP {resp.status_code} after {attempt} attempts."))  # fmt: skip
            await asyncio.sleep(retry_delay(resp, attempt, backoff_s))
            continue

        data = resp.json() if resp.content else {}
        if resp.status_code != 200:
            err = data.get("error", {}) if isinstance(data, dict) else {}
            code = err.get("code", f"http_{resp.status_code}")
            return _Chunk(ok=False, too_large=resp.status_code == 413 or code == "payload_too_large",
                          error=ToolError(code=code, message=err.get("message", resp.text[:300])))  # fmt: skip

        try:
            choice = data["choices"][0]
            translation = choice["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return _Chunk(ok=False, error=ToolError(code="bad_response", message=f"Unexpected response from Bodhan: {resp.text[:300]}"))
        return _Chunk(
            ok=True,
            translation=translation or "",
            truncated=choice.get("finish_reason") == "length",  # output hit the token budget
            output_tokens=int((data.get("usage") or {}).get("completion_tokens", 0)),
            chunks=1,
        )
    return _Chunk(ok=False, error=ToolError(code="unknown", message="Unexpected state."))  # pragma: no cover


async def _translate_piece(client, url, key, text, target, script, backoff_s) -> _Chunk:
    """Translate one chunk. If the API says it is too large or the output was cut off, halve it and recurse."""
    core = text.strip()
    if not core:  # whitespace-only chunk: nothing to translate, keep the layout
        return _Chunk(ok=True, translation=text, chunks=0)
    lead, trail = text[: len(text) - len(text.lstrip())], text[len(text.rstrip()) :]

    cache_key = (core, target, script)
    if cache_key in _CACHE:
        cached, _tokens = _CACHE[cache_key]
        return _Chunk(ok=True, translation=lead + cached + trail, chunks=1)  # cached: no new tokens billed

    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": core}],  # exactly one user turn, no system turn
        "target_language_code": target,
        "target_script": script or "native",
    }
    res = await _post(client, url, key, body, backoff_s)

    if res.ok and not res.truncated:
        _CACHE[cache_key] = (res.translation, res.output_tokens)
        return res.model_copy(update={"translation": lead + res.translation + trail})

    if res.ok or res.too_large:  # truncated output, or input above the API limit
        halves = split_text(core, max(len(core) // 2, MIN_SPLIT_CHARS)) if len(core) > MIN_SPLIT_CHARS else [core]
        if len(halves) > 1:
            parts = []
            for half in halves:  # sequential: one request at a time
                part = await _translate_piece(client, url, key, half, target, script, backoff_s)
                if not part.ok:
                    return part
                parts.append(part)
            return _Chunk(ok=True, translation=lead + "".join(p.translation for p in parts) + trail,
                          output_tokens=sum(p.output_tokens for p in parts), chunks=sum(p.chunks for p in parts))  # fmt: skip
        code, msg = ("truncated_output", "Output was truncated even for a tiny input.") if res.ok else (
            "payload_too_large", res.error.message)  # fmt: skip
        return _Chunk(ok=False, error=ToolError(code=code, message=msg))
    return res


async def translate(
    text: str | None,
    target_language: str,
    script: Literal["native", "roman", "codemix"] | None = None,
    text_path: str | None = None,
    output_path: str | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    backoff_s: float = BACKOFF_S,
    max_chars: int | None = None,
) -> TranslateResult:
    resolved = resolve_text(text, text_path)
    if isinstance(resolved, ToolError):
        return TranslateResult(ok=False, error=resolved)
    text = resolved
    if target_language not in TRANSLATE_LANGUAGES:
        return _fail(
            "unsupported_language",
            f"`{target_language}` is not supported. Use one of: {', '.join(sorted(TRANSLATE_LANGUAGES))}.",
        )
    if len(text) > MAX_TOTAL_CHARS:
        return _fail("text_too_long", f"Input has {len(text)} characters; the limit per call is {MAX_TOTAL_CHARS}. Split the text and call again.")
    key = os.environ.get("BODHAN_TRANSLATE_KEY")
    if not key:
        return _fail("missing_api_key", "BODHAN_TRANSLATE_KEY is not set in the server environment.")

    limit = max_chars or int(os.environ.get("TRANSLATE_MAX_CHARS", DEFAULT_MAX_CHARS))
    pieces = split_text(text, limit)

    translated: list[str] = []
    tokens = chunks = 0
    truncated = False
    async with make_client(TIMEOUT_S, transport) as client:
        for i, piece in enumerate(pieces, 1):
            res = await _translate_piece(client, f"{base_url()}{ENDPOINT}", key, piece, target_language, script, backoff_s)
            if not res.ok:
                e = res.error
                return _fail(e.code, f"Chunk {i} of {len(pieces)} failed ({len(translated)} translated before it): {e.message}", e.retryable, round(tokens * INR_PER_OUTPUT_TOKEN, 6))
            translated.append(res.translation)
            tokens += res.output_tokens
            chunks += res.chunks
            truncated = truncated or res.truncated

    full = "".join(translated)
    saved, werr = save_to(full, output_path, "translation")
    if werr:
        return TranslateResult(ok=False, error=werr, cost_inr=round(tokens * INR_PER_OUTPUT_TOKEN, 6))
    shown, is_preview = inline(full)
    return TranslateResult(
        ok=True,
        translation=shown,
        translation_is_preview=is_preview,
        translation_path=saved,
        source_language=describe(text)["detected_language"],
        target_language=target_language,
        truncated=truncated,
        chunks=chunks,
        output_tokens=tokens,
        cost_inr=round(tokens * INR_PER_OUTPUT_TOKEN, 6),
    )
