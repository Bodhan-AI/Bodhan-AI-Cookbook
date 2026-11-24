"""clients.bodhan: Async clients for the three Bodhan hosted models.
clients.llm: OpenAI-compatible LLM client (LLM_BASE_URL / LLM_MODEL / LLM_API_KEY).
generate: ``--generate``: create missing translations and audio with Bodhan models.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import openai

from .audio import audio_duration
from .config import MODEL_SPEAK, MODEL_TRANSCRIBE, MODEL_TRANSLATE, SPEECH_PATH, TRANSCRIBE_PATH, TRANSLATE_PATH, Settings, default_voice
from .dataset import Item
from .infra import RETRYABLE_STATUS, BudgetExceeded, Cache, CostLedger, RetryableError, make_key, parse_retry_after, with_retries

# -------------------- bodhan
log = logging.getLogger("bodhan_eval.bodhan")


MAX_TRANSCRIBE_SECONDS = 30.0  # hosted Indic-Transcribe limit per request


class BodhanError(Exception):
    """Non-retryable error from a Bodhan endpoint (bad request, auth, unsupported language...)."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


class MissingKeyError(BodhanError):
    pass


class OfflineCacheMiss(BodhanError):
    """--offline was set and the call is not in the cache."""


@dataclass
class TranslateResult:
    text: str
    truncated: bool
    output_tokens: int
    cached: bool
    cache_key: str


@dataclass
class SpeakResult:
    wav: bytes
    duration_s: float
    chars: int
    voice: str
    cached: bool
    cache_key: str


@dataclass
class TranscribeResult:
    text: str
    duration_s: float
    cached: bool
    cache_key: str


DEFAULT_CONCURRENCY = {"translate": 8, "speak": 2, "transcribe": 4}


RATE_LIMIT_PAUSE_S = 20.0  # pause after a 429 that carries no Retry-After header


RETRY_ATTEMPTS = 8  # enough to outlast a per-minute rate-limit window


class BodhanClient:
    def __init__(
        self,
        settings: Settings,
        cache: Cache,
        ledger: CostLedger,
        *,
        concurrency: dict[str, int] | None = None,
        timeout_s: float = 90.0,
        offline: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.settings = settings
        self.cache = cache
        self.ledger = ledger
        self.offline = offline
        conc = {**DEFAULT_CONCURRENCY, **(concurrency or {})}
        self._sem = {k: asyncio.Semaphore(v) for k, v in conc.items()}
        self._http = httpx.AsyncClient(base_url=settings.bodhan_base_url, timeout=timeout_s, transport=transport)
        # Last seen rate-limit headers per service (logged; used to size concurrency later)
        self.observed_limits: dict[str, dict[str, str]] = {}
        # Shared per-service pause after a 429: every caller waits it out, so concurrent
        # tasks do not keep hitting the limit (retries count against it).
        self._cooldown_until: dict[str, float] = {k: 0.0 for k in conc}

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()

    # ---- helpers ---------------------------------------------------------------
    def _key_for(self, service: str) -> str:
        key = {"translate": self.settings.translate_key, "speak": self.settings.speak_key, "transcribe": self.settings.transcribe_key}[service]
        if not key:
            env = {"translate": "BODHAN_TRANSLATE_KEY", "speak": "BODHAN_SPEAK_KEY", "transcribe": "BODHAN_TRANSCRIBE_KEY"}[service]
            raise MissingKeyError(f"{env} is not set", code="missing_key")
        return key

    def _observe(self, service: str, resp: httpx.Response) -> str | None:
        limits = {k: v for k, v in resp.headers.items() if k.lower().startswith("x-ratelimit")}
        if limits:
            self.observed_limits[service] = limits
        return resp.headers.get("x-request-id")

    @staticmethod
    def _raise_for_status(resp: httpx.Response, what: str) -> None:
        if resp.status_code < 400:
            return
        code, message = None, resp.text[:300]
        try:
            err = resp.json().get("error", {})
            if isinstance(err, dict):
                code, message = err.get("code"), err.get("message", message)
            elif "detail" in resp.json():
                message = str(resp.json()["detail"])[:300]
        except (ValueError, AttributeError):
            pass
        if resp.status_code in RETRYABLE_STATUS:
            raise RetryableError(
                f"{what}: HTTP {resp.status_code} {message}", status=resp.status_code, retry_after=parse_retry_after(resp.headers.get("retry-after"))
            )
        raise BodhanError(f"{what}: HTTP {resp.status_code} {code or ''} {message}".strip(), status=resp.status_code, code=code)

    async def _post(self, service: str, path: str, **kw) -> httpx.Response:
        """POST under the service's concurrency limit, honouring a shared 429 cooldown."""
        async with self._sem[service]:
            while (wait := self._cooldown_until[service] - time.monotonic()) > 0:
                await asyncio.sleep(wait)
            resp = await self._http.post(path, **kw)
            if resp.status_code == 429:
                ra = parse_retry_after(resp.headers.get("retry-after"))
                pause = ra if ra is not None else RATE_LIMIT_PAUSE_S
                self._cooldown_until[service] = max(self._cooldown_until[service], time.monotonic() + pause)
                log.info("rate_limited", extra={"fields": {"service": service, "pause_s": pause}})
            return resp

    def _log(self, event: str, **fields) -> None:
        log.info(event, extra={"fields": fields})

    # ---- Indic-Translate -------------------------------------------------------
    async def translate(self, text: str, target_language: str, *, script: str = "native") -> TranslateResult:
        params = {"target_language": target_language, "script": script}
        key = make_key(MODEL_TRANSLATE, TRANSLATE_PATH, params, text)
        hit = self.cache.get(key)
        if hit is not None:
            m = hit.meta
            self.ledger.record_cached("translate", m["output_tokens"])
            return TranslateResult(m["translation"], m["truncated"], m["output_tokens"], True, key)
        if self.offline:
            raise OfflineCacheMiss(f"translate→{target_language} not cached", code="offline_miss")
        api_key = self._key_for("translate")
        est_tokens = max(32, int(len(text) * 2))  # generous upper estimate for the budget gate
        reservation = self.ledger.reserve("translate", self.ledger.price("translate", est_tokens))
        # Live API (smoke test 2026-11-23): /translate is 404; the hosted model is served on the
        # OpenAI-compatible chat endpoint and the target is the extra body field `target_language`.
        body = {"model": MODEL_TRANSLATE, "messages": [{"role": "user", "content": text}], "target_language": target_language}
        if script != "native":
            body["script"] = script

        async def call() -> dict:
            resp = await self._post("translate", TRANSLATE_PATH, json=body, headers={"Authorization": f"Bearer {api_key}"})
            rid = self._observe("translate", resp)
            self._raise_for_status(resp, "translate")
            return {"json": resp.json(), "request_id": rid}

        t0 = time.perf_counter()
        try:
            out = await with_retries(call, what=f"translate→{target_language}", attempts=RETRY_ATTEMPTS)
        except BaseException:
            self.ledger.release(reservation)
            raise
        data = out["json"]
        usage = data.get("usage") or {}
        tokens = int(usage.get("completion_tokens") or 0)
        inr = self.ledger.commit(reservation, tokens)
        choice = (data.get("choices") or [{}])[0]
        meta = {
            "translation": (choice.get("message") or {}).get("content") or "",
            # finish_reason "length" = output hit the token budget (the docs' `truncated: true`)
            "truncated": choice.get("finish_reason") == "length",
            "finish_reason": choice.get("finish_reason"),
            "output_tokens": tokens,
            "usage": usage,
            "request_id": out["request_id"],
        }
        self.cache.put(key, "translate", meta)
        self._log(
            "bodhan_call",
            service="translate",
            target=target_language,
            chars=len(text),
            output_tokens=tokens,
            inr=round(inr, 6),
            latency_s=round(time.perf_counter() - t0, 3),
        )
        return TranslateResult(meta["translation"], meta["truncated"], tokens, False, key)

    # ---- Indic-Speak -----------------------------------------------------------
    async def speak(self, text: str, lang: str, *, voice: str | None = None, style: str | None = None) -> SpeakResult:
        voice = voice or default_voice(lang)
        instructions = {"lang": lang}
        if style:
            instructions["style"] = style
        instructions_str = json.dumps(instructions, sort_keys=True)
        params = {"voice": voice, "instructions": instructions_str}
        key = make_key(MODEL_SPEAK, SPEECH_PATH, params, text)
        hit = self.cache.get(key)
        if hit is not None:
            self.ledger.record_cached("speak", len(text))
            return SpeakResult(hit.payload, hit.meta["duration_s"], len(text), voice, True, key)
        if self.offline:
            raise OfflineCacheMiss(f"speak[{lang}] not cached", code="offline_miss")
        api_key = self._key_for("speak")
        reservation = self.ledger.reserve("speak", self.ledger.price("speak", len(text)))
        body = {"model": MODEL_SPEAK, "input": text, "voice": voice, "instructions": instructions_str}

        async def call() -> bytes:
            resp = await self._post("speak", SPEECH_PATH, json=body, headers={"Authorization": f"Bearer {api_key}"})
            rid = self._observe("speak", resp)
            self._raise_for_status(resp, "speak")
            return resp.content, rid

        t0 = time.perf_counter()
        try:
            wav, request_id = await with_retries(call, what=f"speak[{lang}]", attempts=RETRY_ATTEMPTS)
        except BaseException:
            self.ledger.release(reservation)
            raise
        inr = self.ledger.commit(reservation, len(text))  # billed even if the audio is unusable
        try:
            duration = audio_duration(wav)
        except Exception as exc:  # undecodable audio is itself a finding; do not cache it
            raise BodhanError(f"speak[{lang}]: response is not decodable audio ({exc})", code="bad_audio") from exc
        self.cache.put(key, "speak", {"duration_s": duration, "voice": voice, "lang": lang, "chars": len(text), "request_id": request_id}, wav)
        self._log(
            "bodhan_call",
            service="speak",
            lang=lang,
            voice=voice,
            chars=len(text),
            duration_s=round(duration, 3),
            inr=round(inr, 6),
            latency_s=round(time.perf_counter() - t0, 3),
        )
        return SpeakResult(wav, duration, len(text), voice, False, key)

    # ---- Indic-Transcribe ------------------------------------------------------
    async def transcribe(self, wav: bytes, lang: str | None) -> TranscribeResult:
        """Transcribe one clip of at most 30 s (callers chunk longer audio)."""
        duration = audio_duration(wav)
        if duration > MAX_TRANSCRIBE_SECONDS + 0.05:
            raise BodhanError(f"transcribe: clip is {duration:.1f}s, above the {MAX_TRANSCRIBE_SECONDS:.0f}s limit", code="audio_too_long")
        params = {"language": lang or ""}
        key = make_key(MODEL_TRANSCRIBE, TRANSCRIBE_PATH, params, wav)
        hit = self.cache.get(key)
        if hit is not None:
            self.ledger.record_cached("transcribe", duration)
            return TranscribeResult(hit.meta["text"], duration, True, key)
        if self.offline:
            raise OfflineCacheMiss(f"transcribe[{lang}] not cached", code="offline_miss")
        api_key = self._key_for("transcribe")
        reservation = self.ledger.reserve("transcribe", self.ledger.price("transcribe", duration))
        form = {"model": MODEL_TRANSCRIBE}
        if lang:
            form["language"] = lang

        async def call() -> str:
            resp = await self._post(
                "transcribe",
                TRANSCRIBE_PATH,
                data=form,
                files={"file": ("audio.wav", wav, "audio/wav")},
                headers={"Authorization": f"Bearer {api_key}"},
            )
            rid = self._observe("transcribe", resp)
            self._raise_for_status(resp, "transcribe")
            return str(resp.json().get("text", "")), rid

        t0 = time.perf_counter()
        try:
            text, request_id = await with_retries(call, what=f"transcribe[{lang}]", attempts=RETRY_ATTEMPTS)
        except BaseException:
            self.ledger.release(reservation)
            raise
        inr = self.ledger.commit(reservation, duration)
        self.cache.put(key, "transcribe", {"text": text, "duration_s": duration, "lang": lang, "request_id": request_id})
        self._log(
            "bodhan_call", service="transcribe", lang=lang, duration_s=round(duration, 3), inr=round(inr, 6), latency_s=round(time.perf_counter() - t0, 3)
        )
        return TranscribeResult(text, duration, False, key)


# -------------------- llm
log = logging.getLogger("bodhan_eval.llm")


class LLMError(Exception):
    pass


@dataclass
class LLMResult:
    data: dict | None  # parsed JSON object, or None if unparseable
    raw: str
    cached: bool
    prompt_tokens: int
    completion_tokens: int
    cache_key: str


_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def extract_json(text: str) -> dict | None:
    """Return the first JSON object found in ``text``, or None."""
    if not text:
        return None
    stripped = _FENCE.sub("", text.strip())
    try:
        obj = json.loads(stripped)
        return obj if isinstance(obj, dict) else None
    except ValueError:
        pass
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch == "{":
            try:
                obj, _ = decoder.raw_decode(text[i:])
                if isinstance(obj, dict):
                    return obj
            except ValueError:
                continue
    return None


DEFAULT_LLM_CONCURRENCY = 4  # parallel judge calls; use 1 on providers with tight tokens-per-minute limits


def llm_concurrency_from_env(default: int = DEFAULT_LLM_CONCURRENCY) -> int:
    """``LLM_CONCURRENCY`` (not a secret) overrides the default; bad values fall back to it."""
    try:
        value = int(os.environ.get("LLM_CONCURRENCY", "").strip() or default)
    except ValueError:
        return default
    return max(1, value)


class LLMClient:
    def __init__(self, settings: Settings, cache: Cache, ledger: CostLedger, *, concurrency: int = DEFAULT_LLM_CONCURRENCY,
                 offline: bool = False, client=None, timeout_s: float = 120.0):
        if not settings.llm_configured and client is None:
            raise LLMError("LLM_MODEL and LLM_API_KEY must be set to use the judge")
        self.model = settings.llm_model or "test-model"
        self.cache = cache
        self.ledger = ledger
        self.offline = offline
        self._sem = asyncio.Semaphore(concurrency)
        self._json_mode = True
        self._client = client or openai.AsyncOpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key, max_retries=0, timeout=timeout_s)

    async def aclose(self) -> None:
        close = getattr(self._client, "close", None)
        if close is not None:
            res = close()
            if asyncio.iscoroutine(res):
                await res

    async def complete_json(self, system: str, user: str, *, variant: str = "", max_tokens: int = 2000, bypass_cache: bool = False) -> LLMResult:
        """``bypass_cache=True`` (consistency re-judge) neither reads nor writes the cache."""
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        params = {"temperature": 0, "max_tokens": max_tokens, "variant": variant}
        key = make_key(self.model, "chat.completions", params, json.dumps(messages, ensure_ascii=False, sort_keys=True))
        hit = None if bypass_cache else self.cache.get(key)
        if hit is not None:
            m = hit.meta
            self.ledger.record_cached("llm", m["prompt_tokens"] + m["completion_tokens"])
            return LLMResult(extract_json(m["raw"]), m["raw"], True, m["prompt_tokens"], m["completion_tokens"], key)
        if self.offline:
            if bypass_cache:
                raise OfflineCacheMiss("re-judge needs a live LLM", code="offline_miss")
            raise OfflineCacheMiss("llm call not cached", code="offline_miss")

        async def create(kwargs: dict):
            try:
                return await self._client.chat.completions.create(**kwargs)
            except openai.RateLimitError as exc:  # honour Retry-After (Groq sends it on 429)
                headers = getattr(getattr(exc, "response", None), "headers", None) or {}
                ra = parse_retry_after(headers.get("retry-after"))
                log.info("rate_limited", extra={"fields": {"service": "llm", "retry_after_s": ra}})
                raise RetryableError(f"llm: {exc}", status=429, retry_after=ra) from exc
            except (openai.InternalServerError, openai.APIConnectionError, openai.APITimeoutError) as exc:
                raise RetryableError(f"llm: {exc}") from exc

        async def call():
            async with self._sem:
                kwargs = {"model": self.model, "messages": messages, "temperature": 0, "max_tokens": max_tokens}
                if self._json_mode:
                    try:
                        return await create({**kwargs, "response_format": {"type": "json_object"}})
                    except openai.BadRequestError as exc:
                        log.info("json_mode_unsupported", extra={"fields": {"error": str(exc)[:200]}})
                        self._json_mode = False
                return await create(kwargs)

        t0 = time.perf_counter()
        try:
            resp = await with_retries(
                call,
                what="llm",
                attempts=6,
                max_delay=60.0,
                extra_retryable=(openai.RateLimitError, openai.InternalServerError, openai.APIConnectionError, openai.APITimeoutError),
            )
        except (openai.OpenAIError, RetryableError) as exc:  # retries exhausted, auth error, ...: the judge is unavailable
            raise LLMError(str(exc)) from exc
        raw = (resp.choices[0].message.content or "") if resp.choices else ""
        usage = getattr(resp, "usage", None)
        pt = int(getattr(usage, "prompt_tokens", 0) or 0)
        ct = int(getattr(usage, "completion_tokens", 0) or 0)
        self.ledger.record_free("llm", pt + ct)
        data = extract_json(raw)
        if data is not None and not bypass_cache:  # never cache an unparseable answer; a retry may succeed
            self.cache.put(key, "llm", {"raw": raw, "prompt_tokens": pt, "completion_tokens": ct, "model": self.model})
        log.info(
            "llm_call",
            extra={
                "fields": {
                    "model": self.model,
                    "variant": variant,
                    "prompt_tokens": pt,
                    "completion_tokens": ct,
                    "parsed": data is not None,
                    "latency_s": round(time.perf_counter() - t0, 3),
                }
            },
        )
        return LLMResult(data, raw, False, pt, ct, key)


# -------------------- generate
log = logging.getLogger("bodhan_eval.generate")


DEFAULT_TTS_MAX_CHARS = 110


@dataclass
class GenResult:
    translation: str | None = None  # generated text (None = not generated)
    audio: str | None = None  # generated audio path relative to the report dir
    skipped: list[str] = field(default_factory=list)  # e.g. "audio:too_long", "translation:budget"
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        out: dict = {}
        if self.translation is not None:
            out["translation"] = self.translation
        if self.audio is not None:
            out["audio"] = self.audio
        if self.skipped:
            out["skipped"] = sorted(self.skipped)
        if self.errors:
            out["errors"] = sorted(self.errors)
        return out

    @property
    def budget_skipped(self) -> bool:
        return any(s.endswith(":budget") for s in self.skipped)


def failure_reason(exc: Exception) -> str:
    if isinstance(exc, BodhanError):
        return exc.code or "error"
    if isinstance(exc, RetryableError):
        return "rate_limited" if exc.status == 429 else f"http_{exc.status or 'network'}"
    return type(exc).__name__


def safe_filename(item_id: str) -> str:
    """File-system safe, collision-free name for an item id."""
    clean = re.sub(r"[^A-Za-z0-9_.-]", "_", item_id)
    if clean != item_id:
        clean += "-" + hashlib.sha256(item_id.encode("utf-8")).hexdigest()[:8]
    return clean


async def generate_missing(bodhan: BodhanClient, item: Item, out_dir: Path, tts_max_chars: int = DEFAULT_TTS_MAX_CHARS) -> GenResult:
    """Fill ``item.translation`` / ``item.audio`` in place where missing."""
    res = GenResult()
    if item.translation is None:
        try:
            tr = await bodhan.translate(item.source, item.lang)
            if tr.text.strip():
                item.translation = tr.text
                res.translation = tr.text
            else:
                res.errors.append("translation:empty_output")
        except BudgetExceeded:
            res.skipped.append("translation:budget")
        except Exception as exc:  # API error, or retries exhausted: this item only
            res.errors.append(f"translation:{failure_reason(exc)}")
            log.warning("generate_failed", extra={"fields": {"item": item.id, "what": "translation", "error": str(exc)}})

    if item.audio is None and "audio" not in item.raw:
        text = item.translation
        if not text or not text.strip():
            res.skipped.append("audio:no_text")
        elif len(text) > tts_max_chars:
            res.skipped.append("audio:too_long")
        else:
            try:
                sp = await bodhan.speak(text, item.lang)
                rel = Path("audio") / f"{safe_filename(item.id)}.wav"
                path = out_dir / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(sp.wav)
                item.audio = path
                res.audio = rel.as_posix()
            except BudgetExceeded:
                res.skipped.append("audio:budget")
            except Exception as exc:  # API error, or retries exhausted: this item only
                res.errors.append(f"audio:{failure_reason(exc)}")
                log.warning("generate_failed", extra={"fields": {"item": item.id, "what": "audio", "error": str(exc)}})
    return res
