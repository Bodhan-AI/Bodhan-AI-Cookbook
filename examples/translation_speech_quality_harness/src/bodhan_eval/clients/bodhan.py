"""Client for Bodhan Indic-Translate, Indic-Speak and Indic-Transcribe.

Every call goes through the cache first. Paid calls reserve budget, run under a
per-service concurrency limit, retry with backoff, then record actual cost.

API facts (console.bodhan.ai/api-docs, checked against the live server 2026-11-20):
  POST /v1/chat/completions       {model: indic-translate, messages: [one user turn = the text],
                                   target_language, script} -> choices[0].message.content,
                                   finish_reason ("length" = truncated), usage.completion_tokens
    (The docs also list POST /translate, but the live server returns 404 for it. The gateway
    writes the prompt itself: no system turn, exactly one user turn.)
  POST /v1/audio/speech           {model, input, voice, instructions(JSON str)} -> audio/wav
  POST /v1/audio/transcriptions   multipart {file, model, language} -> {text}; max 30 s audio
"""

from __future__ import annotations

import asyncio
import io
import json
import os
from dataclasses import dataclass

import httpx
import soundfile as sf

from bodhan_eval import cost as costmod
from bodhan_eval.cache import Cache, sha256_bytes
from bodhan_eval.clients.http import APIError, CacheMiss, RateGate, request_with_retry
from bodhan_eval.cost import CostMeter

DEFAULT_BASE_URL = "https://api.bodhan.ai"
TRANSLATE_MODEL = "indic-translate"
SPEAK_MODEL = "indic-speak"
TRANSCRIBE_MODEL = "indic-transcribe"


@dataclass(frozen=True)
class TranslateResult:
    text: str
    truncated: bool
    cached: bool


@dataclass(frozen=True)
class SpeakResult:
    wav: bytes
    cached: bool


@dataclass(frozen=True)
class TranscribeResult:
    text: str
    cached: bool


def _key(service: str) -> str | None:
    return os.environ.get(f"BODHAN_{service.upper()}_KEY") or os.environ.get("BODHAN_API_KEY")


def wav_duration(wav: bytes) -> float:
    info = sf.info(io.BytesIO(wav))
    return info.frames / info.samplerate if info.samplerate else 0.0


class BodhanClient:
    def __init__(
        self,
        cache: Cache,
        cost: CostMeter,
        *,
        base_url: str | None = None,
        concurrency: int = 8,
        timeout: float = 120.0,
        offline: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.cache = cache
        self.cost = cost
        self.offline = offline
        self._http = httpx.AsyncClient(
            base_url=base_url or os.environ.get("BODHAN_BASE_URL", DEFAULT_BASE_URL),
            timeout=timeout,
            transport=transport,
        )
        self._gate = {
            svc: RateGate(float(os.environ.get(f"BODHAN_{svc.upper()}_RPM") or 0) or None)
            for svc in ("translate", "speak", "transcribe")
        }
        self._sem = {
            "translate": asyncio.Semaphore(concurrency),
            "speak": asyncio.Semaphore(max(1, concurrency // 2)),
            "transcribe": asyncio.Semaphore(concurrency),
        }

    async def aclose(self) -> None:
        await self._http.aclose()

    def _headers(self, service: str) -> dict[str, str]:
        key = _key(service)
        if not key:
            raise APIError(
                service,
                None,
                "missing_key",
                f"set BODHAN_{service.upper()}_KEY (or BODHAN_API_KEY)",
            )
        return {"Authorization": f"Bearer {key}"}

    def _check_offline(self, service: str) -> None:
        if self.offline:
            raise CacheMiss(f"{service}: not in cache and running offline")

    # -- translate -----------------------------------------------------------

    def cached_translate(
        self, text: str, target_language: str, script: str = "native"
    ) -> TranslateResult | None:
        """A translation already in the cache, or None. Never calls the API or costs money."""
        payload = {
            "model": TRANSLATE_MODEL,
            "text": text,
            "target_language": target_language,
            "script": script,
        }
        hit = self.cache.get_json("translate", payload)
        if not hit:
            return None
        self.cost.record_cached("translate")
        return TranslateResult(hit[0]["translation"], hit[0]["truncated"], True)

    async def translate(
        self, text: str, target_language: str, script: str = "native"
    ) -> TranslateResult:
        payload = {
            "model": TRANSLATE_MODEL,
            "text": text,
            "target_language": target_language,
            "script": script,
        }
        hit = self.cache.get_json("translate", payload)
        if hit:
            self.cost.record_cached("translate")
            v = hit[0]
            return TranslateResult(v["translation"], v["truncated"], True)
        self._check_offline("translate")

        reservation = self.cost.reserve("translate", costmod.estimate_translate_inr(text))
        try:
            async with self._sem["translate"]:
                resp = await request_with_retry(
                    self._http,
                    "translate",
                    "POST",
                    "/v1/chat/completions",
                    gate=self._gate["translate"],
                    headers=self._headers("translate"),
                    json={
                        "model": TRANSLATE_MODEL,
                        "messages": [{"role": "user", "content": text}],
                        "target_language": target_language,
                        "script": script,
                    },
                )
        except BaseException:
            self.cost.release(reservation)
            raise
        body = resp.json()
        choice = (body.get("choices") or [{}])[0]
        translation = (choice.get("message") or {}).get("content") or ""
        usage = body.get("usage") or {}
        tokens = usage.get("completion_tokens") or max(1, len(translation) // 2)
        self.cost.settle(
            "translate", reservation, tokens, tokens * costmod.PRICE_TRANSLATE_PER_TOKEN
        )
        value = {
            "translation": translation,
            "truncated": choice.get("finish_reason") == "length",
        }
        self.cache.put_json(
            "translate",
            payload,
            value,
            {"request_id": body.get("id"), "usage": usage},
        )
        return TranslateResult(value["translation"], value["truncated"], False)

    # -- speak ---------------------------------------------------------------

    async def speak(
        self, text: str, lang: str, voice: str, style: str | None = None
    ) -> SpeakResult:
        instructions = {"lang": lang}
        if style:
            instructions["style"] = style
        instr = json.dumps(instructions, sort_keys=True)
        payload = {"model": SPEAK_MODEL, "input": text, "voice": voice, "instructions": instr}
        hit = self.cache.get("speak", payload)
        if hit:
            self.cost.record_cached("speak")
            return SpeakResult(hit[0], True)
        self._check_offline("speak")

        inr = costmod.speak_inr(text)
        reservation = self.cost.reserve("speak", inr)
        try:
            async with self._sem["speak"]:
                resp = await request_with_retry(
                    self._http,
                    "speak",
                    "POST",
                    "/v1/audio/speech",
                    gate=self._gate["speak"],
                    headers=self._headers("speak"),
                    json=payload,
                )
        except BaseException:
            self.cost.release(reservation)
            raise
        self.cost.settle("speak", reservation, len(text), inr)
        self.cache.put(
            "speak", payload, resp.content, {"request_id": resp.headers.get("x-request-id")}
        )
        return SpeakResult(resp.content, False)

    # -- transcribe ------------------------------------------------------------

    async def transcribe(self, wav: bytes, language: str) -> TranscribeResult:
        payload = {
            "model": TRANSCRIBE_MODEL,
            "audio_sha256": sha256_bytes(wav),
            "language": language,
        }
        hit = self.cache.get_json("transcribe", payload)
        if hit:
            self.cost.record_cached("transcribe")
            return TranscribeResult(hit[0]["text"], True)
        self._check_offline("transcribe")

        seconds = wav_duration(wav)
        inr = costmod.transcribe_inr(seconds)
        reservation = self.cost.reserve("transcribe", inr)
        try:
            async with self._sem["transcribe"]:
                resp = await request_with_retry(
                    self._http,
                    "transcribe",
                    "POST",
                    "/v1/audio/transcriptions",
                    gate=self._gate["transcribe"],
                    headers=self._headers("transcribe"),
                    data={"model": TRANSCRIBE_MODEL, "language": language},
                    files={"file": ("audio.wav", wav, "audio/wav")},
                )
        except BaseException:
            self.cost.release(reservation)
            raise
        text = resp.json().get("text", "")
        self.cost.settle("transcribe", reservation, seconds, inr)
        self.cache.put_json(
            "transcribe",
            payload,
            {"text": text},
            {"request_id": resp.headers.get("x-request-id"), "seconds": seconds},
        )
        return TranscribeResult(text, False)
