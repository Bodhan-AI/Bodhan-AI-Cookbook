"""Bodhan clients: indic-translate (POST /translate), indic-speak and indic-transcribe (OpenAI-shaped /v1 routes).

Every call goes cache -> budget reservation -> HTTP -> settle -> cache, under a per-model
concurrency cap. These are the only translation, TTS and ASR backends in the harness.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import httpx
import numpy as np
import regex

from .. import log
from ..audio.io import Audio, decode, encode_wav, resample
from ..budget import CostTracker, estimate_translate_tokens, speak_cost, transcribe_cost, translate_cost
from ..cache import Cache, make_key, sha256_bytes
from ..config import Settings
from ..languages import get_language
from .http import ServiceError, ServiceUnavailable, request

TRANSLATE = "indic-translate"
SPEAK = "indic-speak"
TRANSCRIBE = "indic-transcribe"

TRANSCRIBE_LANGS = {
    "as",
    "bhb",
    "bho",
    "bn",
    "brx",
    "doi",
    "en",
    "gu",
    "hi",
    "kn",
    "kok",
    "ks",
    "mai",
    "ml",
    "mni",
    "mr",
    "ne",
    "or",
    "pa",
    "sa",
    "sat",
    "sd",
    "ta",
    "te",
    "ur",
}
MAX_TRANSLATE_CHARS = 3000
ASR_WINDOW_S = 25.0
ASR_RATE = 16000


@dataclass
class CallResult:
    value: object
    cached: bool
    cost_inr: float


class BodhanClient:
    def __init__(self, settings: Settings, cache: Cache, budget: CostTracker, http: httpx.AsyncClient):
        self.settings = settings
        self.cache = cache
        self.budget = budget
        self.http = http
        self.base = settings.base_url.removesuffix("/v1")
        self._sem = {m: asyncio.Semaphore(settings.concurrency.get(k, 4)) for m, k in ((TRANSLATE, "translate"), (SPEAK, "speak"), (TRANSCRIBE, "transcribe"))}
        self._keys = {TRANSLATE: settings.translate_key, SPEAK: settings.speak_key, TRANSCRIBE: settings.transcribe_key}
        self.disabled: dict[str, str] = {} if settings.offline else {m: "no API key configured" for m, k in self._keys.items() if not k}
        self._translate_route = "plain"

    def available(self, model: str) -> bool:
        return model not in self.disabled

    def _headers(self, model: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._keys[model]}"}

    async def _paid_call(self, model: str, key: str, estimate: float, call, cost_of) -> CallResult:
        hit = self.cache.get(key)
        if hit is not None:
            value, cost = hit
            self.budget.attribute(model, cost)
            log.event("cache_hit", model=model, key=key[:12])
            return CallResult(value, True, cost)
        if self.settings.offline:
            raise ServiceUnavailable(f"{model}: offline cache miss; no API request was made")
        async with self._sem[model]:
            # Queued calls recheck the circuit breaker and reserve only when admitted.
            if not self.available(model):
                raise ServiceUnavailable(f"{model} unavailable: {self.disabled[model]}")
            await self.budget.reserve(model, estimate)
            try:
                value = await call()
                actual = cost_of(value)
            except ServiceUnavailable as exc:
                self.disabled[model] = str(exc)
                await self.budget.release(estimate)
                log.warn("service_disabled", model=model, error=str(exc))
                raise
            except BaseException:
                await self.budget.release(estimate)
                raise
            await self.budget.settle(model, estimate, actual)
        self.budget.attribute(model, actual)
        self.cache.put(key, model, value, actual)
        log.event("cache_miss", model=model, key=key[:12], cost_inr=round(actual, 5))
        return CallResult(value, False, actual)

    async def translate(self, text: str, target: str, *, script: str | None = None) -> CallResult:
        if len(text) > MAX_TRANSLATE_CHARS and "\n\n" in text:
            parts = [p for p in text.split("\n\n")]
            results = await asyncio.gather(*(self.translate(p, target, script=script) if p.strip() else _empty() for p in parts))
            joined = "\n\n".join(r.value["translation"] for r in results)
            return CallResult(
                {"translation": joined, "truncated": any(r.value.get("truncated") for r in results)},
                all(r.cached for r in results),
                sum(r.cost_inr for r in results),
            )
        payload: dict = {"text": text, "target_language": target}
        if script:
            payload["script"] = script
        payload["max_output_tokens"] = min(8192, estimate_translate_tokens(text) * 2 + 64)
        key = make_key("translate", TRANSLATE, payload)

        async def call():
            if self._translate_route == "plain":
                try:
                    return await self._translate_plain(payload, text)
                except ServiceError as exc:
                    if exc.status != 404:
                        raise
                    # Some deployments only serve indic-translate through the OpenAI chat transport.
                    self._translate_route = "chat"
                    log.warn("translate_route_fallback", model=TRANSLATE, error="POST /translate returned 404; using /v1/chat/completions")
            return await self._translate_chat(payload, text)

        return await self._paid_call(TRANSLATE, key, translate_cost(payload["max_output_tokens"]), call, lambda v: translate_cost(v["completion_tokens"]))

    async def _translate_plain(self, payload: dict, text: str) -> dict:
        resp = await request(
            self.http,
            "POST",
            f"{self.base}/translate",
            service=TRANSLATE,
            headers=self._headers(TRANSLATE),
            json=payload,
            attempts=self.settings.max_attempts,
            timeout=self.settings.timeout_s,
        )
        body = resp.json()
        if "translation" not in body:
            raise ServiceError(f"{TRANSLATE}: response has no 'translation' field")
        usage = body.get("usage") or {}
        return {
            "translation": body["translation"],
            "truncated": bool(body.get("truncated")),
            "completion_tokens": int(usage.get("completion_tokens") or estimate_translate_tokens(text)),
        }

    async def _translate_chat(self, payload: dict, text: str) -> dict:
        """The model card's format: one user message naming only the target language, greedy decoding."""
        prompt = f"Translate the following text into {target_name(payload['target_language'], payload.get('script'))}:\n\n{text}"
        body = {"model": TRANSLATE, "messages": [{"role": "user", "content": prompt}], "temperature": 0, "max_tokens": payload["max_output_tokens"]}
        resp = await request(
            self.http,
            "POST",
            f"{self.base}/v1/chat/completions",
            service=TRANSLATE,
            headers=self._headers(TRANSLATE),
            json=body,
            attempts=self.settings.max_attempts,
            timeout=self.settings.timeout_s,
        )
        data = resp.json()
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ServiceError(f"{TRANSLATE}: malformed chat response: {str(data)[:200]}") from exc
        usage = data.get("usage") or {}
        return {
            "translation": content.strip(),
            "truncated": choice.get("finish_reason") == "length",
            "completion_tokens": int(usage.get("completion_tokens") or estimate_translate_tokens(text)),
        }

    async def speak(self, text: str, lang: str, voice: str, style: str | None = None) -> CallResult:
        instructions = {"lang": lang}
        if style:
            instructions["style"] = style
        payload = {"model": SPEAK, "input": text, "voice": voice, "instructions": json.dumps(instructions, ensure_ascii=False), "response_format": "wav"}
        key = make_key("speak", SPEAK, payload)

        async def call():
            resp = await request(
                self.http,
                "POST",
                f"{self.base}/v1/audio/speech",
                service=SPEAK,
                headers={**self._headers(SPEAK), "Content-Type": "application/json"},
                json=payload,
                attempts=self.settings.max_attempts,
                timeout=max(self.settings.timeout_s, 120.0),
            )
            data = resp.content
            if not data or resp.headers.get("content-type", "").startswith("application/json"):
                raise ServiceError(f"{SPEAK}: expected audio, got {resp.headers.get('content-type')}")
            path = self.cache.put_blob(data, ".wav")
            return {"blob": path.stem, "bytes": len(data)}

        result = await self._paid_call(SPEAK, key, speak_cost(len(text)), call, lambda v: speak_cost(len(text)))
        data = self.cache.get_blob(result.value["blob"], ".wav")
        if data is None:
            raise ServiceError(f"{SPEAK}: cached audio blob {result.value['blob'][:12]} is missing; clear the cache entry")
        return CallResult(data, result.cached, result.cost_inr)

    async def transcribe(self, audio: Audio, lang: str) -> CallResult:
        """Transcribe in <= 25 s windows cut at low-energy points; returns {"text", "chunks"}."""
        clip = resample(audio, ASR_RATE)
        windows = split_for_asr(clip)
        language = lang if lang in TRANSCRIBE_LANGS else lang.split("-")[0]
        language = language if language in TRANSCRIBE_LANGS else None
        results = await asyncio.gather(*(self._transcribe_chunk(Audio(clip.samples[a:b], clip.rate), language) for a, b in windows))
        texts = [r.value["text"].strip() for r in results]
        chunks = [{"start_s": round(a / clip.rate, 3), "end_s": round(b / clip.rate, 3), "text": t} for (a, b), t in zip(windows, texts, strict=True)]
        return CallResult({"text": " ".join(t for t in texts if t), "chunks": chunks}, all(r.cached for r in results), sum(r.cost_inr for r in results))

    async def _transcribe_chunk(self, clip: Audio, language: str | None) -> CallResult:
        wav = encode_wav(clip)
        data = {"model": TRANSCRIBE}
        if language:
            data["language"] = language
        key = make_key("transcribe", TRANSCRIBE, {"audio_sha256": sha256_bytes(wav), "language": language})
        seconds = clip.duration

        async def call():
            resp = await request(
                self.http,
                "POST",
                f"{self.base}/v1/audio/transcriptions",
                service=TRANSCRIBE,
                headers=self._headers(TRANSCRIBE),
                files={"file": ("audio.wav", wav, "audio/wav")},
                data=data,
                attempts=self.settings.max_attempts,
                timeout=self.settings.timeout_s,
            )
            body = resp.json()
            return {"text": str(body.get("text", ""))}

        return await self._paid_call(TRANSCRIBE, key, transcribe_cost(seconds), call, lambda v: transcribe_cost(seconds))


async def _empty() -> CallResult:
    return CallResult({"translation": "", "truncated": False}, True, 0.0)


def target_name(code: str, script: str | None = None) -> str:
    """Language name as the indic-translate prompt expects it, e.g. 'Kashmiri (Perso-Arabic script)', 'Hindi (Roman)'."""
    name = get_language(code).name if get_language(code).known else code
    name = regex.sub(r"\(([^)]+)\)", lambda m: f"({m.group(1)} script)", name)
    if script == "roman":
        name += " (Roman)"
    elif script == "codemix":
        name += " (code-mixed)"
    return name


def split_for_asr(clip: Audio, window_s: float = ASR_WINDOW_S) -> list[tuple[int, int]]:
    """Sample ranges of at most window_s seconds, each cut at the quietest 50 ms near the boundary."""
    n, rate = len(clip.samples), clip.rate
    limit = int(window_s * rate)
    if n <= int(28.0 * rate):
        return [(0, n)]
    bounds, start = [], 0
    hop = int(0.05 * rate)
    while n - start > limit:
        lo, hi = start + int(limit * 0.6), start + limit
        segment = clip.samples[lo:hi]
        frames = len(segment) // hop
        if frames > 0:
            energy = (segment[: frames * hop].reshape(frames, hop) ** 2).mean(axis=1)
            cut = lo + int(np.argmin(energy)) * hop + hop // 2
        else:
            cut = hi
        bounds.append((start, cut))
        start = cut
    bounds.append((start, n))
    return bounds


def decode_audio(data: bytes) -> Audio:
    return decode(data)
