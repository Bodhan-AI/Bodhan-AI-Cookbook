"""Indic-Translate tool for Bodhan MCP Server.

Wraps Bodhan AI's Indic-Translate model (POST https://api.bodhan.ai/v1/chat/completions)
with local disk caching, intelligent text chunking, language validation,
English pivot routing for Indic-to-Indic directions, rate limit retries,
and structured error reporting.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import httpx
from pydantic import BaseModel, Field

from bodhan_mcp.common import (
    BODHAN_API_BASE,
    CallTracker,
    StructuredError,
    PRICE_TRANSLATE_PER_10K_OUTPUT_TOKENS_INR,
    calculate_translate_cost_inr,
    classify_http_response,
    get_cache_key,
    read_cache,
    write_cache,
    check_live_calls_allowed,
    check_global_cap_allowed,
    is_mock_transport,
    log_spend_dispatched,
    update_spend_ledger,
    log_spend_cache_hit,
)

PRICE_PER_10K_OUTPUT_TOKENS_INR = PRICE_TRANSLATE_PER_10K_OUTPUT_TOKENS_INR

# ------------------------------------------------------------------------------
# Constants and Supported Languages
# ------------------------------------------------------------------------------

MODEL_NAME = "indic-translate"
MAX_CONTEXT_TOKENS = 32768
SAFE_CHUNK_CHAR_LIMIT = 4000  # Safe text chunk size preserving context
CACHE_DIR = Path(".cache/bodhan_translate")
MAX_INVOCATION_HTTP_CALLS = 8  # Hard cap on HTTP calls per tool invocation
MAX_TRUNCATION_DEPTH = 2  # Max recursive split depth on truncation

# 22 Scheduled Indian Languages + English
SUPPORTED_LANGUAGES: Dict[str, str] = {
    # English
    "en": "English",
    "english": "English",
    # 22 Scheduled Languages
    "as": "Assamese",
    "asm": "Assamese",
    "assamese": "Assamese",
    "bn": "Bengali",
    "ben": "Bengali",
    "bengali": "Bengali",
    "brx": "Bodo",
    "bodo": "Bodo",
    "doi": "Dogri",
    "dogri": "Dogri",
    "gu": "Gujarati",
    "guj": "Gujarati",
    "gujarati": "Gujarati",
    "hi": "Hindi",
    "hin": "Hindi",
    "hindi": "Hindi",
    "kn": "Kannada",
    "kan": "Kannada",
    "kannada": "Kannada",
    "ks": "Kashmiri",
    "kashmiri": "Kashmiri",
    "kok": "Konkani",
    "konkani": "Konkani",
    "mai": "Maithili",
    "maithili": "Maithili",
    "ml": "Malayalam",
    "mal": "Malayalam",
    "malayalam": "Malayalam",
    "mni": "Manipuri",
    "manipuri": "Manipuri",
    "mr": "Marathi",
    "mar": "Marathi",
    "marathi": "Marathi",
    "ne": "Nepali",
    "nep": "Nepali",
    "nepali": "Nepali",
    "or": "Odia",
    "ori": "Odia",
    "odia": "Odia",
    "pa": "Punjabi",
    "pan": "Punjabi",
    "punjabi": "Punjabi",
    "sa": "Sanskrit",
    "san": "Sanskrit",
    "sanskrit": "Sanskrit",
    "sat": "Santali",
    "santali": "Santali",
    "sd": "Sindhi",
    "sindhi": "Sindhi",
    "ta": "Tamil",
    "tam": "Tamil",
    "tamil": "Tamil",
    "te": "Telugu",
    "tel": "Telugu",
    "telugu": "Telugu",
    "ur": "Urdu",
    "urd": "Urdu",
    "urdu": "Urdu",
}

# Standard canonical codes for API payload
CANONICAL_CODES: Dict[str, str] = {
    "English": "en",
    "Assamese": "as",
    "Bengali": "bn",
    "Bodo": "brx",
    "Dogri": "doi",
    "Gujarati": "gu",
    "Hindi": "hi",
    "Kannada": "kn",
    "Kashmiri": "ks",
    "Konkani": "kok",
    "Maithili": "mai",
    "Malayalam": "ml",
    "Manipuri": "mni",
    "Marathi": "mr",
    "Nepali": "ne",
    "Odia": "or",
    "Punjabi": "pa",
    "Sanskrit": "sa",
    "Santali": "sat",
    "Sindhi": "sd",
    "Tamil": "ta",
    "Telugu": "te",
    "Urdu": "ur",
}


# ------------------------------------------------------------------------------
# Pydantic Schemas
# ------------------------------------------------------------------------------


class TranslateInput(BaseModel):
    text: str = Field(
        ...,
        description="The source text to be translated. Can include multi-paragraph text, Markdown, LaTeX, or tables.",
    )
    target_language: str = Field(
        ...,
        description="Destination language code (e.g., 'hi', 'ta', 'en') or language name (e.g., 'Hindi', 'Tamil').",
    )
    source_language: Optional[str] = Field(
        None,
        description="Source language code or name. If omitted, the model auto-infers it.",
    )


class TranslateOutput(BaseModel):
    success: bool
    translated_text: str = ""
    source_language: str
    target_language: str
    chunk_count: int = 1
    chunks_count: int = 1  # Alias for backward compatibility
    cache_hit: bool = False
    cached: bool = False  # Alias for backward compatibility
    pivoted_via_english: bool = False
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_inr: float = 0.0
    error: Optional[StructuredError] = None


# ------------------------------------------------------------------------------
# Language Normalization
# ------------------------------------------------------------------------------


def normalize_language(lang_input: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Returns (canonical_code, standardized_name) or (None, None) if unsupported."""
    if not lang_input:
        return None, None
    clean = lang_input.strip().lower()
    std_name = SUPPORTED_LANGUAGES.get(clean)
    if not std_name:
        return None, None
    canon_code = CANONICAL_CODES[std_name]
    return canon_code, std_name


# ------------------------------------------------------------------------------
# Text Chunking
# ------------------------------------------------------------------------------


def chunk_text(text: str, max_chars: int = SAFE_CHUNK_CHAR_LIMIT) -> List[str]:
    """Chunks text cleanly by paragraphs and sentences without splitting words."""
    text = text.strip()
    if not text or len(text) <= max_chars:
        return [text] if text else []

    chunks: List[str] = []
    # Split primarily by double newline (paragraphs)
    paragraphs = text.split("\n\n")
    current_chunk: List[str] = []
    current_len = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(para) > max_chars:
            # Sub-split long paragraph by sentence boundaries
            sentences = re.split(r"(?<=[.!?।\n])\s+", para)
            for s in sentences:
                s = s.strip()
                if not s:
                    continue
                if current_len + len(s) + 1 <= max_chars:
                    current_chunk.append(s)
                    current_len += len(s) + 1
                else:
                    if current_chunk:
                        chunks.append(" ".join(current_chunk))
                    current_chunk = [s]
                    current_len = len(s)
        else:
            if current_len + len(para) + 2 <= max_chars:
                current_chunk.append(para)
                current_len += len(para) + 2
            else:
                if current_chunk:
                    chunks.append("\n\n".join(current_chunk))
                current_chunk = [para]
                current_len = len(para)

    if current_chunk:
        chunks.append("\n\n".join(current_chunk))

    return chunks


# ------------------------------------------------------------------------------
# Tool Implementation
# ------------------------------------------------------------------------------


class IndicTranslateTool:
    """Manages translation calls to Bodhan AI's indic-translate model."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = BODHAN_API_BASE,
        cache_dir: Path = CACHE_DIR,
        client: Optional[httpx.AsyncClient] = None,
        max_concurrency: int = 3,
        max_chunk_chars: int = SAFE_CHUNK_CHAR_LIMIT,
        max_http_calls: int = MAX_INVOCATION_HTTP_CALLS,
        run_id: Optional[str] = None,
        ledger_path: Optional[Union[str, Path]] = None,
    ):
        self.api_key = api_key or os.getenv("BODHAN_TRANSLATE_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.cache_dir = cache_dir
        self.custom_client = client
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self.max_chunk_chars = max_chunk_chars
        self.max_http_calls = max_http_calls
        self.run_id = run_id or os.getenv("BODHAN_RUN_ID", "default")
        self.ledger_path = ledger_path

    async def _send_single_request(
        self,
        client: httpx.AsyncClient,
        text: str,
        target_code: str,
        call_tracker: CallTracker,
        depth: int = 0,
    ) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[StructuredError]]:
        """Sends a single chat completion translation request with retries and bounds."""
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        # EXACT payload fields sent to Bodhan API (no source_language_code)
        payload: Dict[str, Any] = {
            "model": MODEL_NAME,
            "messages": [{"role": "user", "content": text}],
            "target_language_code": target_code,
        }

        # Live-call safety guard: block real HTTP unless BODHAN_ALLOW_LIVE=1
        guard_err = check_live_calls_allowed(url, client)
        if guard_err:
            return None, None, guard_err

        # Global spend cap guard
        est_cost = calculate_translate_cost_inr(min(len(text) * 2, 4000))
        cap_err = check_global_cap_allowed(est_cost, self.ledger_path)
        if cap_err:
            return None, None, cap_err

        max_retries = 2
        response_2xx: Optional[httpx.Response] = None
        last_err: Optional[StructuredError] = None
        call_id: Optional[str] = None
        fffd_retry_count = 0

        for attempt in range(max_retries + 1):
            if not call_tracker.record_call():
                return (
                    None,
                    None,
                    StructuredError(
                        code="CALL_LIMIT_EXCEEDED",
                        message=f"Exceeded maximum total HTTP calls ({call_tracker.max_calls}) for this tool invocation.",
                        retryable=False,
                    ),
                )

            # Write ledger line with status "dispatched" BEFORE real HTTP send
            call_id = None
            if not is_mock_transport(client) or os.getenv("BODHAN_RECORD_MOCK_SPEND") == "1":
                call_id = log_spend_dispatched(
                    model=MODEL_NAME,
                    estimated_cost_inr=est_cost,
                    caller="indic_translate",
                    run_id=self.run_id,
                    ledger_path=self.ledger_path,
                )

            try:
                async with self.semaphore:
                    response = await client.post(url, headers=headers, json=payload, timeout=60.0)

                # Check if 2xx response was received
                if 200 <= response.status_code < 300:
                    # Check for U+FFFD using response's own decoding (response.json())
                    try:
                        parsed_data = response.json()
                        choices = parsed_data.get("choices", [])
                        msg_content = (
                            choices[0].get("message", {}).get("content", "") if choices else ""
                        )
                        if "\ufffd" in msg_content:
                            if fffd_retry_count < 1 and attempt < max_retries:
                                fffd_retry_count += 1
                                await asyncio.sleep(0.5)
                                continue
                            return (
                                None,
                                None,
                                StructuredError(
                                    code="UNICODE_REPLACEMENT_CHAR",
                                    message="Translation output contains invalid Unicode replacement character (U+FFFD).",
                                    retryable=False,
                                ),
                            )
                        response_2xx = response
                        break
                    except Exception:
                        # Malformed JSON payload after 200: handled by post-2xx UNKNOWN_BILLED handler
                        response_2xx = response
                        break

                should_retry, sleep_s, err = classify_http_response(response, attempt, max_retries)
                last_err = err
                if call_id:
                    update_spend_ledger(
                        call_id=call_id,
                        status="failed",
                        http_status=response.status_code,
                        error_code=err.code if err else f"HTTP_{response.status_code}",
                        ledger_path=self.ledger_path,
                    )
                if should_retry:
                    await asyncio.sleep(sleep_s)
                    continue
                return None, None, err

            except httpx.TimeoutException:
                if call_id:
                    update_spend_ledger(
                        call_id=call_id,
                        status="failed",
                        error_code="TIMEOUT",
                        ledger_path=self.ledger_path,
                    )
                if attempt < max_retries:
                    await asyncio.sleep(1.0)
                    continue
                return (
                    None,
                    None,
                    StructuredError(
                        code="TIMEOUT",
                        message="Request to Bodhan API timed out.",
                        retryable=True,
                    ),
                )
            except Exception as e:
                if call_id:
                    update_spend_ledger(
                        call_id=call_id,
                        status="failed",
                        error_code="NETWORK_ERROR",
                        ledger_path=self.ledger_path,
                    )
                if attempt < max_retries:
                    await asyncio.sleep(1.0)
                    continue
                return (
                    None,
                    None,
                    StructuredError(
                        code="NETWORK_ERROR",
                        message=f"Network error during translation request: {str(e)}",
                        retryable=False,
                    ),
                )

        if response_2xx is None:
            return (
                None,
                None,
                last_err
                or StructuredError(code="REQUEST_FAILED", message="Translation request failed."),
            )

        # Once a 2xx response was received, NEVER retry!
        # If any parsing exception occurs here, return StructuredError with UNKNOWN_BILLED
        try:
            data = response_2xx.json()
            usage = data.get("usage", {})
            comp_tokens = usage.get("completion_tokens", 0)
            actual_cost = calculate_translate_cost_inr(comp_tokens)

            if call_id:
                update_spend_ledger(
                    call_id=call_id,
                    status="completed",
                    http_status=response_2xx.status_code,
                    billed_units=comp_tokens,
                    actual_cost_inr=actual_cost,
                    ledger_path=self.ledger_path,
                )

            choices = data.get("choices", [])
            if not choices:
                return (
                    None,
                    None,
                    StructuredError(
                        code="EMPTY_RESPONSE",
                        message="Bodhan API returned no choices in completion.",
                        retryable=False,
                    ),
                )

            message_content = choices[0].get("message", {}).get("content", "")
            finish_reason = choices[0].get("finish_reason")
            is_truncated = (data.get("truncated") is True) or (finish_reason == "length")

            if is_truncated:
                discarded_comp = usage.get("completion_tokens", 0)
                discarded_total = usage.get("total_tokens", 0)
                discarded_prompt = usage.get("prompt_tokens", 0)

                if depth >= MAX_TRUNCATION_DEPTH:
                    return (
                        None,
                        None,
                        StructuredError(
                            code="OUTPUT_TRUNCATED",
                            message="Output was truncated and maximum split depth reached.",
                            retryable=False,
                        ),
                    )

                sentences = [
                    s.strip() for s in re.split(r"(?<=[.!?।\n])\s+", text.strip()) if s.strip()
                ]
                if len(sentences) > 1:
                    sub_chunks = sentences
                elif len(text) > 20:
                    half = len(text) // 2
                    sp = text.rfind(" ", 0, half + 10)
                    split_idx = sp if sp > 0 else half
                    sub_chunks = [text[:split_idx].strip(), text[split_idx:].strip()]
                else:
                    sub_chunks = []

                if len(sub_chunks) > 1:
                    sub_results = []
                    total_usage = {
                        "prompt_tokens": discarded_prompt,
                        "completion_tokens": discarded_comp,
                        "total_tokens": discarded_total,
                    }
                    for sub in sub_chunks:
                        sub_key = get_cache_key(MODEL_NAME, "auto", target_code, sub)
                        cached_sub = read_cache(sub_key, self.cache_dir)
                        if cached_sub is not None:
                            sub_results.append(cached_sub.get("translated_text", ""))
                            total_usage["total_tokens"] += cached_sub.get("total_tokens", 0)
                            total_usage["completion_tokens"] += cached_sub.get(
                                "completion_tokens", 0
                            )
                            continue

                        sub_text, sub_data, sub_err = await self._send_single_request(
                            client, sub, target_code, call_tracker=call_tracker, depth=depth + 1
                        )
                        if sub_err:
                            return None, None, sub_err
                        sub_results.append(sub_text or "")
                        if sub_data and "usage" in sub_data:
                            u = sub_data["usage"]
                            total_usage["prompt_tokens"] += u.get("prompt_tokens", 0)
                            total_usage["completion_tokens"] += u.get("completion_tokens", 0)
                            total_usage["total_tokens"] += u.get("total_tokens", 0)

                        if "\ufffd" not in (sub_text or ""):
                            write_cache(
                                sub_key,
                                {
                                    "translated_text": sub_text,
                                    "total_tokens": sub_data.get("usage", {}).get("total_tokens", 0)
                                    if sub_data
                                    else 0,
                                    "completion_tokens": sub_data.get("usage", {}).get(
                                        "completion_tokens", 0
                                    )
                                    if sub_data
                                    else 0,
                                    "source": "auto",
                                    "target": target_code,
                                },
                                self.cache_dir,
                            )
                    return " ".join(sub_results), {"usage": total_usage}, None

                return (
                    None,
                    None,
                    StructuredError(
                        code="OUTPUT_TRUNCATED",
                        message="Model truncated the output due to token limits.",
                        retryable=False,
                    ),
                )

            return message_content, data, None

        except Exception as e:
            if call_id:
                update_spend_ledger(
                    call_id=call_id,
                    status="completed",
                    http_status=response_2xx.status_code,
                    billed_units=0,
                    actual_cost_inr=est_cost,
                    ledger_path=self.ledger_path,
                )
            return (
                None,
                None,
                StructuredError(
                    code="UNKNOWN_BILLED",
                    message=f"Request succeeded with HTTP {response_2xx.status_code} and was billed, but response processing failed: {str(e)}",
                    retryable=False,
                ),
            )

    async def translate(
        self,
        text: str,
        target_language: str,
        source_language: Optional[str] = None,
    ) -> TranslateOutput:
        """Translates text with full validation, caching, chunking, and pivot routing."""
        # Validate target language
        target_code, target_name = normalize_language(target_language)
        if not target_code or not target_name:
            return TranslateOutput(
                success=False,
                source_language=source_language or "unknown",
                target_language=target_language,
                error=StructuredError(
                    code="UNSUPPORTED_LANGUAGE",
                    message=f"Target language '{target_language}' is not supported. Must be English or one of 22 scheduled Indian languages.",
                    retryable=False,
                ),
            )

        # Validate source language if supplied
        source_code: Optional[str] = None
        source_name: Optional[str] = None
        if source_language:
            source_code, source_name = normalize_language(source_language)
            if not source_code or not source_name:
                return TranslateOutput(
                    success=False,
                    source_language=source_language,
                    target_language=target_name,
                    error=StructuredError(
                        code="UNSUPPORTED_LANGUAGE",
                        message=f"Source language '{source_language}' is not supported. Must be English or one of 22 scheduled Indian languages.",
                        retryable=False,
                    ),
                )

        if not text.strip():
            return TranslateOutput(
                success=True,
                translated_text="",
                source_language=source_name or "auto",
                target_language=target_name,
                chunk_count=0,
                chunks_count=0,
            )

        # Check API key before executing network requests
        if not self.api_key:
            return TranslateOutput(
                success=False,
                source_language=source_name or "auto",
                target_language=target_name,
                error=StructuredError(
                    code="AUTH_ERROR",
                    message="BODHAN_TRANSLATE_KEY is missing from environment.",
                    retryable=False,
                ),
            )

        call_tracker = CallTracker(max_calls=self.max_http_calls)

        # Pivot via English if Indic-to-Indic (44 directions rule)
        is_indic_to_indic = source_code is not None and source_code != "en" and target_code != "en"

        if is_indic_to_indic:
            # Step 1: Translate Source -> English
            step1_out = await self._translate_direct(
                text, target_code="en", source_code=source_code, call_tracker=call_tracker
            )
            if not step1_out.success:
                return step1_out

            # Step 2: Translate English -> Target
            step2_out = await self._translate_direct(
                step1_out.translated_text,
                target_code=target_code,
                source_code="en",
                call_tracker=call_tracker,
            )
            if not step2_out.success:
                return step2_out

            combined_chunks = step1_out.chunk_count + step2_out.chunk_count
            combined_completion = step1_out.completion_tokens + step2_out.completion_tokens
            combined_total = step1_out.total_tokens + step2_out.total_tokens
            combined_cost = calculate_translate_cost_inr(combined_completion)
            both_cached = step1_out.cache_hit and step2_out.cache_hit

            return TranslateOutput(
                success=True,
                translated_text=step2_out.translated_text,
                source_language=source_name or "auto",
                target_language=target_name,
                chunk_count=combined_chunks,
                chunks_count=combined_chunks,
                cache_hit=both_cached,
                cached=both_cached,
                pivoted_via_english=True,
                completion_tokens=combined_completion,
                total_tokens=combined_total,
                estimated_cost_inr=combined_cost,
            )

        return await self._translate_direct(
            text, target_code=target_code, source_code=source_code, call_tracker=call_tracker
        )

    async def _translate_direct(
        self,
        text: str,
        target_code: str,
        source_code: Optional[str] = None,
        call_tracker: Optional[CallTracker] = None,
    ) -> TranslateOutput:
        """Executes a direct translation step (English <-> Indic)."""
        tracker = call_tracker or CallTracker(max_calls=self.max_http_calls)
        norm_source = source_code or "auto"
        cache_key = get_cache_key(MODEL_NAME, norm_source, target_code, text)

        # 1. Check disk cache
        cached_val = read_cache(cache_key, self.cache_dir)
        if cached_val is not None:
            if (
                not is_mock_transport(self.custom_client)
                or os.getenv("BODHAN_RECORD_MOCK_SPEND") == "1"
            ):
                log_spend_cache_hit(
                    model=MODEL_NAME,
                    caller="indic_translate",
                    run_id=self.run_id,
                    ledger_path=self.ledger_path,
                )
            c_toks = cached_val.get("completion_tokens", 0)
            tot_toks = cached_val.get("total_tokens", 0)
            chunks_n = cached_val.get("chunk_count", cached_val.get("chunks_count", 1))
            return TranslateOutput(
                success=True,
                translated_text=cached_val.get("translated_text", ""),
                source_language=norm_source,
                target_language=target_code,
                chunk_count=chunks_n,
                chunks_count=chunks_n,
                cache_hit=True,
                cached=True,
                completion_tokens=c_toks,
                total_tokens=tot_toks,
                estimated_cost_inr=0.0,
            )

        # 2. Chunk text
        chunks = chunk_text(text, max_chars=self.max_chunk_chars)
        translated_parts: List[str] = []
        total_tokens = 0
        total_completion_tokens = 0

        async def execute_all(client: httpx.AsyncClient):
            nonlocal total_tokens, total_completion_tokens
            for chunk in chunks:
                chunk_key = get_cache_key(MODEL_NAME, norm_source, target_code, chunk)
                cached_chunk = read_cache(chunk_key, self.cache_dir)
                if cached_chunk is not None:
                    translated_parts.append(cached_chunk.get("translated_text", ""))
                    total_tokens += cached_chunk.get("total_tokens", 0)
                    total_completion_tokens += cached_chunk.get("completion_tokens", 0)
                    continue

                res_text, res_data, err = await self._send_single_request(
                    client, chunk, target_code=target_code, call_tracker=tracker
                )
                if err:
                    return err

                translated_parts.append(res_text or "")
                usage = res_data.get("usage", {}) if res_data else {}
                c_toks = usage.get("completion_tokens", 0)
                tot_toks = usage.get("total_tokens", 0)
                total_tokens += tot_toks
                total_completion_tokens += c_toks

                # Cache individual successful, non-truncated chunk
                if "\ufffd" not in res_text:
                    write_cache(
                        chunk_key,
                        {
                            "translated_text": res_text,
                            "total_tokens": tot_toks,
                            "completion_tokens": c_toks,
                            "source": norm_source,
                            "target": target_code,
                        },
                        self.cache_dir,
                    )
            return None

        if self.custom_client:
            err = await execute_all(self.custom_client)
        else:
            async with httpx.AsyncClient() as client:
                err = await execute_all(client)

        if err:
            return TranslateOutput(
                success=False,
                source_language=norm_source,
                target_language=target_code,
                chunk_count=len(chunks),
                chunks_count=len(chunks),
                error=err,
            )

        final_translated = (
            "\n\n".join(translated_parts) if len(chunks) > 1 else "".join(translated_parts)
        )
        if "\ufffd" in final_translated:
            return TranslateOutput(
                success=False,
                source_language=norm_source,
                target_language=target_code,
                chunk_count=len(chunks),
                chunks_count=len(chunks),
                error=StructuredError(
                    code="UNICODE_REPLACEMENT_CHAR",
                    message="Final translation output contains invalid Unicode replacement character (U+FFFD).",
                    retryable=False,
                ),
            )

        cost_inr = calculate_translate_cost_inr(total_completion_tokens)

        # Cache full result ONLY on success
        write_cache(
            cache_key,
            {
                "translated_text": final_translated,
                "chunk_count": len(chunks),
                "chunks_count": len(chunks),
                "total_tokens": total_tokens,
                "completion_tokens": total_completion_tokens,
                "estimated_cost_inr": cost_inr,
                "source": norm_source,
                "target": target_code,
            },
            self.cache_dir,
        )

        return TranslateOutput(
            success=True,
            translated_text=final_translated,
            source_language=norm_source,
            target_language=target_code,
            chunk_count=len(chunks),
            chunks_count=len(chunks),
            cache_hit=False,
            cached=False,
            completion_tokens=total_completion_tokens,
            total_tokens=total_tokens,
            estimated_cost_inr=cost_inr,
        )
