"""Client for the three Bodhan models used by the pipeline. Every ASR, MT and TTS call goes through here.

- Indic-Transcribe: POST /v1/audio/transcriptions   (multipart, <=30 s of audio, returns {"text"})
- Indic-OCR:        POST /v1/chat/completions (model indic-ocr, image as base64 data URI, returns blocks with bbox)
- Indic-Translate:  POST /translate (documented) with POST /v1/chat/completions as the fallback on 404
- Indic-Speak:      POST /v1/audio/speech           (JSON, returns PCM16 24 kHz mono WAV)

Retries with exponential backoff on 429/5xx, a global concurrency limit, an on-disk cache so
reruns do not repeat paid calls, and per-model cost metering.
"""
import base64
import hashlib
import json
import os
import threading
import time
from pathlib import Path

import httpx

from . import config
from .logs import log


class BodhanError(RuntimeError):
    def __init__(self, msg: str, status: int | None = None, code: str | None = None):
        super().__init__(msg)
        self.status, self.code = status, code


def is_budget_error(text: str) -> bool:
    """Bodhan answers HTTP 429 both for rate limits and for a spent budget ("ExceededBudget ... over budget").
    The second one never clears by waiting, so it must not be retried."""
    t = (text or "").lower()
    return "exceededbudget" in t or "budget_exceeded" in t or "over budget" in t


def hint(status: int | None, model: str, body: str = "") -> str:
    """Plain-English next step for the errors people actually hit."""
    if status in (401, 403):
        return f" | Hint: the key was rejected. Check it is correct and was created for {model} (each Bodhan key serves one model)."
    if status == 404:
        return (f" | Hint: route not found for this key. Most often the key was not created for {model} "
                "(each Bodhan key serves one model), or BODHAN_BASE_URL is wrong.")
    if status == 429 and is_budget_error(body):
        return (" | Hint: this is not a rate limit. The Bodhan account behind this key has used up its budget (see Spend and Budget above), "
                "so every call is refused until the budget is raised. Waiting or lowering concurrency will not help: ask Bodhan to "
                "top up or raise the budget in the console, then run again. Cached results from earlier runs are reused and cost nothing.")
    if status == 429:
        return (" | Hint: Bodhan is limiting requests for this key: above its request-per-minute or parallel limit (the text before this hint is "
                "Bodhan's own reply). Retries count against that limit, so the app now spaces calls to the limit Bodhan reports and waits "
                "for it to refill. If it keeps happening set DUB_CONCURRENCY=1 and ask Bodhan to raise the limit.")
    return ""


class Meter:
    """Counts calls, retries, latency and cost (INR) per model. Thread safe."""

    def __init__(self):
        self.lock = threading.Lock()
        self.data = {m: {"calls": 0, "cache_hits": 0, "retries": 0, "seconds": 0.0, "cost_inr": 0.0}
                     for m in ("transcribe", "translate", "speak", "ocr")}

    def add(self, model: str, **kw):
        with self.lock:
            for k, v in kw.items():
                self.data[model][k] += v

    def snapshot(self) -> dict:
        with self.lock:
            out = {m: {k: round(v, 4) for k, v in d.items()} for m, d in self.data.items()}
        out["total_cost_inr"] = round(sum(d["cost_inr"] for d in out.values() if isinstance(d, dict)), 4)
        return out


class Gate:
    """A concurrency limit whose size can change at run time (the API reports its own parallel-request limit in a header)."""

    def __init__(self, limit: int):
        self.limit, self.active, self.cv = max(1, limit), 0, threading.Condition()

    def set_limit(self, n: int):
        with self.cv:
            self.limit = max(1, n)
            self.cv.notify_all()

    def __enter__(self):
        with self.cv:
            while self.active >= self.limit:
                self.cv.wait(timeout=1.0)
            self.active += 1

    def __exit__(self, *a):
        with self.cv:
            self.active -= 1
            self.cv.notify_all()


class BodhanClient:
    def __init__(self, keys: config.Keys, cache_dir: Path | None, max_concurrency: int = 4,
                 meter: Meter | None = None, base_url: str | None = None, max_attempts: int = 5,
                 backoff: float = 1.5, http: httpx.Client | None = None):
        self.keys, self.cache_dir = keys, cache_dir
        self.max_conc = max(1, max_concurrency)
        self.gates: dict[str, Gate] = {}
        self.floor: dict[str, float] = {}      # minimum seconds between requests, learnt from the API's own rate-limit headers
        self.meter = meter or Meter()
        self.base = (base_url or config.BASE_URL).rstrip("/")
        self.max_attempts, self.backoff = max_attempts, backoff
        # Pacing: minimum seconds between request starts per model. Starts at BODHAN_MIN_INTERVAL (default 0) and
        # doubles after each 429 (up to 15 s), shared by every thread, then relaxes after successes.
        self.base_pace = float(os.environ.get("BODHAN_MIN_INTERVAL", "0") or 0)
        self.pace, self.next_at, self.plock = {}, {}, threading.Lock()
        self.http = http or httpx.Client(timeout=httpx.Timeout(120, connect=15))
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)

    def with_meter(self, meter: "Meter") -> "BodhanClient":
        """Same connection, cache and concurrency limit, but costs are counted on a separate meter (per language)."""
        c = object.__new__(BodhanClient)
        c.__dict__.update(self.__dict__)
        c.meter = meter
        return c

    # ---- cache ----
    def _ck(self, kind: str, *parts) -> str:
        return hashlib.sha256(json.dumps([kind, *parts], ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def _cget(self, key: str, ext: str):
        if not self.cache_dir:
            return None
        p = self.cache_dir / f"{key}.{ext}"
        return p.read_bytes() if p.exists() else None

    def _cput(self, key: str, ext: str, data: bytes):
        if self.cache_dir:
            (self.cache_dir / f"{key}.{ext}").write_bytes(data)

    # ---- http with retries ----
    def _gate(self, model: str) -> Gate:
        with self.plock:
            return self.gates.setdefault(model, Gate(self.max_conc))

    def _learn_limits(self, model: str, r):
        """The docs: every response carries x-ratelimit-limit-requests and x-ratelimit-limit-parallel-requests, and
        "retries count against the request limit". So spend the limit evenly instead of bursting and retrying."""
        try:
            rpm = int(r.headers.get("x-ratelimit-limit-requests") or 0)
            par = int(r.headers.get("x-ratelimit-limit-parallel-requests") or 0)
        except ValueError:
            return
        with self.plock:
            changed = False
            if rpm > 0:
                fl = 60.0 / rpm * 1.15                    # 15% headroom; the header is read as requests per minute
                if abs(self.floor.get(model, 0) - fl) > 1e-6:
                    self.floor[model], changed = fl, True
                    self.pace[model] = max(self.pace.get(model, self.base_pace), fl)
                    self.next_at[model] = max(self.next_at.get(model, 0.0), time.monotonic() + fl)   # the call that just returned used a slot
            if par > 0:
                g = self.gates.setdefault(model, Gate(self.max_conc))
                if g.limit != min(par, self.max_conc):
                    g.set_limit(min(par, self.max_conc))
                    changed = True
        if changed:
            log("api_limits", api=model, requests_per_minute=rpm or "?", parallel=par or "?", min_interval_s=round(self.floor.get(model, 0), 2))

    def _pace_wait(self, model: str):
        iv = self.pace.get(model, self.base_pace)
        if iv <= 0:
            return
        with self.plock:
            now = time.monotonic()
            at = max(now, self.next_at.get(model, 0.0))
            self.next_at[model] = at + iv
        if at > now:
            time.sleep(at - now)

    def _slow_down(self, model: str):
        if self.backoff <= 0:
            return
        with self.plock:
            self.pace[model] = min(30.0, max(self.pace.get(model, self.base_pace) * 2, self.backoff, self.floor.get(model, 0.0)))

    def _relax(self, model: str):
        with self.plock:
            cur = self.pace.get(model)
            if cur:
                low = max(self.base_pace, self.floor.get(model, 0.0))
                self.pace[model] = max(low, cur * 0.8)

    def _request(self, model: str, label: str, fn):
        last = None
        for attempt in range(1, self.max_attempts + 1):       # retries count against the API's request limit: no extra 429 attempts
            t0 = t1 = time.time()
            try:
                self._pace_wait(model)
                with self._gate(model):
                    t1 = time.time()                                  # latency is the call itself, not the time spent queued behind the limits
                    r = fn()
                took = time.time() - t1
                self._learn_limits(model, r)
                self.meter.add(model, calls=1, seconds=took)
                if r.status_code == 200:
                    self._relax(model)
                    log("api_call", api=label, status=200, seconds=round(took, 2), queued_s=round(t1 - t0, 2))
                    return r
                if r.status_code == 429 and is_budget_error(r.text):
                    log("api_error", api=label, status=429, body=r.text[:300], reason="budget_exceeded", request_id=r.headers.get("x-request-id", ""))
                    raise BodhanError(f"{label}: HTTP 429: {r.text[:300]}{hint(429, 'indic-' + model, r.text)}", 429, "budget_exceeded")
                if r.status_code in (429, 500, 502, 503, 504):
                    ra = float(r.headers.get("retry-after", 0) or 0)
                    # a 429 means the minute's budget is spent: wait for it to refill instead of retrying quickly
                    wait = min(90.0, ra or (self.backoff * 2 ** (attempt + 1) if r.status_code == 429 else min(30.0, self.backoff * 2 ** (attempt - 1))))
                    body = r.text[:200].replace("\n", " ")
                    detail = f": {body}" if body and r.status_code == 429 else ""
                    last = BodhanError(f"{label}: HTTP {r.status_code}{detail}{hint(r.status_code, 'indic-' + model, r.text)}", r.status_code)
                    self.meter.add(model, retries=1)
                    if r.status_code == 429:
                        self._slow_down(model)
                    log("api_retry", api=label, status=r.status_code, attempt=attempt, wait=round(wait, 1), body=body,
                        retry_after=r.headers.get("retry-after", ""), ratelimit=r.headers.get("x-ratelimit-remaining", ""),
                        request_id=r.headers.get("x-request-id", ""))
                    time.sleep(wait)
                    continue
                code = None
                try:
                    code = r.json().get("error", {}).get("code")
                except Exception:
                    pass
                log("api_error", api=label, status=r.status_code, body=r.text[:300], request_id=r.headers.get("x-request-id", ""))
                raise BodhanError(f"{label}: HTTP {r.status_code}: {r.text[:300]}{hint(r.status_code, 'indic-' + model)}", r.status_code, code)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last = BodhanError(f"{label}: {type(e).__name__}")
                self.meter.add(model, retries=1)
                log("api_retry", api=label, error=type(e).__name__, attempt=attempt)
                time.sleep(min(30.0, self.backoff * 2 ** (attempt - 1)))
        raise last or BodhanError(f"{label}: failed")

    def _auth(self, key: str) -> dict:
        return {"Authorization": f"Bearer {key}"}

    # ---- Indic-Transcribe ----
    def transcribe(self, wav_path: Path, lang: str, seconds: float) -> str:
        data = Path(wav_path).read_bytes()
        key = self._ck("stt", hashlib.sha256(data).hexdigest(), lang)
        hit = self._cget(key, "json")
        if hit:
            self.meter.add("transcribe", cache_hits=1)
            return json.loads(hit)["text"]
        r = self._request("transcribe", "transcribe", lambda: self.http.post(
            f"{self.base}/v1/audio/transcriptions", headers=self._auth(self.keys.transcribe),
            files={"file": (Path(wav_path).name, data, "audio/wav")},
            data={"model": "indic-transcribe", "language": lang}))
        text = (r.json().get("text") or "").strip()
        self.meter.add("transcribe", cost_inr=seconds / 60 * config.PRICE_TRANSCRIBE_PER_MIN)
        self._cput(key, "json", json.dumps({"text": text}, ensure_ascii=False).encode())
        return text

    # ---- Indic-Translate ----
    # Documented route first (POST /translate; the docs say to prefer it). The OpenAI-compatible POST /v1/chat/completions is
    # only the fallback if /translate answers 404 for this key. Whichever works is remembered for the rest of the run.
    TRANSLATE_ROUTES = ("/translate", "/v1/chat/completions")

    @staticmethod
    def _translate_body(path: str, text: str, lang: str, script: str) -> dict:
        if path.endswith("/chat/completions"):
            body = {"model": "indic-translate", "messages": [{"role": "user", "content": text}], "target_language_code": lang}
            if script != "native":
                body["target_script"] = script
            return body
        body = {"text": text, "target_language": lang}
        if script != "native":
            body["script"] = script
        return body

    def _post_translate(self, text: str, lang: str, script: str):
        paths = [os.environ["BODHAN_TRANSLATE_PATH"]] if os.environ.get("BODHAN_TRANSLATE_PATH") else list(self.TRANSLATE_ROUTES)
        start = getattr(self, "_tpath", None)
        if start in paths:
            paths.remove(start)
            paths.insert(0, start)
        err = None
        for path in paths:
            body = self._translate_body(path, text, lang, script)
            try:
                r = self._request("translate", "translate", lambda path=path, body=body: self.http.post(
                    f"{self.base}{path}", headers=self._auth(self.keys.translate), json=body))
                self._tpath = path
                return r
            except BodhanError as e:
                if e.status != 404:
                    raise
                log("translate_path_not_found", path=path, detail=str(e)[:200])
                err = e
        raise err

    def translate(self, text: str, lang: str, script: str = "native") -> dict:
        key = self._ck("mt", text, lang, script)
        hit = self._cget(key, "json")
        if hit:
            self.meter.add("translate", cache_hits=1)
            return json.loads(hit)
        j = self._post_translate(text, lang, script).json()
        try:   # chat-completions shape, else the /translate shape
            content = (j["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError):
            content = (j.get("translation") or "").strip()
        use = j.get("usage") or {}
        tokens = use.get("output_tokens") or use.get("completion_tokens") or max(1, len(content) // 2)
        out = {"text": content, "truncated": bool(j.get("truncated")), "tokens": tokens}
        self.meter.add("translate", cost_inr=tokens / 10000 * config.PRICE_TRANSLATE_PER_10K_TOKENS)
        if not out["truncated"]:  # never cache a cut-off translation
            self._cput(key, "json", json.dumps(out, ensure_ascii=False).encode())
        return out

    # ---- Indic-OCR ----
    def ocr(self, png: bytes, max_tokens: int = 2048) -> dict:
        """One whole image in, reading-ordered blocks out: {"text", "blocks": [{order,label,type,bbox,conf,text}]}.
        POST /v1/chat/completions, model indic-ocr, image as a base64 data URI (PNG or JPEG only)."""
        if not self.keys.ocr:
            raise BodhanError("ocr: BODHAN_OCR_KEY is not set (create a key for Indic-OCR in the Bodhan console)")
        key = self._ck("ocr", hashlib.sha256(png).hexdigest())
        hit = self._cget(key, "json")
        if hit:
            self.meter.add("ocr", cache_hits=1)
            return json.loads(hit)
        uri = "data:image/png;base64," + base64.b64encode(png).decode()

        def call(tokens):
            body = {"model": "indic-ocr", "max_tokens": tokens,
                    "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": uri}}]}]}
            return self._request("ocr", "ocr", lambda: self.http.post(
                f"{self.base}/v1/chat/completions", headers=self._auth(self.keys.ocr), json=body))

        try:
            r = call(max_tokens)
        except BodhanError as e:
            if e.status != 502:
                raise
            log("ocr_retry_more_tokens", detail=str(e)[:160])      # a block was longer than max_tokens (e.g. a big table)
            r = call(max_tokens * 2)
        j = r.json()
        try:
            text = (j["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError):
            text = ""
        blocks = [{"order": int(b.get("order", i)), "label": str(b.get("label", "")), "type": str(b.get("type", "")),
                   "bbox": [float(v) for v in (b.get("bbox_xyxy") or [0, 0, 0, 0])][:4], "conf": float(b.get("conf", 0) or 0),
                   "text": str(b.get("text") or "")} for i, b in enumerate(j.get("blocks") or [])]
        out = {"text": text, "blocks": blocks}
        self.meter.add("ocr", cost_inr=config.PRICE_OCR_PER_IMAGE)
        self._cput(key, "json", json.dumps(out, ensure_ascii=False).encode())
        return out

    # ---- Indic-Speak ----
    def speak(self, text: str, voice: str, lang: str, style: str | None = None, nonce: int = 0) -> bytes:
        instr = {"lang": lang}
        if style:
            instr["style"] = style
        key = self._ck("tts", text, voice, json.dumps(instr, sort_keys=True), nonce)
        hit = self._cget(key, "wav")
        if hit:
            self.meter.add("speak", cache_hits=1)
            return hit
        r = self._request("speak", "speak", lambda: self.http.post(
            f"{self.base}/v1/audio/speech", headers=self._auth(self.keys.speak),
            json={"model": "indic-speak", "input": text, "voice": voice,
                  "instructions": json.dumps(instr)}))
        self.meter.add("speak", cost_inr=len(text) / 10000 * config.PRICE_SPEAK_PER_10K_CHARS)
        self._cput(key, "wav", r.content)
        return r.content
