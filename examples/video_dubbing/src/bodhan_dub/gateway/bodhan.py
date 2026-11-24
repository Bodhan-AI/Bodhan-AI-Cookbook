"""
Typed API client for Bodhan Indic models:
- Indic-Transcribe (ASR): POST /v1/audio/transcriptions
- Indic-Translate (MT): POST /v1/chat/completions
- Indic-Speak (TTS): POST /v1/audio/speech
- Indic-OCR (Doc OCR): POST /v1/chat/completions (optional)

Encapsulates caching, cost tracking, budget gating, resilience, and policy checking.
"""

from __future__ import annotations

import asyncio
import io
import json
from typing import Any, Dict, Optional, Tuple

import httpx
import soundfile as sf

from bodhan_dub.config import Settings
from bodhan_dub.gateway.cache import DiskCache
from bodhan_dub.gateway.cost import BudgetGuard, CostLedger
from bodhan_dub.gateway.resilience import (
    CircuitBreaker,
    CircuitBreakerRegistry,
    execute_with_resilience,
)
from bodhan_dub.logging import PipelineLogger
from bodhan_dub.policy import HostAllowlistGuard, validate_bodhan_host


class UnknownVoiceOrLanguageError(Exception):
    """Raised when Bodhan returns 400/404/422 indicating an invalid/unknown voice or language."""

    def __init__(self, message: str, lang: Optional[str] = None, service: str = "tts"):
        super().__init__(message)
        self.lang = lang
        self.service = service


class BodhanClient:
    """Async client managing all interactions with Bodhan AI endpoints."""

    def __init__(
        self,
        settings: Settings,
        cache: DiskCache,
        ledger: CostLedger,
        budget_guard: BudgetGuard,
        logger: PipelineLogger,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        self.settings = settings
        self.cache = cache
        self.ledger = ledger
        self.budget_guard = budget_guard
        self.logger = logger

        # Single-flight deduplication
        self._in_flight: Dict[str, asyncio.Task[Any]] = {}
        self._in_flight_lock = asyncio.Lock()

        # Concurrency semaphores
        self.sem_asr = asyncio.Semaphore(settings.semaphore_asr)
        self.sem_translate = asyncio.Semaphore(settings.semaphore_translate)
        self.sem_tts = asyncio.Semaphore(settings.semaphore_tts)

        # Circuit breaker registry isolating per (service, language)
        self.cb_registry = CircuitBreakerRegistry(
            failure_threshold=settings.circuit_breaker_threshold,
            recovery_timeout=60.0,
        )

        # Policy guard for allowed hosts (E3: strictly validates Bodhan host)
        validate_bodhan_host(settings.bodhan_base_url)
        self.policy_guard = HostAllowlistGuard(
            [
                httpx.URL(settings.bodhan_base_url).host,
                httpx.URL(settings.llm_base_url).host,
            ]
        )

        # Underlying HTTP client with policy hook
        self._http = httpx.AsyncClient(
            base_url=settings.bodhan_base_url,
            timeout=settings.timeout_asr,
            event_hooks={"request": [self.policy_guard.async_request_hook]},
            transport=transport,
        )

    async def close(self) -> None:
        await self._http.aclose()

    @property
    def cb_asr(self) -> CircuitBreaker:
        return self.cb_registry.get_breaker("transcribe", "default")

    @property
    def cb_translate(self) -> CircuitBreaker:
        return self.cb_registry.get_breaker("translate", "default")

    @property
    def cb_tts(self) -> CircuitBreaker:
        return self.cb_registry.get_breaker("speak", "default")

    # -----------------------------------------------------------------------
    # 1. Indic-Transcribe
    # -----------------------------------------------------------------------
    async def _do_transcribe(
        self,
        audio_bytes: bytes,
        language: Optional[str],
        response_format: str,
        duration_seconds: float,
        stage: str,
        lang: Optional[str],
        segment_id: Optional[str],
        cache_key: str,
    ) -> Dict[str, Any]:
        est_cost = self.ledger.calculate_transcribe_cost(duration_seconds)
        async with self.budget_guard.reserve(est_cost):
            headers = {"Authorization": f"Bearer {self.settings.bodhan_transcribe_key}"}
            form_data = {"model": "indic-transcribe", "response_format": response_format}
            if language:
                form_data["language"] = language
            files = {"file": ("chunk.wav", audio_bytes, "audio/wav")}

            async def _call():
                return await self._http.post(
                    "/audio/transcriptions",
                    headers=headers,
                    data=form_data,
                    files=files,
                    timeout=self.settings.timeout_asr,
                )

            cb = self.cb_registry.get_breaker("transcribe", language or lang or "en")
            resp = await execute_with_resilience(
                _call,
                semaphore=self.sem_asr,
                circuit_breaker=cb,
            )
            resp.raise_for_status()
            data = resp.json()

            actual_cost = await self.ledger.record_transcribe(duration_seconds, from_cache=False)
            self.cache.put(cache_key, data, actual_cost, extra_meta={"duration": duration_seconds})
            return data

    async def transcribe(
        self,
        audio_bytes: bytes,
        language: Optional[str] = "en",
        response_format: str = "verbose_json",
        duration_seconds: float = 10.0,
        stage: str = "asr",
        lang: Optional[str] = None,
        segment_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Transcribes audio using Bodhan Indic-Transcribe.
        Returns parsed response dict containing text and (if verbose_json) segments/words.
        """
        service = "indic-transcribe"
        model = "indic-transcribe"
        params = {"language": language or "auto", "response_format": response_format}

        # Check Cache
        cache_key = self.cache.compute_key(service, model, params, audio_bytes)
        cached = self.cache.get(cache_key)
        if cached is not None:
            data, _ = cached
            await self.ledger.record_transcribe(duration_seconds, from_cache=True)
            self.logger.debug(
                f"Cache hit for transcribe [{cache_key[:8]}]",
                stage=stage,
                lang=lang,
                segment_id=segment_id,
            )
            return data

        # C3: Single-flight in-flight deduplication
        flight_key = f"transcribe:{cache_key}"
        async with self._in_flight_lock:
            if flight_key in self._in_flight:
                task = self._in_flight[flight_key]
                is_leader = False
            else:
                task = asyncio.create_task(
                    self._do_transcribe(
                        audio_bytes, language, response_format, duration_seconds, stage, lang, segment_id, cache_key
                    )
                )
                self._in_flight[flight_key] = task
                is_leader = True

        try:
            return await task
        finally:
            if is_leader:
                async with self._in_flight_lock:
                    self._in_flight.pop(flight_key, None)

    # -----------------------------------------------------------------------
    # 2. Indic-Translate
    # -----------------------------------------------------------------------
    async def _do_translate(
        self,
        text: str,
        target_language_code: str,
        source_language_code: Optional[str],
        stage: str,
        lang: Optional[str],
        segment_id: Optional[str],
        cache_key: str,
    ) -> Tuple[str, Dict[str, Any]]:
        est_tokens = max(10, len(text.split()) * 3)
        est_cost = self.ledger.calculate_translate_cost(est_tokens)
        async with self.budget_guard.reserve(est_cost):
            headers = {
                "Authorization": f"Bearer {self.settings.bodhan_translate_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": "indic-translate",
                "messages": [{"role": "user", "content": text}],
                "target_language_code": target_language_code,
            }
            if source_language_code:
                payload["source_language_code"] = source_language_code

            async def _call():
                return await self._http.post(
                    "/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=self.settings.timeout_translate,
                )

            cb = self.cb_registry.get_breaker("translate", target_language_code or "default")
            try:
                resp = await execute_with_resilience(
                    _call,
                    semaphore=self.sem_translate,
                    circuit_breaker=cb,
                )
                if resp.status_code in (400, 404, 422):
                    resp_text = resp.text.lower()
                    if any(k in resp_text for k in ["voice", "language", "not supported", "unknown", "invalid"]):
                        cb.trip()
                        raise UnknownVoiceOrLanguageError(
                            f"Bodhan Indic-Translate rejected language '{target_language_code}': {resp.text}",
                            lang=target_language_code,
                            service="translate",
                        )
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (400, 404, 422):
                    resp_text = e.response.text.lower()
                    if any(k in resp_text for k in ["voice", "language", "not supported", "unknown", "invalid"]):
                        cb.trip()
                        raise UnknownVoiceOrLanguageError(
                            f"Bodhan Indic-Translate rejected language '{target_language_code}': {e.response.text}",
                            lang=target_language_code,
                            service="translate",
                        ) from e
                raise

            choices = data.get("choices", [])
            if not choices or not choices[0].get("message", {}).get("content"):
                raise ValueError(f"Indic-Translate returned invalid/empty response structure: {data}")

            translated_content = str(choices[0]["message"]["content"]).strip()
            if not translated_content:
                raise ValueError("Indic-Translate returned empty translation content")

            usage = data.get("usage", {})
            completion_tokens = usage.get("completion_tokens", est_tokens)
            actual_cost = await self.ledger.record_translate(completion_tokens, from_cache=False)

            self.cache.put(cache_key, data, actual_cost)
            return translated_content, usage

    async def translate(
        self,
        text: str,
        target_language_code: str,
        source_language_code: Optional[str] = "en",
        stage: str = "translate",
        lang: Optional[str] = None,
        segment_id: Optional[str] = None,
        nonce: Optional[int] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Translates text using Bodhan Indic-Translate.
        Returns (translated_text, usage_dict).
        """
        service = "indic-translate"
        model = "indic-translate"
        params: Dict[str, Any] = {
            "source": source_language_code or "auto",
            "target": target_language_code,
        }
        if nonce is not None:
            params["nonce"] = nonce

        # Check Cache
        cache_key = self.cache.compute_key(service, model, params, text)
        cached = self.cache.get(cache_key)
        if cached is not None:
            data, _ = cached
            tokens = data.get("usage", {}).get("completion_tokens", 0)
            await self.ledger.record_translate(tokens, from_cache=True)
            self.logger.debug(
                f"Cache hit for translate [{cache_key[:8]}]",
                stage=stage,
                lang=lang,
                segment_id=segment_id,
            )
            return data["choices"][0]["message"]["content"], data.get("usage", {})

        # C3: Single-flight in-flight deduplication
        flight_key = f"translate:{cache_key}"
        async with self._in_flight_lock:
            if flight_key in self._in_flight:
                task = self._in_flight[flight_key]
                is_leader = False
            else:
                task = asyncio.create_task(
                    self._do_translate(
                        text, target_language_code, source_language_code, stage, lang, segment_id, cache_key
                    )
                )
                self._in_flight[flight_key] = task
                is_leader = True

        try:
            return await task
        finally:
            if is_leader:
                async with self._in_flight_lock:
                    self._in_flight.pop(flight_key, None)

    # -----------------------------------------------------------------------
    # 3. Indic-Speak
    # -----------------------------------------------------------------------
    async def _do_speak(
        self,
        text: str,
        voice: str,
        language_code: str,
        instructions_str: str,
        stage: str,
        lang: Optional[str],
        segment_id: Optional[str],
        cache_key: str,
    ) -> bytes:
        est_cost = self.ledger.calculate_speak_cost(len(text))
        async with self.budget_guard.reserve(est_cost):
            headers = {
                "Authorization": f"Bearer {self.settings.bodhan_speak_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": "indic-speak",
                "input": text,
                "voice": voice,
                "instructions": instructions_str,
            }

            async def _call():
                return await self._http.post(
                    "/audio/speech",
                    headers=headers,
                    json=payload,
                    timeout=self.settings.timeout_tts,
                )

            cb = self.cb_registry.get_breaker("speak", language_code or lang or "default")
            try:
                resp = await execute_with_resilience(
                    _call,
                    semaphore=self.sem_tts,
                    circuit_breaker=cb,
                )
                if resp.status_code in (400, 404, 422):
                    resp_text = resp.text.lower()
                    if any(k in resp_text for k in ["voice", "language", "not supported", "unknown", "invalid"]):
                        cb.trip()
                        raise UnknownVoiceOrLanguageError(
                            f"Bodhan Indic-Speak rejected voice '{voice}' or language '{language_code}': {resp.text}",
                            lang=language_code or lang,
                            service="speak",
                        )
                resp.raise_for_status()
                audio_bytes = resp.content
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (400, 404, 422):
                    resp_text = e.response.text.lower()
                    if any(k in resp_text for k in ["voice", "language", "not supported", "unknown", "invalid"]):
                        cb.trip()
                        raise UnknownVoiceOrLanguageError(
                            f"Bodhan Indic-Speak rejected voice '{voice}' or language '{language_code}': {e.response.text}",
                            lang=language_code or lang,
                            service="speak",
                        ) from e
                raise

            # A5: Validate response BEFORE caching it:
            if not audio_bytes.startswith(b"RIFF") or b"WAVE" not in audio_bytes[:16]:
                raise ValueError("Indic-Speak returned invalid audio: missing RIFF/WAV header")

            try:
                with sf.SoundFile(io.BytesIO(audio_bytes)) as sfile:
                    frames = len(sfile)
                    sr = sfile.samplerate
                    if frames <= 0 or sr <= 0:
                        raise ValueError(f"Invalid WAV frames ({frames}) or sample rate ({sr})")
            except Exception as e:
                raise ValueError(f"Indic-Speak returned corrupt or unreadable audio: {e}") from e

            actual_cost = await self.ledger.record_speak(len(text), from_cache=False)
            self.cache.put(cache_key, audio_bytes, actual_cost, extra_meta={"chars": len(text)})
            return audio_bytes

    async def speak(
        self,
        text: str,
        voice: str,
        language_code: str,
        style: Optional[str] = None,
        stage: str = "tts",
        lang: Optional[str] = None,
        segment_id: Optional[str] = None,
        nonce: Optional[int] = None,
    ) -> bytes:
        """
        Synthesizes text into audio using Bodhan Indic-Speak.
        Returns raw audio bytes (24 kHz mono).
        """
        service = "indic-speak"
        model = "indic-speak"
        inst_dict = {"lang": language_code}
        if style:
            inst_dict["style"] = style
        instructions_str = json.dumps(inst_dict)

        params: Dict[str, Any] = {"voice": voice, "instructions": instructions_str}
        if nonce is not None:
            params["nonce"] = nonce
            self.logger.info(
                f"Deliberate retry_same requested with nonce={nonce} to bypass identical synthesis",
                stage=stage,
                lang=lang,
                segment_id=segment_id,
            )

        # Check Cache
        cache_key = self.cache.compute_key(service, model, params, text)
        cached = self.cache.get(cache_key)
        if cached is not None:
            audio_bytes, _ = cached
            await self.ledger.record_speak(len(text), from_cache=True)
            self.logger.debug(
                f"Cache hit for speak [{cache_key[:8]}]",
                stage=stage,
                lang=lang,
                segment_id=segment_id,
            )
            return audio_bytes

        # C3: Single-flight in-flight deduplication
        flight_key = f"speak:{cache_key}"
        async with self._in_flight_lock:
            if flight_key in self._in_flight:
                task = self._in_flight[flight_key]
                is_leader = False
            else:
                task = asyncio.create_task(
                    self._do_speak(
                        text, voice, language_code, instructions_str, stage, lang, segment_id, cache_key
                    )
                )
                self._in_flight[flight_key] = task
                is_leader = True

        try:
            return await task
        finally:
            if is_leader:
                async with self._in_flight_lock:
                    self._in_flight.pop(flight_key, None)
