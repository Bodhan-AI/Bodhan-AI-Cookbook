"""Offline fakes for deterministic tests. They are NOT used by the shipped code.

FakeBodhan is an httpx.MockTransport that imitates the four endpoints and their
limits (<=30 s audio, target_language required, case-sensitive voices). Its
"translation" is a reversible letter substitution into the target script, so
back-translation checks behave like the real thing. Synthesised audio is a tone
whose hash is remembered, so back-transcription returns the spoken text.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import unicodedata
import wave
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import httpx
import numpy as np

from bodhan_mcp.languages import ALL_VOICES, LANG_TO_SCRIPT, TRANSLATE_LANGUAGES

_BLOCKS = {"Devanagari": 0x0900, "Bengali": 0x0980, "Gurmukhi": 0x0A00, "Gujarati": 0x0A80, "Odia": 0x0B00, "Tamil": 0x0B80,
           "Telugu": 0x0C00, "Kannada": 0x0C80, "Malayalam": 0x0D00}


def _letters(script: str) -> list[str]:
    base = _BLOCKS[script]
    out = [chr(c) for c in range(base, base + 0x80) if unicodedata.category(chr(c)) == "Lo"]
    return out[:26]


_FWD = {s: dict(zip("abcdefghijklmnopqrstuvwxyz", _letters(s))) for s in _BLOCKS}
_BWD = {ch: lat for s in _BLOCKS for lat, ch in _FWD[s].items()}


def pseudo_translate(text: str, target: str) -> str:
    if target == "en":
        return "".join(_BWD.get(ch, ch) for ch in text)
    script = LANG_TO_SCRIPT.get(target, "Devanagari")
    if script not in _FWD:
        script = "Devanagari"
    plain = "".join(_BWD.get(ch, ch) for ch in text)  # Indic->Indic goes via Latin
    return "".join(_FWD[script].get(ch, _FWD[script].get(ch.lower(), ch)) for ch in plain)


def wav_tone(seconds: float, rate: int = 24000, freq: float = 220.0, seed: int = 0) -> bytes:
    n = int(seconds * rate)
    t = np.arange(n) / rate
    x = 0.3 * np.sin(2 * math.pi * (freq + seed % 50) * t)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((x * 32767).astype("<i2").tobytes())
    return buf.getvalue()


def wav_duration(data: bytes) -> float:
    with wave.open(io.BytesIO(data)) as w:
        return w.getnframes() / w.getframerate()


@dataclass
class FakeBodhan:
    """Configure behaviour per test, then pass `transport` to BodhanClient."""

    truncate_over_chars: int | None = None  # translate returns finish_reason=length above this size
    fail_first: dict[str, list[int]] = field(default_factory=dict)  # path-suffix -> statuses to return first
    retry_after: str = "0"
    speak_garble: set[int] = field(default_factory=set)  # TTS call indices whose audio "says" something else
    ocr_pages: list[str] = field(default_factory=lambda: ["Page text."])
    reflow_tables: bool = False  # whole-table translation loses the table shape
    calls: Counter = field(default_factory=Counter)
    requests: list[dict[str, Any]] = field(default_factory=list)
    spoken: dict[str, str] = field(default_factory=dict)
    asr_default: str = "नमस्ते यह एक परीक्षण वाक्य है"
    asr_override: str | None = None  # every transcription returns this text
    speak_seconds_per_char: float = 0.07
    ocr_truncate: bool = False  # OCR answers with finish_reason=length
    delay: float = 0.0  # seconds each request takes (to make concurrency observable)
    drop_last_sentence: bool = False  # translation silently omits the final sentence
    append_text: str = ""  # appended to every non-English translation

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle_async)

    def _maybe_fail(self, key: str) -> httpx.Response | None:
        queue = self.fail_first.get(key)
        if queue:
            status = queue.pop(0)
            if status == 0:
                raise httpx.ReadTimeout("simulated timeout")
            headers = {"retry-after": self.retry_after} if status == 429 else {}
            body = {"error": {"message": "insufficient credit balance" if status == 4291 else "simulated", "code": str(status)}}
            return httpx.Response(429 if status == 4291 else status, json=body, headers=headers)
        return None

    async def handle_async(self, request: httpx.Request) -> httpx.Response:
        if self.delay:
            import asyncio

            await asyncio.sleep(self.delay)
        return self.handle(request)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        key = "speech" if path.endswith("/audio/speech") else "transcriptions" if path.endswith("/transcriptions") else "chat"
        if key == "chat":
            body = json.loads(request.content)
            key = "ocr" if body.get("model") == "indic-ocr" else "translate"
        self.calls[key] += 1
        failed = self._maybe_fail(key)
        if failed is not None:
            return failed
        if key == "transcriptions":
            return self._transcribe(request)
        if key == "speech":
            return self._speak(json.loads(request.content))
        if key == "ocr":
            return self._ocr(json.loads(request.content))
        return self._translate(json.loads(request.content))

    def _translate(self, body: dict) -> httpx.Response:
        msgs = body.get("messages") or []
        if any(m.get("role") == "system" for m in msgs) or len(msgs) != 1:
            return httpx.Response(400, json={"error": {"message": "a system turn is not accepted", "code": "400"}})
        target = body.get("target_language")
        if not target:
            target = "hi"  # mirrors the live API's silent default
        if target not in TRANSLATE_LANGUAGES:
            return httpx.Response(400, json={"error": {"message": f"unsupported language '{target}'", "code": "400"}})
        text = msgs[0]["content"]
        self.requests.append({"kind": "translate", "target": target, "text": text})
        out = pseudo_translate(text, target)
        if self.append_text and target != 'en':
            out = out + self.append_text
        if self.drop_last_sentence and target != 'en' and '. ' in text:
            out = pseudo_translate(text.rsplit('. ', 1)[0] + '.', target)
        if self.reflow_tables and "|" in text and "\n" in text:
            out = out.replace("\n", " ")
        finish = "stop"
        if self.truncate_over_chars and len(text) > self.truncate_over_chars:
            out, finish = out[: len(out) // 2], "length"
        return httpx.Response(200, json={"choices": [{"index": 0, "finish_reason": finish, "message": {"role": "assistant", "content": out}}],
                                         "usage": {"prompt_tokens": len(text) // 4, "completion_tokens": max(1, len(out) // 3), "total_tokens": 0}})

    def _speak(self, body: dict) -> httpx.Response:
        voice = body.get("voice")
        if voice not in ALL_VOICES:
            return httpx.Response(500, json={"error": {"message": "Internal server error"}})
        instr = json.loads(body["instructions"])  # must be a JSON string
        assert "lang" in instr
        text = body["input"]
        idx = self.calls["speech"] - 1
        audio = wav_tone(max(0.5, len(text) * self.speak_seconds_per_char), seed=idx)
        said = "कुछ और ही बोला गया" if idx in self.speak_garble else text
        self.spoken[hashlib.sha256(audio).hexdigest()] = said
        self.requests.append({"kind": "speak", "voice": voice, "lang": instr["lang"], "text": text})
        return httpx.Response(200, content=audio, headers={"content-type": "audio/mpeg"})  # mislabelled like the live API

    def _transcribe(self, request: httpx.Request) -> httpx.Response:
        raw = request.read()
        # crude multipart parse: find the WAV payload
        start = raw.find(b"RIFF")
        end = raw.rfind(b"\r\n--")
        wav = raw[start:end]
        seconds = wav_duration(wav)
        if seconds > 30.5:
            return httpx.Response(400, json={"error": {"message": f"audio is {seconds:.1f}s; max 30s", "code": "400"}})
        text = self.asr_override or self.spoken.get(hashlib.sha256(wav).hexdigest(), self.asr_default)
        self.requests.append({"kind": "transcribe", "seconds": round(seconds, 2)})
        return httpx.Response(200, json={"text": text, "usage": None})

    def _ocr(self, body: dict) -> httpx.Response:
        part = body["messages"][0]["content"][0]
        url = part["image_url"]["url"]
        assert url.startswith("data:image/") and ";base64," in url
        base64.b64decode(url.split(",", 1)[1])
        i = (self.calls["ocr"] - 1) % len(self.ocr_pages)
        md = self.ocr_pages[i]
        blocks = [{"order": 0, "label": "Paragraph", "type": "Text", "bbox_xyxy": [0, 0, 10, 10], "conf": 0.9, "text": md}]
        if "|" in md:
            blocks.append({"order": 1, "label": "Table", "type": "Table", "bbox_xyxy": [0, 10, 10, 20], "conf": 0.9, "text": ""})
        return httpx.Response(200, json={"choices": [{"index": 0, "finish_reason": "length" if self.ocr_truncate else "stop", "message": {"role": "assistant", "content": md}}],
                                         "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}, "blocks": blocks})


# ---------------------------------------------------------------------------
# Scripted planner LLM
# ---------------------------------------------------------------------------

class _Obj:
    def __init__(self, **kw: Any):
        self.__dict__.update(kw)


class ScriptedChat:
    """Stands in for openai.AsyncOpenAI().chat.completions. `script` is a list of
    callables(messages) -> list of (tool_name, args) or a str (plain content)."""

    def __init__(self, script: list):
        self.script = list(script)
        self.seen: list[list[dict]] = []
        self.completions = self

    @property
    def chat(self):
        return self

    async def create(self, *, model: str, messages: list[dict], tools: list[dict], tool_choice: str, **extra):
        self.extra = extra
        self.seen.append([dict(m) for m in messages])
        step = self.script.pop(0) if self.script else [("finish", {"status": "failed", "final_answer": "script exhausted"})]
        if callable(step):
            step = step(messages)
        if isinstance(step, str):
            msg = _Obj(content=step, tool_calls=None)
        else:
            calls = [_Obj(id=f"c{len(self.seen)}_{i}", function=_Obj(name=n, arguments=json.dumps(a, ensure_ascii=False))) for i, (n, a) in enumerate(step)]
            msg = _Obj(content="scripted step", tool_calls=calls)
        return _Obj(choices=[_Obj(message=msg, finish_reason="tool_calls")], usage=_Obj(prompt_tokens=100, completion_tokens=10))


def last_tool_result(messages: list[dict], name_hint: str | None = None) -> dict:
    for m in reversed(messages):
        if m.get("role") == "tool":
            return json.loads(m["content"].split("... [truncated")[0]) if "truncated" not in m["content"] else {}
    return {}
