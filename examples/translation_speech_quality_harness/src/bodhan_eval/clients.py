"""Every call to Bodhan or the LLM goes through here: cache first, then budget, retries, concurrency, logging."""

import io
import json
import random
import threading
import time
import wave

import httpx
import openai

from . import config
from .cache import Cache, key_for

KEY_ENV = {"translate": "BODHAN_TRANSLATE_KEY", "speak": "BODHAN_SPEAK_KEY", "transcribe": "BODHAN_TRANSCRIBE_KEY"}
MAX_ATTEMPTS = 8  # backoff 1, 2, 4 ... 30 s: about 2.5 minutes, longer than a per-minute rate window


class NotRun(Exception):
    """A check could not run. Never a finding about the item."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class RunLog:
    """Structured events for run_log.jsonl. Not part of the deterministic report."""

    def __init__(self):
        self.events: list[dict] = []
        self._lock = threading.Lock()

    def event(self, **kw):
        with self._lock:
            self.events.append({"t": round(time.time(), 3), **kw})

    def calls(self):
        return [e for e in self.events if e.get("kind") == "call"]


class Budget:
    def __init__(self, limit_inr: float | None):
        self.limit = limit_inr
        self.spent = 0.0
        self._lock = threading.Lock()

    def reserve(self, amount: float):
        # Reserve before the call so concurrent calls cannot overshoot the cap together.
        with self._lock:
            if self.limit is not None and self.spent + amount > self.limit + 1e-9:
                raise NotRun("budget: --max-cost-inr reached")
            self.spent += amount

    def settle(self, reserved: float, actual: float):
        with self._lock:
            self.spent += actual - reserved


class _Base:
    def __init__(self, cache: Cache, log: RunLog):
        self.cache, self.log = cache, log

    def _cached(self, service, request, ctx, call, est_cost=0.0, budget=None):
        """Return cached (meta, blob) for this exact request, or make the call once and cache it."""
        k = key_for(service, request)
        hit = self.cache.get(k)
        if hit:
            self.log.event(kind="call", service=service, key=k, cache="hit", cost_inr=hit[0].get("cost_inr", 0.0), spent_inr=0.0, **(ctx or {}))
            return hit
        if budget:
            budget.reserve(est_cost)
        t0 = time.time()
        try:
            meta, blob, cost, retries = call()
        except BaseException:
            if budget:
                budget.settle(est_cost, 0.0)
            raise
        if budget:
            budget.settle(est_cost, cost)
        meta["cost_inr"] = round(cost, 6)
        self.cache.put(k, meta, blob)
        self.log.event(kind="call", service=service, key=k, cache="miss", cost_inr=meta["cost_inr"], spent_inr=meta["cost_inr"],
                       latency_s=round(time.time() - t0, 3), retries=retries, **(ctx or {}))
        return meta, blob


def wav_seconds(data: bytes) -> float:
    try:
        with wave.open(io.BytesIO(data)) as w:
            return w.getnframes() / float(w.getframerate())
    except (wave.Error, EOFError) as e:
        raise NotRun(f"input: not a readable WAV ({e})") from None


class Bodhan(_Base):
    def __init__(self, keys: dict, cache: Cache, budget: Budget, log: RunLog, concurrency=4, timeout=120.0,
                 transport: httpx.BaseTransport | None = None, base_url=config.BODHAN_BASE_URL, sleep=time.sleep):
        super().__init__(cache, log)
        self.keys, self.budget, self.sleep = keys, budget, sleep
        self.http = httpx.Client(base_url=base_url, timeout=timeout, transport=transport, headers={"User-Agent": "bodhan-eval"})
        self.sem = {s: threading.Semaphore(concurrency) for s in KEY_ENV}

    def _post(self, service, path, **kw):
        key = self.keys.get(service)
        if not key:
            raise NotRun(f"auth: {KEY_ENV[service]} is not set")
        last = ""
        for attempt in range(MAX_ATTEMPTS):
            wait = min(30.0, 2**attempt) * (0.5 + random.random() / 2)
            try:
                with self.sem[service]:
                    r = self.http.post(path, headers={"Authorization": f"Bearer {key}"}, **kw)
            except httpx.TransportError as e:
                last = type(e).__name__
            else:
                if r.status_code == 200:
                    return r, attempt
                last = f"HTTP {r.status_code}: {r.text[:200]}"
                if r.status_code == 401 or "key_model_access_denied" in r.text:
                    raise NotRun(f"auth: {KEY_ENV[service]} rejected ({r.status_code})")
                # 403 is retried: the gateway returns transient 403s right after 429s.
                if r.status_code not in (403, 408, 409, 429) and r.status_code < 500:
                    raise NotRun(f"api: {service} {last}")
                try:
                    wait = max(wait, float(r.headers.get("retry-after", 0)))
                except ValueError:
                    pass
            if attempt < MAX_ATTEMPTS - 1:
                self.sleep(min(wait, 60.0))
        raise NotRun(f"api: {service} failed after {MAX_ATTEMPTS} attempts ({last})")

    def translate(self, text: str, lang: str, ctx: dict | None = None) -> dict:
        """Translate with Indic-Translate. Returns {'text', 'finish_reason', 'usage', 'cost_inr'}."""
        if not text or not text.strip():
            raise NotRun("input: empty text")
        prompt = f"Translate the following text into {config.prompt_name(lang)}:\n\n{text}"
        # Indic output needs about 1.5-2x the tokens of the English source; unused budget costs nothing.
        budget_tokens = min(8192, max(256, 2 * len(text)))
        for max_tokens in (budget_tokens, min(8192, budget_tokens * 2)):
            # /translate returns 404 for issued keys; the chat route with the model card's prompt works.
            # One user turn, no system message, greedy decoding (the default sampling is not reproducible).
            body = {"model": "indic-translate", "messages": [{"role": "user", "content": prompt}], "temperature": 0, "max_tokens": max_tokens}

            def call(body=body):
                r, retries = self._post("translate", "/v1/chat/completions", json=body)
                data = r.json()
                choice = data["choices"][0]
                usage = data.get("usage") or {}
                cost = usage.get("completion_tokens", 0) * config.PRICE_TRANSLATE_PER_OUTPUT_TOKEN
                meta = {"text": choice["message"]["content"] or "", "finish_reason": choice.get("finish_reason"),
                        "usage": usage, "fingerprint": data.get("system_fingerprint")}
                return meta, None, cost, retries

            meta, _ = self._cached("translate", body, ctx, call, max_tokens * config.PRICE_TRANSLATE_PER_OUTPUT_TOKEN, self.budget)
            if meta.get("finish_reason") != "length":
                return meta
        raise NotRun("truncated: translation hit the token limit twice")

    def speak(self, text: str, lang: str, voice: str | None = None, ctx: dict | None = None) -> bytes:
        """Synthesize speech with Indic-Speak. Returns WAV bytes."""
        if not text or not text.strip():
            raise NotRun("input: empty text")  # the API answers 500, not 422, to empty input
        slang = config.speak_lang(lang)
        if not slang:
            raise NotRun(f"unsupported: no Indic-Speak voice for {lang}")
        body = {"model": "indic-speak", "input": text, "voice": voice or config.default_voice(lang), "instructions": json.dumps({"lang": slang})}

        def call():
            r, retries = self._post("speak", "/v1/audio/speech", json=body)
            if r.content[:4] != b"RIFF":  # header says audio/mpeg but the body is WAV; sniff bytes
                raise NotRun("api: speech returned a non-WAV body")
            return {"chars": len(text), "bytes": len(r.content)}, r.content, len(text) * config.PRICE_SPEAK_PER_CHAR, retries

        _, blob = self._cached("speak", body, ctx, call, len(text) * config.PRICE_SPEAK_PER_CHAR, self.budget)
        return blob

    def transcribe(self, wav: bytes, lang: str, ctx: dict | None = None) -> str:
        """Transcribe one WAV clip (callers keep clips within 30 s). Only WAV is accepted by the hosted API."""
        seconds = wav_seconds(wav)
        cost = seconds / 60 * config.PRICE_TRANSCRIBE_PER_MINUTE
        request = {"audio_sha256": key_for(wav), "language": lang, "model": "indic-transcribe"}

        def call():
            r, retries = self._post("transcribe", "/v1/audio/transcriptions", files={"file": ("audio.wav", wav, "audio/wav")},
                                    data={"model": "indic-transcribe", "language": lang})
            return {"text": r.json().get("text") or "", "seconds": round(seconds, 3)}, None, cost, retries

        meta, _ = self._cached("transcribe", request, ctx, call, cost, self.budget)
        return meta["text"]


class LLM(_Base):
    """Any OpenAI-compatible chat endpoint. Used as the judge; never writes Indian-language text into outputs."""

    def __init__(self, base_url: str | None, api_key: str | None, model: str | None, cache: Cache, log: RunLog,
                 concurrency=4, timeout=120.0, http_client: httpx.Client | None = None):
        super().__init__(cache, log)
        self.model = model
        self.available = bool(base_url and model)
        self.sem = threading.Semaphore(concurrency)
        self._drop: set[str] = set()  # parameters the endpoint rejected
        if self.available:
            self.client = openai.OpenAI(base_url=base_url.strip().rstrip("/"), api_key=api_key or "none", timeout=timeout,
                                        max_retries=8, http_client=http_client)  # free tiers limit tokens per minute

    def chat(self, messages: list[dict], tag: str = "judge", max_tokens: int = 4000, ctx: dict | None = None) -> str:
        if not self.available:
            raise NotRun("llm: LLM_BASE_URL or LLM_MODEL is not set")
        request = {"model": self.model, "messages": messages, "tag": tag}

        def call():
            params = {"temperature": 0, "max_tokens": max_tokens}
            for _ in range(3):
                kw = {k: v for k, v in params.items() if k not in self._drop}
                try:
                    with self.sem:
                        resp = self.client.chat.completions.create(model=self.model, messages=messages, **kw)
                    break
                except openai.BadRequestError as e:
                    # Some endpoints reject temperature or the max_tokens name; drop the offending parameter and retry.
                    msg = str(e).lower()
                    bad = [p for p in kw if p in msg]
                    if not bad:
                        raise NotRun(f"llm: bad request ({str(e)[:150]})") from None
                    self._drop.update(bad)
                    if "max_tokens" in bad:
                        params["max_completion_tokens"] = max_tokens
                except openai.APIError as e:
                    raise NotRun(f"llm: {type(e).__name__}") from None
            else:
                raise NotRun("llm: endpoint rejected every parameter combination")
            usage = resp.usage.model_dump() if resp.usage else {}
            return {"text": resp.choices[0].message.content or "", "usage": usage}, None, 0.0, 0

        meta, _ = self._cached("llm", request, ctx, call)
        return meta["text"]
