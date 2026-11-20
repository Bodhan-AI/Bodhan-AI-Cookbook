from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import threading
import time
import unicodedata
import uuid
from collections import Counter
from difflib import SequenceMatcher
from decimal import Decimal
from functools import wraps
from pathlib import Path
from typing import Any

import httpx

from bodhan_mcp.config import CostLedger, Settings
from bodhan_mcp.errors import ToolError
from bodhan_mcp.media import (
    SUPPORTED_AUDIO,
    audio_duration,
    convert_wav_to_mp3,
    image_pages,
    inspect_media_file,
    join_wav_files,
    parse_srt,
    resolve_input,
    read_text_source,
    sha256_file,
    split_audio,
    split_text,
    tts_chunks,
    wav_duration,
    write_srt,
)

BODHAN_BASE = "https://api.bodhan.ai"
LANGUAGES = {
    "as", "bhb", "bho", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok",
    "ks", "mai", "ml", "mni", "mr", "ne", "or", "pa", "sa", "sat", "sd",
    "ta", "te", "ur",
}
TRANSLATION_LANGUAGES = (
    LANGUAGES - {"bhb", "bho"}
) | {"ks-Deva", "mni-Beng", "sd-Arab"}
VOICES = {
    "en": ("Kavya", "Amit"),
    "as": ("Prastuti", "Ankur"), "bn": ("Ishita", "Sourav"),
    "brx": ("Gwrbw", "Sansuma"), "doi": ("Preeti", "Sham"),
    "gu": ("Dhara", "Parth"), "hi": ("Kavya", "Suhani", "Amit"),
    "kn": ("Deepika", "Adarsh"), "kok": ("Anjali", "Sandeep"),
    "ks": ("Zoon", "Ishfaq"), "mai": ("Vaidehi", "Madhukar"),
    "ml": ("Lakshmi", "Kiran"), "mni": ("Thoibi", "Chaoba"),
    "mr": ("Anagha", "Chinmay"), "ne": ("Srijana", "Sagar"),
    "or": ("Itishree", "Akash"), "pa": ("Kaur", "Manpreet"),
    "sa": ("Bharati", "Aryaman"), "sat": ("Phulmani", "Sibu"),
    "sd": ("Moomal", "Rano"), "ta": ("Anitha", "Arun"),
    "te": ("Sravani", "Vamsi"), "ur": ("Saba", "Zaid"),
}
PRICES = {
    "transcribe_per_minute": Decimal("0.10"),
    "translate_per_10k_tokens": Decimal("0.20"),
    "speak_per_10k_chars": Decimal("6"),
    "ocr_per_page": Decimal("0.20"),
}
TRANSLATION_CHUNK_CHARS = 2500
TRANSLATION_OUTPUT_TOKEN_CAP = 2048
TTS_CHUNK_CHARS = 280


def _uncertain_outcome_cost(error: ToolError) -> Decimal:
    if error.details and error.details.get("outcome") == "unknown":
        return Decimal(str(error.details.get("reserved_cost_inr", "0")))
    return Decimal("0")


def _serialize_tool_call(function: Any) -> Any:
    @wraps(function)
    def wrapped(self: ToolService, *args: Any, **kwargs: Any) -> Any:
        with self._operation_lock:
            return function(self, *args, **kwargs)

    return wrapped


class ToolService:
    def __init__(
        self,
        settings: Settings | None = None,
        input_roots: tuple[Path, ...] | None = None,
        output_dir: Path | None = None,
        ledger: CostLedger | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings or Settings.from_env(output_dir)
        self.output_dir = (output_dir or self.settings.output_dir).resolve()
        self.input_roots = tuple(
            dict.fromkeys(
                [root.resolve() for root in (input_roots or (Path.cwd(),))]
                + [self.output_dir]
            )
        )
        self.artifact_dir = self.output_dir / "artifacts"
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.ledger = ledger or CostLedger()
        self._http = http_client or httpx.Client(timeout=self.settings.timeout_seconds)
        self._semaphore = threading.BoundedSemaphore(2)
        self._operation_lock = threading.RLock()
        self._cache: dict[str, dict[str, Any]] = {}
        self._request_cache: dict[str, dict[str, Any]] = {}
        self._retry_count = 0
        self._request_count = 0

    def close(self) -> None:
        self._http.close()

    def _fingerprint(self, model: str, operation: str, material: str) -> str:
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        return hashlib.sha256(f"{model}|{operation}|{digest}".encode()).hexdigest()

    def _cached(self, fingerprint: str) -> dict[str, Any] | None:
        cached = self._cache.get(fingerprint)
        if cached is None:
            return None
        result = dict(cached)
        result["cached"] = True
        result["cost_inr"] = 0.0
        result["estimated_cost_inr"] = 0.0
        result["cost_basis"] = "cached result; no new model request"
        return result

    def _call(
        self,
        method: str,
        url: str,
        model: str,
        reservation: Decimal,
        *,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        self.ledger.reserve(reservation)
        try:
            key = self.settings.api_key(model)
            request_headers = {"Authorization": f"Bearer {key}", **(headers or {})}
            with self._semaphore:
                for attempt in range(self.settings.max_retries + 1):
                    try:
                        self._request_count += 1
                        response = self._http.request(
                            method, url, headers=request_headers, timeout=self.settings.timeout_seconds, **kwargs
                        )
                    except httpx.TimeoutException as exc:
                        if isinstance(exc, (httpx.ConnectTimeout, httpx.PoolTimeout)) and attempt < self.settings.max_retries:
                            self._retry_count += 1
                            time.sleep(min(2**attempt * 0.4, 4))
                            continue
                        if isinstance(exc, (httpx.ConnectTimeout, httpx.PoolTimeout)):
                            raise ToolError(
                                "request_timeout",
                                "Bodhan connection timed out before the request was sent.",
                                True,
                            ) from exc
                        raise ToolError(
                            "provider_outcome_unknown",
                            "Bodhan may have processed this paid request, but the response timed out. It was not retried to avoid duplicate charges.",
                            False,
                            {"outcome": "unknown", "reserved_cost_inr": str(reservation)},
                        ) from exc
                    except httpx.RequestError as exc:
                        if isinstance(exc, httpx.ConnectError) and attempt < self.settings.max_retries:
                            self._retry_count += 1
                            time.sleep(min(2**attempt * 0.4, 4))
                            continue
                        if isinstance(exc, httpx.ConnectError):
                            raise ToolError(
                                "network_error",
                                "Could not connect to Bodhan before sending the request.",
                                True,
                            ) from exc
                        raise ToolError(
                            "provider_outcome_unknown",
                            "The Bodhan connection failed after the request may have been sent. It was not retried to avoid duplicate charges.",
                            False,
                            {"outcome": "unknown", "reserved_cost_inr": str(reservation)},
                        ) from exc
                    if response.status_code == 429:
                        if attempt < self.settings.max_retries:
                            self._retry_count += 1
                            retry_after = response.headers.get("Retry-After")
                            try:
                                delay = min(max(0.0, float(retry_after)), 10) if retry_after else min(2**attempt * 0.4, 4)
                            except ValueError:
                                delay = min(2**attempt * 0.4, 4)
                            time.sleep(delay)
                            continue
                    if response.is_error:
                        error_code = "provider_error"
                        error_message = "Bodhan rejected the request."
                        try:
                            body = response.json()
                            error = body.get("error", body) if isinstance(body, dict) else body
                            if isinstance(error, dict):
                                error_code = str(error.get("code") or error_code)
                                error_message = str(
                                    error.get("message") or error.get("detail") or error_message
                                )
                            elif error:
                                error_message = str(error)
                        except (ValueError, AttributeError):
                            pass
                        error_message = error_message.replace(key, "[REDACTED]")
                        if response.status_code >= 500:
                            raise ToolError(
                                "provider_outcome_unknown",
                                "Bodhan returned a server error after receiving the request; it was not retried to avoid duplicate charges.",
                                False,
                                {
                                    "http_status": response.status_code,
                                    "provider_code": error_code,
                                    "outcome": "unknown",
                                    "reserved_cost_inr": str(reservation),
                                },
                            )
                        code = "authentication_failed" if response.status_code == 401 else (
                            "rate_limited" if response.status_code == 429 else
                            "provider_unavailable" if response.status_code >= 500 else
                            "endpoint_not_found" if response.status_code == 404 else
                            "model_rejected_input"
                        )
                        raise ToolError(
                            code,
                            error_message[:500],
                            response.status_code == 429 or response.status_code >= 500,
                            {"http_status": response.status_code, "provider_code": error_code},
                        )
                    return response
        except ToolError as exc:
            if exc.details and exc.details.get("outcome") == "unknown":
                self.ledger.settle(reservation, reservation)
            else:
                self.ledger.cancel(reservation)
            raise
        raise ToolError("provider_unavailable", "Bodhan request failed after bounded retries.", True)

    def _save_text(self, name: str, text: str, suffix: str = ".md") -> str:
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        path = self.artifact_dir / f"{name}-{uuid.uuid4().hex[:8]}{suffix}"
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
        return str(path)

    def _save_json(self, name: str, value: Any) -> str:
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        path = self.artifact_dir / f"{name}-{uuid.uuid4().hex[:8]}.json"
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
        return str(path)

    @_serialize_tool_call
    def transcribe_audio(
        self,
        file_path: str,
        language: str | None = None,
        window_seconds: float = 30.0,
        include_segments: bool = False,
    ) -> dict[str, Any]:
        if language is not None and language not in LANGUAGES:
            raise ToolError("unsupported_language", f"Unsupported transcription language code: {language}.")
        if not isinstance(window_seconds, (int, float)) or not 1 <= window_seconds <= 30:
            raise ToolError("invalid_tool_arguments", "window_seconds must be between 1 and 30.")
        path = resolve_input(file_path, self.input_roots)
        if path.suffix.lower() not in SUPPORTED_AUDIO:
            raise ToolError("unsupported_file_type", "Transcription accepts WAV, FLAC, OGG, and MP3 files.")
        duration = audio_duration(path)
        fingerprint = self._fingerprint(
            "indic-transcribe",
            "transcribe",
            f"{sha256_file(path)}|{language or ''}|{window_seconds}",
        )
        cached = self._cached(fingerprint)
        if cached:
            return cached
        transcripts: list[str] = []
        total_cost = Decimal("0")
        input_seconds = Decimal("0")
        retries_before = self._retry_count
        start_time = time.perf_counter()
        for chunk_path, seconds in split_audio(path, window_seconds):
            estimate = (Decimal(str(seconds)) / Decimal(60)) * PRICES["transcribe_per_minute"]
            chunk_fingerprint = self._fingerprint(
                "indic-transcribe",
                "transcribe_chunk",
                f"{sha256_file(chunk_path)}|{language or ''}",
            )
            cached_chunk = self._request_cache.get(chunk_fingerprint)
            if cached_chunk:
                chunk_text = str(cached_chunk["text"])
                transcripts.append(chunk_text)
                input_seconds += Decimal(str(seconds))
                if path.suffix.lower() == ".wav":
                    chunk_path.unlink(missing_ok=True)
                continue
            payload: dict[str, Any] = {"model": "indic-transcribe"}
            if language:
                payload["language"] = language
            try:
                with chunk_path.open("rb") as audio_file:
                    response = self._call(
                        "POST",
                        f"{BODHAN_BASE}/v1/audio/transcriptions",
                        "indic-transcribe",
                        estimate,
                        data=payload,
                        files={"file": (chunk_path.name, audio_file, "audio/wav")},
                    )
                actual = estimate
                self.ledger.settle(estimate, actual)
                total_cost += actual
                input_seconds += Decimal(str(seconds))
                try:
                    text = str(response.json()["text"]).strip()
                except (ValueError, KeyError, TypeError, IndexError) as exc:
                    raise ToolError("invalid_model_response", "Bodhan returned an invalid transcription response.") from exc
                transcripts.append(text)
                self._request_cache[chunk_fingerprint] = {"text": text, "cost_inr": str(actual)}
            except ToolError as exc:
                if not transcripts:
                    raise
                transcript = "\n".join(part for part in transcripts if part)
                artifact = self._save_text("transcript-partial", transcript)
                return {
                    "ok": False, "tool": "transcribe_audio", "transcript": transcript,
                    "source_duration_seconds": round(duration, 3), "chunks": len(transcripts),
                    "model": "indic-transcribe", "input_fingerprint": fingerprint,
                    "cost_inr": float(total_cost + _uncertain_outcome_cost(exc)),
                    "cost_basis": "measured audio duration at assignment rate; uncertain request reserved conservatively",
                    "usage": {"input_audio_seconds": float(input_seconds)},
                    "retries": self._retry_count - retries_before,
                    "latency_ms": round((time.perf_counter() - start_time) * 1000),
                    "artifact_path": artifact,
                    "warnings": ["Only part of the audio was transcribed."],
                    "error": {
                        "code": exc.code, "message": exc.message,
                        "retryable": exc.retryable, "details": exc.details or {},
                    },
                }
            finally:
                if path.suffix.lower() == ".wav":
                    chunk_path.unlink(missing_ok=True)
        transcript = "\n".join(part for part in transcripts if part)
        if not transcript:
            raise ToolError("empty_model_response", "Bodhan returned an empty transcript.")
        artifact = self._save_text("transcript", transcript)
        language_detection = self._detect_indic_language(transcript)
        result = {
            "ok": True, "tool": "transcribe_audio", "transcript": transcript,
            "detected_language": language_detection,
            "source_duration_seconds": round(duration, 3), "chunks": len(transcripts),
            "model": "indic-transcribe", "cost_inr": float(total_cost),
            "cost_basis": "measured audio duration at assignment rate",
            "input_fingerprint": fingerprint,
            "usage": {"input_audio_seconds": float(input_seconds)},
            "retries": self._retry_count - retries_before,
            "latency_ms": round((time.perf_counter() - start_time) * 1000),
            "artifact_path": artifact,
            "warnings": ["Language detection is based on output script and may be inconclusive."],
        }
        if include_segments:
            result["warnings"].append(
                "Bodhan did not return timestamps; no SRT or timed segment artifact was generated."
            )
        self._cache[fingerprint] = result
        return result

    @_serialize_tool_call
    def translate_text(
        self,
        text: str | None,
        target_language: str,
        script: str = "native",
        text_file_path: str | None = None,
    ) -> dict[str, Any]:
        if target_language not in TRANSLATION_LANGUAGES:
            raise ToolError("unsupported_language", f"Unsupported translation language code: {target_language}.")
        if script not in {"native", "roman", "codemix"}:
            raise ToolError("invalid_tool_arguments", "script must be native, roman, or codemix.")
        if target_language == "en" and script != "native":
            raise ToolError("invalid_tool_arguments", "The Bodhan API does not allow script options for English.")
        if target_language in {"ks-Deva", "sd-Arab"} and script == "codemix":
            raise ToolError(
                "unsupported_script_for_language",
                f"The Bodhan API does not support codemix for {target_language}.",
            )
        text = read_text_source(text, text_file_path, self.input_roots)
        if text_file_path is None and self._contains_indic_script(text):
            raise ToolError(
                "text_file_required",
                "Pass Indic text through text_file_path so the agent does not generate or rewrite it.",
            )
        chunks = split_text(text, TRANSLATION_CHUNK_CHARS)
        fingerprint = self._fingerprint(
            "indic-translate", "translate", f"{target_language}|{script}|{json.dumps(chunks, ensure_ascii=False)}"
        )
        cached = self._cached(fingerprint)
        if cached:
            return cached
        outputs: list[str] = []
        costs: list[Decimal] = []
        total_output_tokens = 0
        usage_reported = True
        retries_before = self._retry_count
        started = time.perf_counter()

        def partial_result(error: ToolError) -> dict[str, Any]:
            partial_text = "\n\n".join(outputs)
            artifact = self._save_text("translation-partial", partial_text) if partial_text else None
            return {
                "ok": False, "tool": "translate_text", "translated_text": partial_text,
                "target_language": target_language, "script": script, "chunks": len(outputs),
                "coverage": "partial" if outputs else "none", "model": "indic-translate",
                "input_fingerprint": fingerprint, "usage": {"output_tokens": total_output_tokens},
                "cost_basis": "reported output-token usage" if usage_reported else "reserved maximum output-token estimate",
                "cost_inr": float(sum(costs, Decimal("0")) + _uncertain_outcome_cost(error)),
                "verification": {
                    "name": "translation_chunk_coverage",
                    "status": "partial",
                    "checks": {
                        "source_chunks": len(chunks),
                        "translated_chunks": len(outputs),
                        "chunk_order_preserved": True,
                        "empty_outputs": False,
                        "truncation_detected": error.code == "translation_incomplete",
                    },
                    "failed_checks": ["all_source_chunks_translated"],
                    "warnings": ["Semantic translation accuracy is not automatically verified."],
                },
                "retries": self._retry_count - retries_before,
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "artifact_path": artifact,
                "warnings": ["Not every source chunk was translated."],
                "error": {
                    "code": error.code, "message": error.message,
                    "retryable": error.retryable, "details": error.details or {},
                },
            }

        for chunk in chunks:
            chunk_fingerprint = self._fingerprint(
                "indic-translate", "translate_chunk", f"{target_language}|{script}|{chunk}"
            )
            cached_chunk = self._request_cache.get(chunk_fingerprint)
            if cached_chunk:
                if cached_chunk.get("error"):
                    error = cached_chunk["error"]
                    return partial_result(ToolError(error["code"], error["message"], False))
                outputs.append(str(cached_chunk["text"]))
                total_output_tokens += int(cached_chunk["output_tokens"])
                usage_reported = usage_reported and cached_chunk.get("usage_reported", True)
                continue
            reservation = (
                Decimal(TRANSLATION_OUTPUT_TOKEN_CAP) / Decimal(10000)
            ) * PRICES["translate_per_10k_tokens"]
            try:
                response = self._call(
                    "POST",
                    f"{BODHAN_BASE}/v1/chat/completions",
                    "indic-translate",
                    reservation,
                    json={
                        "model": "indic-translate",
                        "messages": [{"role": "user", "content": chunk}],
                        "target_language_code": target_language,
                        "target_script": script,
                    },
                )
            except ToolError as exc:
                if outputs:
                    return partial_result(exc)
                raise
            try:
                body = response.json()
                choices = body["choices"]
                choice = choices[0]
                translated = choice["message"]["content"]
                usage = body.get("usage") or {}
                if body.get("truncated") or choice.get("finish_reason") == "length":
                    self.ledger.settle(reservation, reservation)
                    costs.append(reservation)
                    self._request_cache[chunk_fingerprint] = {
                        "error": {
                            "code": "translation_incomplete",
                            "message": "Bodhan truncated a translation chunk; use smaller source chunks or a lower-complexity task.",
                        }
                    }
                    return partial_result(ToolError(
                        "translation_incomplete",
                        "Bodhan truncated a translation chunk; use smaller source chunks or a lower-complexity task.",
                    ))
                if "completion_tokens" not in usage:
                    usage_reported = False
                tokens = int(usage.get("completion_tokens", TRANSLATION_OUTPUT_TOKEN_CAP))
                actual = Decimal(tokens) / Decimal(10000) * PRICES["translate_per_10k_tokens"]
            except (ValueError, KeyError, TypeError) as exc:
                self.ledger.cancel(reservation)
                error = ToolError("invalid_model_response", "Bodhan returned an invalid translation response.")
                if outputs:
                    return partial_result(error)
                raise error from exc
            if not isinstance(translated, str) or not translated.strip():
                self.ledger.cancel(reservation)
                error = ToolError("empty_model_response", "Bodhan returned an empty translation.")
                if outputs:
                    return partial_result(error)
                raise error
            self.ledger.settle(reservation, actual)
            costs.append(actual)
            total_output_tokens += tokens
            outputs.append(translated.strip())
            self._request_cache[chunk_fingerprint] = {
                "text": translated.strip(),
                "cost_inr": str(actual),
                "output_tokens": tokens,
                "usage_reported": "completion_tokens" in usage,
            }
            if self.ledger.maximum is not None and self.ledger.spent > self.ledger.maximum:
                return partial_result(ToolError(
                    "budget_exceeded",
                    "Reported translation usage exceeded the reserved cost estimate; stopping further model calls.",
                ))
        translated_text = "\n\n".join(outputs)
        artifact = self._save_text("translation", translated_text)
        result = {
            "ok": True, "tool": "translate_text", "translated_text": translated_text,
            "target_language": target_language, "script": script, "chunks": len(chunks),
            "coverage": "all source chunks translated", "model": "indic-translate",
            "cost_basis": "reported output-token usage" if usage_reported else "reserved maximum output-token estimate",
            "input_fingerprint": fingerprint,
            "usage": {"output_tokens": total_output_tokens},
            "retries": self._retry_count - retries_before,
            "cost_inr": float(sum(costs, Decimal("0"))),
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "artifact_path": artifact,
            "verification": {
                "name": "translation_chunk_coverage",
                "status": "passed",
                "checks": {
                    "source_chunks": len(chunks),
                    "translated_chunks": len(outputs),
                    "chunk_order_preserved": True,
                    "empty_outputs": False,
                    "truncation_detected": False,
                },
                "failed_checks": [],
                "warnings": ["Semantic translation accuracy is not automatically verified."],
            },
            "warnings": ["Semantic translation accuracy is not automatically verified."],
        }
        self._cache[fingerprint] = result
        return result

    @_serialize_tool_call
    def translate_subtitles(
        self,
        segments_file_path: str,
        target_language: str,
    ) -> dict[str, Any]:
        if target_language not in TRANSLATION_LANGUAGES:
            raise ToolError("unsupported_language", f"Unsupported translation language code: {target_language}.")
        path = resolve_input(segments_file_path, self.input_roots)
        if path.suffix.lower() != ".srt":
            raise ToolError("unsupported_file_type", "Subtitle translation requires an SRT file.")
        segments = parse_srt(path.read_text(encoding="utf-8"))
        translated_segments: list[dict[str, object]] = []
        total_cost = Decimal("0")
        started = time.perf_counter()
        for segment in segments:
            temporary_text = self.artifact_dir / f"subtitle-source-{uuid.uuid4().hex[:8]}.txt"
            temporary_text.write_text(str(segment["text"]), encoding="utf-8")
            try:
                translated = self.translate_text(
                    None,
                    target_language,
                    "native",
                    str(temporary_text),
                )
            except ToolError as exc:
                partial_artifact = (
                    self._save_text(
                        "subtitles-partial",
                        write_srt(translated_segments),
                        ".srt",
                    )
                    if translated_segments else None
                )
                return {
                    "ok": False,
                    "tool": "translate_subtitles",
                    "target_language": target_language,
                    "translated_segments": len(translated_segments),
                    "source_segments": len(segments),
                    "coverage": "partial" if translated_segments else "none",
                    "cost_inr": float(total_cost + _uncertain_outcome_cost(exc)),
                    "latency_ms": round((time.perf_counter() - started) * 1000),
                    "artifact_path": partial_artifact,
                    "warnings": ["Not all subtitle captions were translated."],
                    "error": {
                        "code": exc.code,
                        "message": exc.message,
                        "retryable": exc.retryable,
                        "details": exc.details or {},
                    },
                }
            finally:
                temporary_text.unlink(missing_ok=True)
            total_cost += Decimal(str(translated.get("cost_inr", 0)))
            if not translated.get("ok"):
                partial_artifact = (
                    self._save_text(
                        "subtitles-partial",
                        write_srt(translated_segments),
                        ".srt",
                    )
                    if translated_segments else None
                )
                return {
                    "ok": False,
                    "tool": "translate_subtitles",
                    "target_language": target_language,
                    "translated_segments": len(translated_segments),
                    "source_segments": len(segments),
                    "coverage": "partial" if translated_segments else "none",
                    "cost_inr": float(total_cost),
                    "latency_ms": round((time.perf_counter() - started) * 1000),
                    "artifact_path": partial_artifact,
                    "warnings": ["Not all subtitle captions were translated."],
                    "error": translated.get(
                        "error",
                        {"code": "translation_failed", "message": "A subtitle caption could not be translated."},
                    ),
                }
            translated_segments.append(
                {
                    "start": segment["start"],
                    "end": segment["end"],
                    "text": translated["translated_text"],
                }
            )
        artifact = self._save_text(
            "subtitles",
            write_srt(translated_segments),
            ".srt",
        )
        return {
            "ok": True,
            "tool": "translate_subtitles",
            "target_language": target_language,
            "translated_segments": len(translated_segments),
            "source_segments": len(segments),
            "coverage": "all subtitle segments translated",
            "cost_inr": float(total_cost),
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "artifact_path": artifact,
            "warnings": ["Subtitle timing is inherited from audio chunk boundaries, not word alignment."],
        }

    @_serialize_tool_call
    def generate_speech(
        self,
        text: str | None,
        language: str,
        voice: str | None = None,
        style: str | None = None,
        text_file_path: str | None = None,
        output_format: str = "wav",
    ) -> dict[str, Any]:
        if output_format not in {"wav", "mp3"}:
            raise ToolError("invalid_tool_arguments", "output_format must be wav or mp3.")
        if language not in VOICES:
            raise ToolError("unsupported_language", f"Indic-Speak does not list language code: {language}.")
        if style and style not in {
            "AIR style news", "Customer Care", "TV style news", "advertisements", "anger",
            "children's stories", "disgust", "educational lecture", "fear", "happy", "news",
            "sad", "single person narration audiobook", "surprise",
        }:
            raise ToolError("invalid_tool_arguments", "The requested speech style is not in Bodhan's documented list.")
        selected_voice = voice or VOICES[language][0]
        if selected_voice not in {name for pair in VOICES.values() for name in pair}:
            raise ToolError("invalid_tool_arguments", "The voice name is not in Bodhan's documented voice library.")
        text = read_text_source(text, text_file_path, self.input_roots)
        if text_file_path is None and self._contains_indic_script(text):
            raise ToolError(
                "text_file_required",
                "Pass Indic text through text_file_path; inline Indic text is not accepted from the agent.",
            )
        chunks = tts_chunks(text, TTS_CHUNK_CHARS)
        fingerprint = self._fingerprint(
            "indic-speak",
            "speak",
            json.dumps([text, language, selected_voice, style, output_format], ensure_ascii=False),
        )
        cached = self._cached(fingerprint)
        if cached:
            return cached
        started = time.perf_counter()
        speech_files: list[Path] = []
        costs: list[Decimal] = []
        retries_before = self._retry_count
        temporary_dir = tempfile.TemporaryDirectory(prefix="bodhan-speech-")
        try:
            for index, chunk in enumerate(chunks):
                chunk_fingerprint = self._fingerprint(
                    "indic-speak",
                    "speak_chunk",
                    json.dumps([chunk, language, selected_voice, style], ensure_ascii=False),
                )
                cached_chunk = self._request_cache.get(chunk_fingerprint)
                if cached_chunk:
                    chunk_bytes = cached_chunk["audio"]
                else:
                    reservation = (
                        Decimal(len(chunk)) / Decimal(10000)
                    ) * PRICES["speak_per_10k_chars"]
                    instructions: dict[str, str] = {"lang": language}
                    if style:
                        instructions["style"] = style
                    response = self._call(
                        "POST",
                        f"{BODHAN_BASE}/v1/audio/speech",
                        "indic-speak",
                        reservation,
                        headers={"Content-Type": "application/json"},
                        json={
                            "model": "indic-speak",
                            "input": chunk,
                            "voice": selected_voice,
                            "instructions": json.dumps(instructions),
                        },
                    )
                    self.ledger.settle(reservation, reservation)
                    chunk_bytes = response.content
                    costs.append(reservation)
                    self._request_cache[chunk_fingerprint] = {
                        "audio": chunk_bytes,
                        "cost_inr": str(reservation),
                    }
                chunk_file = Path(temporary_dir.name) / f"part-{index:04d}.wav"
                chunk_file.write_bytes(chunk_bytes)
                if wav_duration(chunk_file) <= 0:
                    raise ToolError("invalid_audio_response", "Bodhan returned an empty audio file.")
                speech_files.append(chunk_file)
            output_path = self.artifact_dir / f"speech-{uuid.uuid4().hex[:8]}.wav"
            temporary_output = output_path.with_suffix(".tmp.wav")
            try:
                join_wav_files(speech_files, temporary_output)
                temporary_output.replace(output_path)
            finally:
                temporary_output.unlink(missing_ok=True)
        except ToolError as exc:
            if not speech_files:
                raise
            output_path = self.artifact_dir / f"speech-partial-{uuid.uuid4().hex[:8]}.wav"
            temporary_output = output_path.with_suffix(".tmp.wav")
            try:
                join_wav_files(speech_files, temporary_output)
                temporary_output.replace(output_path)
            finally:
                temporary_output.unlink(missing_ok=True)
            result = {
                "ok": False, "tool": "generate_speech", "artifact_path": str(output_path),
                "ai_generated": True, "duration_seconds": round(wav_duration(output_path), 3),
                "language": language, "voice": selected_voice, "input_characters": len(text),
                "model": "indic-speak", "input_fingerprint": fingerprint,
                "cost_basis": "input characters at assignment rate",
                "usage": {"input_characters": len(text)},
                "cost_inr": float(sum(costs, Decimal("0")) + _uncertain_outcome_cost(exc)),
                "verification": {"status": "not_run", "reason": exc.code},
                "retries": self._retry_count - retries_before,
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "warnings": ["Only part of the requested speech was generated."],
                "error": {
                    "code": exc.code, "message": exc.message,
                    "retryable": exc.retryable, "details": exc.details or {},
                },
            }
            result["output_format"] = "wav"
            if output_format == "mp3":
                result["warnings"].append("MP3 was not exported because speech generation was partial.")
            self._cache[fingerprint] = result
            return result
        finally:
            temporary_dir.cleanup()
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise ToolError("invalid_audio_response", "The generated WAV artifact is missing or empty.")
        duration = wav_duration(output_path)
        if duration <= 0:
            raise ToolError("invalid_audio_response", "The generated WAV artifact has no playable duration.")
        try:
            verification = self.transcribe_audio(str(output_path), language)
        except ToolError as exc:
            result = {
                "ok": False, "tool": "generate_speech", "artifact_path": str(output_path),
                "ai_generated": True, "duration_seconds": round(duration, 3),
                "language": language, "voice": selected_voice, "input_characters": len(text),
                "model": "indic-speak", "input_fingerprint": fingerprint,
                "cost_basis": "input characters at assignment rate",
                "usage": {"input_characters": len(text)},
                "cost_inr": float(sum(costs, Decimal("0")) + _uncertain_outcome_cost(exc)),
                "verification": {"status": "not_run", "reason": exc.code},
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "warnings": ["Speech was generated but its Bodhan back-transcription could not be completed."],
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "retryable": exc.retryable,
                    "details": exc.details or {},
                },
            }
            self._cache[fingerprint] = result
            return result
        verified_text = self._normalize(verification["transcript"])
        expected = self._normalize(text)
        match = self._text_match_metrics(expected, verified_text)
        failed_checks = []
        if match["token_coverage"] < 0.8:
            failed_checks.append("token_coverage")
        if match["sequence_similarity"] < 0.8:
            failed_checks.append("sequence_similarity")
        if not 0.6 <= match["length_ratio"] <= 1.5:
            failed_checks.append("length_ratio")
        verification_status = "failed" if failed_checks else "passed"
        verification_warnings = (
            ["Back-transcription indicates possible omissions, reordering, or repetition."]
            if failed_checks
            else ["Back-transcription is a consistency check, not a pronunciation or semantic quality guarantee."]
        )
        result = {
            "ok": verification_status == "passed", "tool": "generate_speech",
            "artifact_path": str(output_path), "ai_generated": True,
            "duration_seconds": round(duration, 3), "language": language,
            "voice": selected_voice, "input_characters": len(text),
            "model": "indic-speak",
            "cost_basis": "input characters at assignment rate, plus verification transcription",
            "input_fingerprint": fingerprint,
            "usage": {"input_characters": len(text)},
            "retries": self._retry_count - retries_before,
            "cost_inr": float(sum(costs, Decimal("0")) + Decimal(str(verification["cost_inr"]))),
            "verification": {
                "status": verification_status, "method": "Bodhan Indic-Transcribe back-transcription",
                "name": "speech_back_transcription",
                "token_coverage": match["token_coverage"],
                "sequence_similarity": match["sequence_similarity"],
                "length_ratio": match["length_ratio"],
                "expected_tokens": match["expected_tokens"],
                "back_transcribed_tokens": match["back_transcribed_tokens"],
                "repeated_or_extra_tokens": match["repeated_or_extra_tokens"],
                "missing_tokens": match["missing_tokens"],
                "checks": {
                    "audio_exists_and_nonempty": True,
                    "audio_decodes": True,
                    "duration_positive": True,
                    "chunk_order_checked_by_full_output_back_transcription": True,
                },
                "failed_checks": failed_checks,
                "warnings": verification_warnings,
                "transcript_artifact": verification["artifact_path"],
            },
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "warnings": verification_warnings,
        }
        result["output_format"] = output_format
        if output_format == "mp3":
            mp3_path = output_path.with_suffix(".mp3")
            convert_wav_to_mp3(output_path, mp3_path)
            if not mp3_path.is_file() or mp3_path.stat().st_size == 0 or audio_duration(mp3_path) <= 0:
                raise ToolError("audio_conversion_failed", "The MP3 artifact failed post-encoding validation.")
            result["wav_artifact_path"] = str(output_path)
            result["artifact_path"] = str(mp3_path)
        self._cache[fingerprint] = result
        return result

    @_serialize_tool_call
    def ocr_document(self, file_path: str, table_format: str = "html") -> dict[str, Any]:
        if table_format not in {"html", "markdown"}:
            raise ToolError("invalid_tool_arguments", "table_format must be html or markdown.")
        path = resolve_input(file_path, self.input_roots)
        inspection = inspect_media_file(path)
        expected_page_count = int(inspection.get("page_count", 1))
        fingerprint = self._fingerprint("indic-ocr", "ocr", f"{sha256_file(path)}|{table_format}")
        cached = self._cached(fingerprint)
        if cached:
            return cached
        page_results: list[dict[str, Any]] = []
        costs: list[Decimal] = []
        retries_before = self._retry_count
        started = time.perf_counter()

        def partial_result(error: ToolError) -> dict[str, Any]:
            combined_partial = "\n\n".join(
                f"## Page {page['page']}\n\n{page['text']}" for page in page_results
            )
            artifact = self._save_text("ocr-partial", combined_partial) if combined_partial else None
            page_numbers = [int(page["page"]) for page in page_results]
            empty_pages = [
                int(page["page"])
                for page in page_results
                if not str(page["text"]).strip()
            ]
            return {
                "ok": False, "tool": "ocr_document", "page_count": expected_page_count,
                "processed_pages": page_numbers,
                "page_results": page_results, "text": combined_partial,
                "table_format": table_format, "model": "indic-ocr",
                "input_fingerprint": fingerprint,
                "cost_basis": "page count at assignment rate",
                "usage": {"pages": len(page_results)},
                "cost_inr": float(sum(costs, Decimal("0")) + _uncertain_outcome_cost(error)),
                "retries": self._retry_count - retries_before,
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "artifact_path": artifact,
                "warnings": ["Only part of the document was processed."],
                "verification": {
                    "name": "ocr_page_coverage",
                    "status": "partial",
                    "checks": {
                        "expected_pages": expected_page_count,
                        "processed_pages": page_numbers,
                        "page_order_preserved": page_numbers == sorted(page_numbers),
                        "duplicate_pages": len(page_numbers) != len(set(page_numbers)),
                        "empty_pages": empty_pages,
                    },
                    "failed_checks": ["all_expected_pages_processed"],
                    "warnings": ["Table structure is preserved only to the extent returned by Bodhan OCR."],
                },
                "error": {
                    "code": error.code, "message": error.message,
                    "retryable": error.retryable, "details": error.details or {},
                },
            }

        for page_number, image_bytes, mime_type in image_pages(path):
            page_fingerprint = self._fingerprint(
                "indic-ocr", "ocr_page", f"{hashlib.sha256(image_bytes).hexdigest()}|{table_format}"
            )
            cached_page = self._request_cache.get(page_fingerprint)
            if cached_page:
                page_results.append({"page": page_number, "text": cached_page["text"]})
                continue
            reservation = PRICES["ocr_per_page"]
            data_uri = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
            try:
                response = self._call(
                    "POST",
                    f"{BODHAN_BASE}/v1/chat/completions",
                    "indic-ocr",
                    reservation,
                    headers={"Content-Type": "application/json"},
                    json={
                        "model": "indic-ocr",
                        "messages": [
                            {
                                "role": "user",
                                "content": [{"type": "image_url", "image_url": {"url": data_uri}}],
                            }
                        ],
                        "table_format": table_format,
                    },
                )
            except ToolError as exc:
                if page_results:
                    return partial_result(exc)
                raise
            self.ledger.settle(reservation, reservation)
            costs.append(reservation)
            try:
                text = response.json()["choices"][0]["message"]["content"]
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                error = ToolError("invalid_model_response", "Bodhan returned an invalid OCR response.")
                if page_results:
                    return partial_result(error)
                raise error from exc
            if not isinstance(text, str):
                raise ToolError("invalid_model_response", "Bodhan returned non-text OCR content.")
            page_results.append({"page": page_number, "text": text})
            self._request_cache[page_fingerprint] = {"text": text, "cost_inr": str(reservation)}
        combined = "\n\n".join(f"## Page {page['page']}\n\n{page['text']}" for page in page_results)
        page_numbers = [int(page["page"]) for page in page_results]
        empty_pages = [int(page["page"]) for page in page_results if not str(page["text"]).strip()]
        if (
            page_numbers != list(range(1, expected_page_count + 1))
            or len(page_numbers) != len(set(page_numbers))
            or empty_pages
        ):
            error = ToolError(
                "ocr_verification_failed",
                "OCR did not return one non-empty result for every page in source order.",
            )
            result = partial_result(error)
            result["warnings"].append(
                "One or more pages were missing, duplicated, out of order, or empty."
            )
            return result
        artifact = self._save_text("ocr", combined)
        result = {
            "ok": True, "tool": "ocr_document", "page_count": expected_page_count,
            "processed_pages": [page["page"] for page in page_results],
            "page_results": page_results, "text": combined, "table_format": table_format,
            "model": "indic-ocr", "cost_inr": float(sum(costs, Decimal("0"))),
            "cost_basis": "page count at assignment rate",
            "input_fingerprint": fingerprint,
            "usage": {"pages": len(page_results)},
            "retries": self._retry_count - retries_before,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "artifact_path": artifact,
            "verification": {
                "name": "ocr_page_coverage",
                "status": "passed",
                "checks": {
                    "expected_pages": expected_page_count,
                    "processed_pages": page_numbers,
                    "page_order_preserved": True,
                    "duplicate_pages": False,
                    "empty_pages": [],
                },
                "failed_checks": [],
                "warnings": ["Table structure is preserved only to the extent returned by Bodhan OCR."],
            },
            "warnings": ["Table structure is preserved only to the extent returned by Bodhan OCR."],
        }
        self._cache[fingerprint] = result
        return result

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join(ToolService._tokens(text))

    @staticmethod
    def _tokens(text: str) -> list[str]:
        tokens: list[str] = []
        current: list[str] = []
        for character in unicodedata.normalize("NFC", text.casefold()):
            if unicodedata.category(character)[0] in {"L", "M", "N"}:
                current.append(character)
            elif current:
                tokens.append("".join(current))
                current = []
        if current:
            tokens.append("".join(current))
        return tokens

    @classmethod
    def _text_match_metrics(cls, expected: str, received: str) -> dict[str, int | float]:
        expected_tokens = cls._tokens(expected)
        received_tokens = cls._tokens(received)
        expected_counts = Counter(expected_tokens)
        received_counts = Counter(received_tokens)
        matched = sum(min(count, received_counts[token]) for token, count in expected_counts.items())
        expected_count = len(expected_tokens)
        received_count = len(received_tokens)
        coverage = matched / expected_count if expected_count else 0.0
        sequence_similarity = (
            SequenceMatcher(None, expected_tokens, received_tokens, autojunk=False).ratio()
            if expected_count or received_count
            else 1.0
        )
        return {
            "token_coverage": round(coverage, 3),
            "sequence_similarity": round(sequence_similarity, 3),
            "length_ratio": round(received_count / expected_count, 3) if expected_count else 0.0,
            "expected_tokens": expected_count,
            "back_transcribed_tokens": received_count,
            "repeated_or_extra_tokens": max(0, received_count - matched),
            "missing_tokens": max(0, expected_count - matched),
        }

    @staticmethod
    def _contains_indic_script(text: str) -> bool:
        return any(
            ("\u0600" <= char <= "\u08ff")
            or ("\u0900" <= char <= "\u0dff")
            or ("\u0f00" <= char <= "\u109f")
            or ("\u1700" <= char <= "\u1cff")
            for char in text
        )

    @staticmethod
    def _detect_indic_language(text: str) -> dict[str, Any] | None:
        scripts = {
            "Devanagari": (0x0900, 0x097F, None),
            "Bengali": (0x0980, 0x09FF, None),
            "Gurmukhi": (0x0A00, 0x0A7F, "pa"),
            "Gujarati": (0x0A80, 0x0AFF, "gu"),
            "Odia": (0x0B00, 0x0B7F, "or"),
            "Tamil": (0x0B80, 0x0BFF, "ta"),
            "Telugu": (0x0C00, 0x0C7F, "te"),
            "Kannada": (0x0C80, 0x0CFF, "kn"),
            "Malayalam": (0x0D00, 0x0D7F, "ml"),
            "Arabic": (0x0600, 0x08FF, None),
            "Meitei_Mayek": (0xABC0, 0xABFF, "mni"),
            "Ol_Chiki": (0x1C50, 0x1C7F, "sat"),
            "Sora_Sompeng": (0x110D0, 0x110FF, "sat"),
        }
        counts = {
            script: sum(start <= ord(char) <= end for char in text)
            for script, (start, end, _) in scripts.items()
        }
        total = sum(counts.values())
        if not total:
            return {"language": None, "script": None, "status": "unknown", "method": "unicode_script"}
        script, count = max(counts.items(), key=lambda item: item[1])
        confidence = count / total
        if confidence < 0.6:
            return {
                "language": None,
                "script": "mixed",
                "status": "unknown",
                "method": "unicode_script",
                "confidence": round(confidence, 3),
            }
        language = scripts[script][2]
        return {
            "language": language,
            "script": script,
            "status": "probable" if language else "unknown",
            "method": "unicode_script",
            "confidence": round(confidence, 3),
            "ambiguity": None if language else "Multiple languages use this script.",
            "note": "Script heuristics are not definitive language identification.",
        }


def safe_tool_result(function: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
    try:
        result = function(*args, **kwargs)
    except ToolError as exc:
        estimated_cost = float((exc.details or {}).get("reserved_cost_inr", 0))
        result = {
            "ok": False,
            "cost_inr": estimated_cost,
            "estimated_cost_inr": estimated_cost,
            "actual_cost_inr": None,
            "error": {
                "code": exc.code,
                "message": exc.message,
                "retryable": exc.retryable,
                "details": exc.details or {},
            },
        }
    result.setdefault("estimated_cost_inr", result.get("cost_inr", 0.0))
    result.setdefault("actual_cost_inr", None)
    return result
