#!/usr/bin/env python3
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from ..client import client
from ..models import (
    TRANSLATE_SCRIPTS,
    ErrorCode,
)
from ..media import chunk_text_for_translation, script_ratio
from ..tools._lang import validate_lang


def _error(
    code: ErrorCode, message: str, retryable: bool = False,
    details: dict | None = None,
) -> dict:
    return {
        "error": code.value,
        "message": message,
        "code": code.value,
        "retryable": retryable,
        "details": details or {},
    }


def _rejoin_translations(chunks: list[str]) -> str:
    """Join translated chunks, preserving Markdown table structure."""
    if not chunks:
        return ""
    groups: list[list[str]] = []
    current_group: list[str] = [chunks[0]]
    for chunk in chunks[1:]:
        # A chunk is part of a table block if it starts with '|' and the
        # previous chunk also looks like a table row (starts with '|').
        if chunk.startswith("|") and current_group[-1].startswith("|"):
            current_group.append(chunk)
        else:
            groups.append(current_group)
            current_group = [chunk]
    groups.append(current_group)
    parts = []
    for group in groups:
        if len(group) > 1:
            # Contiguous table block — join rows with single newline
            parts.append("\n".join(group))
        else:
            parts.append(group[0])
    return "\n\n".join(parts)


# Maximum per-retry backoff cap (seconds). Total budget across all retries
# is bounded so a persistent 429 loop cannot hang the CLI process.
_MAX_RETRY_DELAY_SEC = 10.0
_MAX_RATE_LIMIT_RETRIES = 3
_MAX_TOTAL_RATE_LIMIT_BACKOFF_SEC = 25.0


async def _request_with_rate_limit_retry(
    chunk: str,
    target_code: str,
    script: str,
) -> tuple[str, float]:
    """Issue a single translate API call with bounded 429 retry logic.

    On HTTP 429 we honour the *Retry-After* response header (capped at
    ``_MAX_RETRY_DELAY_SEC``), retry up to ``_MAX_RATE_LIMIT_RETRIES``
    times, and stop once the cumulative sleep exceeds
    ``_MAX_TOTAL_RATE_LIMIT_BACKOFF_SEC``.
    """
    total_backoff = 0.0
    for attempt in range(_MAX_RATE_LIMIT_RETRIES + 1):
        try:
            api_resp = await client.request(
                "indic-translate",
                "/chat/completions",
                json_data={
                    "model": "indic-translate",
                    "messages": [
                        {
                            "role": "user",
                            "content": f"Translate '{chunk}' to {target_code}",
                        }
                    ],
                    "script": script,
                },
                timeout=60,
            )
            resp = api_resp.data
            cost = api_resp.cost_inr
            content = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
            return content, cost
        except Exception as e:
            err_str = str(e).lower()
            is_rate_limited = (
                "429" in err_str or "rate limit" in err_str
                or "rate_limited" in err_str
            )
            if not is_rate_limited:
                # Non-rate-limit error — surface immediately.
                raise
            # We are rate-limited. If this is the final attempt or the
            # backoff budget is exhausted, give up with a structured error.
            if attempt == _MAX_RATE_LIMIT_RETRIES:
                break
            delay = min((2 ** (attempt + 1)), _MAX_RETRY_DELAY_SEC)
            if total_backoff + delay > _MAX_TOTAL_RATE_LIMIT_BACKOFF_SEC:
                break
            await asyncio.sleep(delay)
            total_backoff += delay
    # Exhausted retries or hit backoff ceiling.
    raise RateLimitExhaustedError(
        f"Translation rate-limited after {_MAX_RATE_LIMIT_RETRIES} retries "
        f"(total backoff {total_backoff:.1f}s)"
    )


class RateLimitExhaustedError(Exception):
    pass


async def translate_text(
    source_lang: str,
    target_lang: str,
    text: Optional[str] = None,
    input_file: Optional[str] = None,
    output_file: Optional[str] = None,
    script: str = "native",
) -> dict:
    source_code, err = validate_lang(source_lang, "translate")
    if err:
        return err
    target_code, err = validate_lang(target_lang, "translate")
    if err:
        return err

    if script not in TRANSLATE_SCRIPTS:
        supported = ", ".join(sorted(TRANSLATE_SCRIPTS))
        return _error(
            ErrorCode.VALIDATION_ERROR,
            f"Invalid script '{script}'. Must be one of: {supported}",
            retryable=False,
        )

    if input_file is None and text is None:
        return _error(
            ErrorCode.VALIDATION_ERROR,
            "Either text or input_file must be provided",
            retryable=False,
        )

    input_text = text
    if input_file:
        path = Path(input_file)
        if not path.exists():
            return _error(
                ErrorCode.FILE_NOT_FOUND,
                f"File not found: {input_file}",
                retryable=False,
            )
        if path.is_dir():
            return _error(
                ErrorCode.INVALID_INPUT,
                f"Path is a directory, expected a text file: {input_file}",
                retryable=False,
            )
        if path.suffix.lower() in (".txt", ".md"):
            try:
                input_text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                return _error(
                    ErrorCode.MEDIA_PROCESSING_ERROR,
                    f"Failed to read input file: {e}",
                    retryable=False,
                )
        else:
            return _error(
                ErrorCode.UNSUPPORTED_FORMAT,
                f"Unsupported input file format: {path.suffix}",
                retryable=False,
                details={"supported": [".txt", ".md"]},
            )

    if not input_text or not input_text.strip():
        return _error(
            ErrorCode.EMPTY_INPUT,
            "Input text is empty or whitespace-only",
            retryable=False,
        )

    chunks = chunk_text_for_translation(input_text)
    translations = []
    total_cost = 0.0

    semaphore = asyncio.Semaphore(2)
    last_request_time = 0.0
    PACING_DELAY = 1.0

    async def _translate_chunk(chunk: str) -> tuple[str, float]:
        nonlocal last_request_time
        # Minimum pacing delay between outgoing requests.
        now = asyncio.get_event_loop().time()
        elapsed = now - last_request_time
        if elapsed < PACING_DELAY:
            await asyncio.sleep(PACING_DELAY - elapsed)
        last_request_time = asyncio.get_event_loop().time()

        async with semaphore:
            return await _request_with_rate_limit_retry(chunk, target_code, script)

    tasks = [_translate_chunk(c) for c in chunks]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    has_rate_limit_failure = False
    for res in results:
        if isinstance(res, RateLimitExhaustedError):
            has_rate_limit_failure = True
            continue
        if isinstance(res, Exception):
            # Surface first non-rate-limit failure immediately.
            return _error(
                ErrorCode.NETWORK_ERROR,
                f"Translation API call failed: {res}",
                retryable=True,
            )
        content, cost = res
        translations.append(content)
        total_cost += cost

    if has_rate_limit_failure:
        return _error(
            ErrorCode.RATE_LIMITED,
            "Translation rate-limited after bounded retries",
            retryable=True,
            details={
                "max_retries": _MAX_RATE_LIMIT_RETRIES,
                "max_total_backoff_sec": _MAX_TOTAL_RATE_LIMIT_BACKOFF_SEC,
            },
        )

    full_translation = _rejoin_translations(translations)

    target_script_map = {
        "devanagari": ["hi", "mr", "ne", "sa", "bn", "as", "gu", "pa", "or"],
        "tamil": ["ta"],
        "telugu": ["te"],
        "bengali": ["bn", "as"],
        "kannada": ["kn"],
        "gujarati": ["gu"],
        "malayalam": ["ml"],
        "oriya": ["or"],
        "gurmukhi": ["pa"],
        "arabic": ["ur", "ks", "sd-Arab"],
    }

    expected_scripts = []
    for scr, langs in target_script_map.items():
        if target_code in langs:
            expected_scripts.append(scr)

    target_script_verified = False
    if expected_scripts:
        target_script_verified = any(
            script_ratio(full_translation, scr) >= 0.85 for scr in expected_scripts
        )

    if output_file:
        try:
            Path(output_file).write_text(full_translation, encoding="utf-8")
        except Exception as e:
            return _error(
                ErrorCode.MEDIA_PROCESSING_ERROR,
                f"Failed to write output file: {e}",
                retryable=False,
            )

    return {
        "translation": (
            f"<untrusted_source_data>{full_translation}</untrusted_source_data>"
        ),
        "target_language": target_code,
        "script": script,
        "target_script_verified": target_script_verified,
        "cost_inr": round(total_cost, 4),
        "output_file": output_file,
        "cached": False,
    }


__all__ = ["translate_text"]
