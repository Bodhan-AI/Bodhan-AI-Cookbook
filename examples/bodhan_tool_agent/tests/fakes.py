"""A deterministic in-process stand-in for the Bodhan API, for tests.

make_fake_transport() returns an httpx.MockTransport emulating the four endpoints:

- /v1/audio/transcriptions: if the clip is a tone produced by the fake TTS, returns the exact text
  that was synthesized (the tone frequency encodes it); otherwise a fixed phrase in the requested
  language ("auto_language" when no language is sent).
- /v1/chat/completions with model indic-translate (the live translation route): maps every
  letter to a letter of the target script, keeping digits, punctuation, newlines and Markdown
  intact. Unknown codes get a LiteLLM-style 400; inputs longer than `truncate_over` characters come
  back half-translated with finish_reason "length". /translate answers 404, as the live API does.
- /v1/audio/speech: 24 kHz mono audio whose length is proportional to the input, served as MP3
  (what the live API returns) or WAV (`speak_format="wav"`, what the docs describe).
- /v1/chat/completions with model indic-ocr: a chat.completion with layout blocks; optionally a
  502 while max_tokens is below `ocr_min_tokens`.

`transport.fake` exposes call counters and the recorded requests.
"""

from __future__ import annotations

import io
import json
import re
import subprocess
import unicodedata
import wave
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from bodhan_mcp.config import Settings
from bodhan_mcp.media import ffmpeg_exe
from bodhan_mcp.runtime import Runtime

TTS_RATE = 24_000

PHRASES = {
    "hi": "नमस्ते दुनिया, आज मौसम अच्छा है।",
    "te": "నమస్కారం ప్రపంచం, ఈ రోజు వాతావరణం బాగుంది.",
    "ta": "வணக்கம் உலகம், இன்று வானிலை நன்றாக உள்ளது.",
    "bn": "নমস্কার পৃথিবী, আজ আবহাওয়া ভালো।",
    "mr": "नमस्कार जग, आज हवामान छान आहे.",
    "en": "hello world, the weather is nice today.",
}

SCRIPT_LETTERS = {
    "Devanagari": "कखगघचछजझटठडढणतथदधनपफबभमयरलवशसह",
    "Tamil": "கஙசஞடணதநபமயரலவழளறன",
    "Telugu": "కఖగఘచఛజఝటఠడఢణతథదధనపఫబభమయరలవ",
    "Bengali": "কখগঘচছজঝটঠডঢণতথদধনপফবভমযরলশসহ",
    "Gujarati": "કખગઘચછજઝટઠડઢણતથદધનપફબભમયરલવ",
    "Kannada": "ಕಖಗಘಚಛಜಝಟಠಡಢಣತಥದಧನಪಫಬಭಮಯರಲವ",
    "Malayalam": "കഖഗഘചഛജഝടഠഡഢണതഥദധനപഫബഭമയരലവ",
    "Gurmukhi": "ਕਖਗਘਚਛਜਝਟਠਡਢਣਤਥਦਧਨਪਫਬਭਮਯਰਲਵ",
    "Odia": "କଖଗଘଚଛଜଝଟଠଡଢଣତଥଦଧନପଫବଭମଯରଲ",
    "Arabic": "ابتثجحخدذرزسشصضطظعغفقكلمنهوي",
    "Latin": "abcdefghijklmnopqrstuvwxyz",
}
TARGET_SCRIPT = {
    "hi": "Devanagari", "mr": "Devanagari", "ne": "Devanagari", "sa": "Devanagari", "mai": "Devanagari",
    "kok": "Devanagari", "doi": "Devanagari", "brx": "Devanagari", "sd": "Devanagari", "ks-Deva": "Devanagari",
    "ta": "Tamil", "te": "Telugu", "bn": "Bengali", "as": "Bengali", "mni-Beng": "Bengali", "gu": "Gujarati",
    "kn": "Kannada", "ml": "Malayalam", "pa": "Gurmukhi", "or": "Odia", "ur": "Arabic", "ks": "Arabic",
    "sd-Arab": "Arabic", "en": "Latin", "sat": "Devanagari", "mni": "Bengali",
}

_FREQ_BASE = 200.0
_FREQ_STEP = 20.0


def fake_translate(text: str, target: str, script: str = "native") -> str:
    letters = SCRIPT_LETTERS["Latin" if script == "roman" else TARGET_SCRIPT.get(target, "Devanagari")]
    out = []
    for ch in text:
        if ch.isascii() and not ch.isalpha():
            out.append(ch)
        elif ch.isalpha():
            out.append(letters[ord(ch) % len(letters)])
        elif ch.isdigit():
            out.append(ch)
        elif ch in "⟦⟧" or not ch.isprintable() or ch.isspace():
            out.append(ch)
        else:
            # Vowel signs and other marks would belong to the source script; drop them.
            if unicodedata.category(ch).startswith("M"):
                continue
            out.append(ch)
    return "".join(out)


def tone(freq: float, seconds: float, sr: int, amplitude: float = 0.3) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    return (np.sin(2 * np.pi * freq * t) * amplitude * 32767).astype(np.int16)


def wav_bytes(pcm: np.ndarray, sr: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.astype(np.int16).tobytes())
    return buf.getvalue()


def mp3_bytes(pcm: np.ndarray, sr: int) -> bytes:
    proc = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-f", "s16le", "-ar", str(sr), "-ac", "1", "-i", "-",
         "-codec:a", "libmp3lame", "-b:a", "64k", "-f", "mp3", "-"],
        input=pcm.astype(np.int16).tobytes(), capture_output=True, check=True,
    )
    return proc.stdout


def write_tone_wav(path: Path, pattern: list[tuple[float, float]], sr: int = 16_000, freq: float = 440.0,
                   lead_silence_s: float = 0.0) -> Path:
    """Write a WAV of alternating (tone_seconds, silence_seconds) pairs."""
    parts = [np.zeros(int(lead_silence_s * sr), dtype=np.int16)]
    for on, off in pattern:
        parts.append(tone(freq, on, sr))
        parts.append(np.zeros(int(off * sr), dtype=np.int16))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(wav_bytes(np.concatenate(parts), sr))
    return path


def _dominant_freq(pcm: np.ndarray, sr: int) -> float:
    if len(pcm) < 256:
        return 0.0
    spectrum = np.abs(np.fft.rfft(pcm.astype(np.float32)))
    freqs = np.fft.rfftfreq(len(pcm), 1 / sr)
    return float(freqs[int(np.argmax(spectrum))])


def _read_wav(data: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(data), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16), w.getframerate()


def _multipart_field(body: bytes, name: str) -> bytes | None:
    m = re.search(rb'name="' + name.encode() + rb'"(?:; filename="[^"]*")?\r\n(?:[^\r\n]+\r\n)*\r\n(.*?)\r\n--',
                  body, re.S)
    return m.group(1) if m else None


@dataclass
class FakeBodhan:
    auto_language: str = "hi"
    truncate_over: int | None = None
    ocr_min_tokens: int | None = None
    speak_seconds_per_char: float = 0.06
    speak_format: str = "mp3"
    bad_speech_inputs: set[str] = field(default_factory=set)
    fail_plan: dict[str, list[int]] = field(default_factory=dict)
    retry_after: str | None = "0"
    transcribe_fn: Callable[[bytes, str | None], str] | None = None
    ocr_blocks_fn: Callable[[bytes, dict[str, Any]], list[dict[str, Any]]] | None = None
    calls: Counter = field(default_factory=Counter)
    requests: list[tuple[str, Any]] = field(default_factory=list)
    _spoken: list[str] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls[path] += 1
        if not request.headers.get("authorization", "").startswith("Bearer "):
            return self._error(401, "auth_error", "LiteLLM Virtual Key expected.")
        plan = self.fail_plan.get(path)
        if plan:
            status = plan.pop(0)
            headers = {"retry-after": self.retry_after} if self.retry_after and status == 429 else {}
            return httpx.Response(status, headers=headers,
                                  json={"error": {"message": "planned failure", "code": "internal_error"}})
        handler = {
            "/v1/audio/transcriptions": self._transcribe,
            "/v1/audio/speech": self._speak,
            "/v1/chat/completions": self._chat,
        }.get(path)
        if handler is None:
            return httpx.Response(404, json={"detail": "Not Found"})
        return handler(request)

    @staticmethod
    def _error(status: int, kind: str, message: str) -> httpx.Response:
        # The live gateway answers with LiteLLM-style envelopes: the status sits in `code`.
        return httpx.Response(status, json={"error": {"message": message, "type": kind, "param": None,
                                                      "code": str(status)}})

    # -- endpoints -----------------------------------------------------------

    def _transcribe(self, request: httpx.Request) -> httpx.Response:
        body = request.read()
        audio = _multipart_field(body, "file")
        if audio is None:
            return httpx.Response(422, json={"detail": [{"loc": ["body", "file"], "msg": "Field required"}]})
        lang_raw = _multipart_field(body, "language")
        lang = lang_raw.decode() if lang_raw else None
        self.requests.append(("transcribe", {"language": lang, "bytes": len(audio)}))
        if self.transcribe_fn is not None:
            return httpx.Response(200, json={"text": self.transcribe_fn(audio, lang)})
        try:
            pcm, sr = _read_wav(audio)
        except (wave.Error, EOFError):
            return httpx.Response(415, json={"detail": "could not measure audio duration"})
        if len(pcm) / sr > 30.0:
            return self._error(400, "invalid_request_error", "audio longer than 30 seconds")
        idx = round((_dominant_freq(pcm, sr) - _FREQ_BASE) / _FREQ_STEP)
        if 0 <= idx < len(self._spoken):
            text = self._spoken[idx]
        else:
            text = PHRASES.get(lang or self.auto_language, PHRASES["en"])
        return httpx.Response(200, json={"text": text}, headers={"x-request-id": f"t{self.calls.total()}"})

    def _chat(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        if body.get("model") == "indic-translate":
            return self._translate(body)
        if body.get("model") == "indic-ocr":
            return self._ocr(body)
        return self._error(400, "invalid_request_error", f"unknown model {body.get('model')!r}")

    def _translate(self, body: dict[str, Any]) -> httpx.Response:
        msgs = body.get("messages") or []
        if len(msgs) != 1 or msgs[0].get("role") != "user":
            return self._error(400, "invalid_request_error", "exactly one user message is required")
        text = msgs[0].get("content")
        target = body.get("target_language")
        script = body.get("script", "native")
        self.requests.append(("translate", {"text": text, "target_language": target, "script": script}))
        if not isinstance(text, str) or not text:
            return self._error(400, "invalid_request_error", "text is required")
        if target not in TARGET_SCRIPT:
            return self._error(400, "invalid_request_error",
                               f"unsupported language {target!r}; supported: " + ", ".join(sorted(TARGET_SCRIPT)))
        truncated = self.truncate_over is not None and len(text) > self.truncate_over
        source = text[: len(text) // 2] if truncated else text
        translation = fake_translate(source, target, script)
        return httpx.Response(200, json={
            "id": f"chatcmpl-{self.calls.total()}", "object": "chat.completion", "created": 0,
            "model": "indic-translate",
            "choices": [{"index": 0, "finish_reason": "length" if truncated else "stop",
                         "message": {"role": "assistant", "content": translation}}],
            "usage": {"prompt_tokens": len(text) // 2, "completion_tokens": len(translation) // 2 + 1,
                      "total_tokens": len(text) + 1},
        }, headers={"x-request-id": f"tr{self.calls.total()}"})

    def _speak(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        self.requests.append(("speak", body))
        text = body.get("input", "")
        instructions = json.loads(body.get("instructions", "{}"))
        if not text:
            return self._error(500, "internal_server_error", "empty input")
        if "lang" not in instructions or not body.get("voice"):
            return httpx.Response(422, json={"detail": "voice and instructions.lang are required"})
        if text in self.bad_speech_inputs:
            freq = 7000.0  # decodes to nothing known: the back-transcript won't match
        else:
            self._spoken.append(text)
            freq = _FREQ_BASE + _FREQ_STEP * (len(self._spoken) - 1)
        seconds = max(0.3, len(text) * self.speak_seconds_per_char)
        pcm = np.concatenate([np.zeros(int(0.1 * TTS_RATE), dtype=np.int16), tone(freq, seconds, TTS_RATE),
                              np.zeros(int(0.1 * TTS_RATE), dtype=np.int16)])
        if self.speak_format == "wav":
            return httpx.Response(200, content=wav_bytes(pcm, TTS_RATE), headers={"content-type": "audio/wav"})
        return httpx.Response(200, content=mp3_bytes(pcm, TTS_RATE), headers={"content-type": "audio/mpeg"})

    def _ocr(self, body: dict[str, Any]) -> httpx.Response:
        self.requests.append(("ocr", {"max_tokens": body.get("max_tokens"), "table_format": body.get("table_format")}))
        msgs = body.get("messages") or []
        if len(msgs) != 1:
            return self._error(400, "invalid_prompt", "exactly one user message")
        url = msgs[0]["content"][0]["image_url"]["url"]
        if not url.startswith(("data:image/png;base64,", "data:image/jpeg;base64,")):
            return httpx.Response(502, json={"detail": "image could not be decoded"})
        if self.ocr_min_tokens and int(body.get("max_tokens", 2048)) < self.ocr_min_tokens:
            return httpx.Response(502, json={"detail": "block exceeded max_tokens"})
        page_no = self.calls["/v1/chat/completions"]
        blocks = self.ocr_blocks_fn(url.encode(), body) if self.ocr_blocks_fn else default_blocks(page_no)
        content = "\n\n".join(b["text"] for b in blocks if b["text"])
        return httpx.Response(200, json={
            "id": f"indic-ocr-{page_no}", "created": 0, "model": "indic-ocr", "object": "chat.completion",
            "choices": [{"finish_reason": "stop", "index": 0, "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            "blocks": blocks,
        })


def default_blocks(page: int) -> list[dict[str, Any]]:
    def block(order: int, label: str, kind: str, text: str) -> dict[str, Any]:
        return {"order": order, "label": label, "type": kind, "bbox_xyxy": [0, 0, 10, 10], "conf": 0.9, "text": text}

    return [
        block(0, "Header", "Text", "Running header"),
        block(1, "Chapter-title", "Title", f"Chapter {page}"),
        block(2, "Paragraph", "Text", "The river flows 42 km to the sea."),
        block(3, "Table", "Table", "| Item | Qty |\n| --- | --- |\n| Rice | 2 |"),
        block(4, "Image", "Picture", ""),
        block(5, "Page-number", "Text", str(page)),
    ]


def make_fake_transport(**opts: Any) -> httpx.MockTransport:
    fake = FakeBodhan(**opts)
    transport = httpx.MockTransport(fake)
    transport.fake = fake  # type: ignore[attr-defined]
    return transport


def fake_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "base_url": "https://fake.bodhan.test",
        "keys": {k: "test-key" for k in ("transcribe", "translate", "speak", "ocr")},
        "workdir": tmp_path / "work",
        "cache_dir": tmp_path / "cache",
        "max_retries": 3,
        "call_timeout_s": 10.0,
    }
    values.update(overrides)
    return Settings(**values)


def fake_runtime(tmp_path: Path, settings: dict[str, Any] | None = None, **opts: Any) -> tuple[Runtime, FakeBodhan]:
    transport = make_fake_transport(**opts)
    runtime = Runtime(fake_settings(tmp_path, **(settings or {})), transport=transport)

    async def no_sleep(_seconds: float) -> None:
        return None

    runtime.client.sleep = no_sleep
    return runtime, transport.fake  # type: ignore[attr-defined]
