"""Thin async client for the four Bodhan endpoints: retries, concurrency limit, cache, cost accounting."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import random
import logging
import os
import wave
from typing import Any

import httpx

from . import cache
from .config import KEY_ENV, get_settings
from .costs import (PRICE_OCR_PER_IMAGE, PRICE_SPEAK_PER_10K_CHARS, PRICE_TRANSCRIBE_PER_MIN,
                    PRICE_TRANSLATE_PER_10K_OUT_TOKENS, tracker)
from .errors import ToolFailure

_transport: httpx.AsyncBaseTransport | None = None  # tests inject a fake Bodhan here
_sems: dict[int, asyncio.Semaphore] = {}
_key_ok: set[str] = set()  # services whose key has worked at least once
_translate_use_chat = False  # flips to True if POST /translate answers 404
log = logging.getLogger("bodhan_mcp.client")


def set_transport(t: httpx.AsyncBaseTransport | None) -> None:
    global _transport
    _transport = t


def _sem() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    key = id(loop)
    if key not in _sems:
        _sems[key] = asyncio.Semaphore(max(1, get_settings().concurrency))
    return _sems[key]


def wav_duration(data: bytes) -> float:
    with wave.open(io.BytesIO(data), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def _error_text(resp: httpx.Response) -> tuple[str, str | None]:
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:300], None
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err)[:400], err.get("code")
    if isinstance(body, dict) and "detail" in body:
        return str(body["detail"])[:400], None
    return str(body)[:400], None


async def _post(service: str, path: str, **kw: Any) -> httpx.Response:
    s = get_settings()
    key = s.key_for(service)
    if not key:
        raise ToolFailure("missing_api_key", f"No API key for the {service} model.",
                          hint=f"Set {KEY_ENV[service]} (or BODHAN_API_KEY) in the environment / .env file.")
    headers = {"Authorization": f"Bearer {key}"}
    url = s.base_url + path
    last: str = ""
    auth_retried = False
    for attempt in range(s.max_retries + 1):
        try:
            async with _sem():
                async with httpx.AsyncClient(transport=_transport, timeout=s.http_timeout_s) as c:
                    resp = await c.post(url, headers=headers, **kw)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last = f"{type(e).__name__}: {e}"
            if attempt == s.max_retries:
                raise ToolFailure("network_error", f"The Bodhan {service} API could not be reached ({last}).",
                                  hint="Retry later; results already produced are cached.", retryable=True)
            await asyncio.sleep(min(20, 2 ** attempt) * (0.7 + random.random() * 0.6))
            continue
        code = resp.status_code
        retry_5xx = code in (500, 503, 504) or (code == 502 and service != "ocr")
        if code == 429 or retry_5xx:
            last = f"HTTP {code}"
            if attempt == s.max_retries:
                raise ToolFailure("rate_limited" if code == 429 else "upstream_error",
                                  f"The Bodhan {service} API kept failing ({last}) after {attempt + 1} attempts.",
                                  hint="Wait and retry, or reduce concurrency (BODHAN_CONCURRENCY).",
                                  retryable=True)
            try:
                wait = float(resp.headers.get("retry-after", ""))
            except ValueError:
                wait = min(20, 2 ** attempt)
            await asyncio.sleep(wait * (0.7 + random.random() * 0.6))
            continue
        if code == 401 and service in _key_ok and not auth_retried:
            auth_retried = True  # key worked before: treat a blip as retryable, once
            await asyncio.sleep(1)
            continue
        if code < 400:
            _key_ok.add(service)
        return resp
    raise ToolFailure("upstream_error", f"Bodhan {service} API failed: {last}", retryable=True)


def _raise_for_status(resp: httpx.Response, service: str) -> None:
    if resp.status_code < 400:
        return
    msg, code = _error_text(resp)
    st = resp.status_code
    if st == 401:
        raise ToolFailure("auth_error", f"The {service} API key was rejected: {msg}",
                          hint=f"Check {KEY_ENV[service]}; keys are issued per model.")
    if st == 413:
        raise ToolFailure("payload_too_large", f"Input too large for {service}: {msg}",
                          hint="Split the input into smaller pieces.")
    if st == 415:
        raise ToolFailure("unsupported_media", f"Audio format refused: {msg}",
                          hint="Send WAV, FLAC, OGG or MP3 (M4A/AAC/WebM/AMR are refused).")
    if st in (400, 422):
        raise ToolFailure("bad_request", f"The {service} API rejected the request: {msg}",
                          details={"upstream_code": code})
    raise ToolFailure("upstream_error", f"The {service} API returned HTTP {st}: {msg}", retryable=st >= 500)


# ------------------------------------------------------------------ speech to text
async def transcribe(wav_bytes: bytes, language: str | None) -> dict[str, Any]:
    dur = wav_duration(wav_bytes)
    if dur > 30.0:
        raise ToolFailure("internal_error", f"Chunk of {dur:.1f}s exceeds the 30 s API window.")
    fp = cache.fingerprint("asr", wav_bytes, language)
    hit = cache.get_json("asr", fp)
    tracker.note_call("transcribe", fp, cached=hit is not None)
    if hit is not None:
        return {**hit, "cached": True}
    cost = dur / 60.0 * PRICE_TRANSCRIBE_PER_MIN
    data = {"model": "indic-transcribe"}
    if language:
        data["language"] = language
    with tracker.reserved(cost):
        resp = await _post("transcribe", "/v1/audio/transcriptions",
                           data=data, files={"file": ("chunk.wav", wav_bytes, "audio/wav")})
        _raise_for_status(resp, "transcribe")
        text = (resp.json().get("text") or "").strip()
        tracker.charge("transcribe", cost)
    out = {"text": text, "seconds": dur}
    cache.put_json("asr", fp, out)
    return {**out, "cached": False}


# ------------------------------------------------------------------ translation
async def translate(text: str, target: str, script: str = "native") -> dict[str, Any]:
    """Two transports give identical output: POST /translate (preferred) and the chat-completions route
    (single user turn, 'Translate the following text into X:'). BODHAN_TRANSLATE_TRANSPORT = auto | translate | chat.
    In auto mode a 404 from /translate switches to the chat route for the rest of the process."""
    global _translate_use_chat
    fp = cache.fingerprint("mt", text, target, script)
    hit = cache.get_json("mt", fp)
    tracker.note_call("translate", fp, cached=hit is not None)
    if hit is not None:
        return {**hit, "cached": True}
    tracker.precheck(0.01)
    mode = os.environ.get("BODHAN_TRANSLATE_TRANSPORT", "auto").lower()
    use_chat = mode == "chat" or (mode == "auto" and _translate_use_chat)
    out: dict[str, Any] | None = None
    if not use_chat:
        body: dict[str, Any] = {"text": text, "target_language": target}
        if script != "native" and target != "en":
            body["script"] = script
        resp = await _post("translate", "/translate", json=body)
        if resp.status_code == 404 and mode == "auto":
            log.warning("POST /translate returned 404; using the chat-completions route for translation instead.")
            _translate_use_chat = use_chat = True
        else:
            _raise_for_status(resp, "translate")
            j = resp.json()
            if "translation" not in j:
                raise ToolFailure("upstream_error", f"Unexpected translate response: {str(j)[:200]}", retryable=True)
            out = {"translation": j["translation"], "truncated": bool(j.get("truncated")),
                   "tokens": int((j.get("usage") or {}).get("completion_tokens") or 0)}
    if use_chat:
        from .langs import translate_prompt
        resp = await _post("translate", "/v1/chat/completions", json={
            "model": "indic-translate",
            "messages": [{"role": "user", "content": translate_prompt(text, target, script)}]})
        _raise_for_status(resp, "translate")
        j = resp.json()
        try:
            choice = j["choices"][0]
            out = {"translation": (choice["message"]["content"] or "").strip(),
                   "truncated": choice.get("finish_reason") == "length",
                   "tokens": int((j.get("usage") or {}).get("completion_tokens") or 0)}
        except (KeyError, IndexError, TypeError):
            raise ToolFailure("upstream_error", f"Unexpected chat response: {str(j)[:200]}", retryable=True)
    assert out is not None
    tracker.charge("translate", out["tokens"] / 10000.0 * PRICE_TRANSLATE_PER_10K_OUT_TOKENS)
    if not out["truncated"]:  # never cache an incomplete translation
        cache.put_json("mt", fp, out)
    return {**out, "cached": False}


# ------------------------------------------------------------------ speech synthesis
async def speak(text: str, voice: str, lang: str, style: str | None) -> dict[str, Any]:
    fp = cache.fingerprint("tts", text, voice, lang, style)
    hit = cache.get_bytes("tts", fp)
    tracker.note_call("speak", fp, cached=hit is not None)
    if hit is not None:
        return {"wav": hit, "cached": True, "chars": len(text)}
    cost = len(text) / 10000.0 * PRICE_SPEAK_PER_10K_CHARS
    instr = {"lang": lang}
    if style:
        instr["style"] = style
    with tracker.reserved(cost):
        resp = await _post("speak", "/v1/audio/speech",
                           json={"model": "indic-speak", "input": text, "voice": voice,
                                 "instructions": json.dumps(instr)})
        _raise_for_status(resp, "speak")
        wav = resp.content
        if not wav[:4] == b"RIFF":
            raise ToolFailure("upstream_error", "Indic-Speak did not return WAV audio.", retryable=True)
        tracker.charge("speak", cost)
    cache.put_bytes("tts", fp, wav)
    return {"wav": wav, "cached": False, "chars": len(text)}


# ------------------------------------------------------------------ OCR
async def ocr(image_bytes: bytes, mime: str, table_format: str = "markdown", max_tokens: int = 2048) -> dict[str, Any]:
    fp = cache.fingerprint("ocr", image_bytes, table_format)  # max_tokens only affects failures
    hit = cache.get_json("ocr", fp)
    tracker.note_call("ocr", fp, cached=hit is not None)
    if hit is not None:
        return {**hit, "cached": True}
    with tracker.reserved(PRICE_OCR_PER_IMAGE):
        last_err: ToolFailure | None = None
        for mt in (max_tokens, max(max_tokens, 4096), 8192):
            b64 = base64.b64encode(image_bytes).decode()
            body = {"model": "indic-ocr", "table_format": table_format, "max_tokens": mt,
                    "messages": [{"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]}]}
            resp = await _post("ocr", "/v1/chat/completions", json=body)
            if resp.status_code == 502:
                msg, _ = _error_text(resp)
                last_err = ToolFailure("ocr_failed", f"Indic-OCR could not transcribe the page: {msg}",
                                       hint="A block was too long for max_tokens, or the image could not be decoded.")
                if "token" in msg.lower() or "max" in msg.lower():
                    continue  # retry once or twice with a bigger per-block budget
                break
            _raise_for_status(resp, "ocr")
            j = resp.json()
            tracker.charge("ocr", PRICE_OCR_PER_IMAGE)
            out = {"markdown": (j.get("choices") or [{}])[0].get("message", {}).get("content", "") or "",
                   "blocks": j.get("blocks") or []}
            cache.put_json("ocr", fp, out)
            return {**out, "cached": False}
    raise last_err or ToolFailure("ocr_failed", "Indic-OCR failed.")
