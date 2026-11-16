"""An in-process fake of the four Bodhan endpoints, used through ``httpx.MockTransport``.

It enforces the real limits so tests prove the tools respect them:
  * transcription rejects audio longer than 30 s (400)
  * speech rejects inputs longer than 600 characters (422) and unknown voices / langs
  * translation rejects payloads over 5,000 characters (413) and unsupported targets (400)
  * OCR accepts exactly one image_url part

Speech is faked as tones: each synthesised text gets a frequency derived from its hash and a
duration proportional to its length. Fake transcription splits audio on silence, measures each
tone's frequency and looks the text up, so back-transcription round-trips like the real thing.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import wave
from collections import Counter
from dataclasses import dataclass, field

import httpx
import numpy as np

VALID_ASR = {"as", "bhb", "bho", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "mai", "ml", "mni", "mr", "ne", "or",
             "pa", "sa", "sat", "sd", "ta", "te", "ur"}
VALID_MT = VALID_ASR - {"bhb", "bho"} | {"ks-Deva", "mni-Beng", "sd-Arab"}
VOICES = {"Prastuti", "Ankur", "Ishita", "Sourav", "Gwrbw", "Sansuma", "Preeti", "Sham", "Dhara", "Parth", "Kavya", "Suhani", "Amit",
          "Deepika", "Adarsh", "Anjali", "Sandeep", "Zoon", "Ishfaq", "Vaidehi", "Madhukar", "Lakshmi", "Kiran", "Thoibi", "Chaoba",
          "Anagha", "Chinmay", "Srijana", "Sagar", "Itishree", "Akash", "Kaur", "Manpreet", "Bharati", "Aryaman", "Phulmani", "Sibu",
          "Moomal", "Rano", "Anitha", "Arun", "Sravani", "Vamsi", "Saba", "Zaid"}
SCRIPT_BASE = {"hi": 0x0905, "mr": 0x0905, "ne": 0x0905, "ta": 0x0B85, "bn": 0x0985, "as": 0x0985, "te": 0x0C05, "kn": 0x0C85,
               "ml": 0x0D05, "gu": 0x0A85, "pa": 0x0A05, "or": 0x0B05}


def _tone_wav(freqs_durs: list[tuple[float, float]], sr: int = 24000, gap_s: float = 0.6) -> bytes:
    parts = []
    for i, (f, dur) in enumerate(freqs_durs):
        if i:
            parts.append(np.zeros(int(sr * gap_s), dtype=np.float32))
        n = int(sr * dur)
        parts.append(0.5 * np.sin(2 * np.pi * f * np.arange(n) / sr).astype(np.float32))
    x = np.concatenate(parts) if parts else np.zeros(sr, dtype=np.float32)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes((x * 32767).astype("<i2").tobytes())
    return buf.getvalue()


def fake_translate(text: str, target: str) -> str:
    if target == "en":
        return "".join(chr(ord("a") + ord(c) % 26) if (not c.isascii() and c.isalpha()) else c for c in text)
    base = SCRIPT_BASE.get(target, 0x0905)
    return "".join(chr(base + ord(c) % 6) if c.isascii() and c.isalpha() else c for c in text)


@dataclass
class FakeBodhan:
    tone_texts: dict[float, str] = field(default_factory=dict)  # frequency -> text
    text_tones: dict[str, float] = field(default_factory=dict)  # text -> frequency
    calls: Counter = field(default_factory=Counter)
    requests: list[tuple[str, str]] = field(default_factory=list)  # (endpoint, fingerprint)
    fail_next: list[int] = field(default_factory=list)  # statuses to return before succeeding
    repeat_texts: set[str] = field(default_factory=set)  # TTS "repeats" these (to test verification)
    truncate_over: int = 10_000
    ocr_pages: dict[int, str] = field(default_factory=dict)  # image height -> markdown
    ocr_default: str = "Chapter 1\n\nThe river flows to the sea.\n\n| Name | Marks |\n|---|---|\n| Asha | 10 |"
    asr_language_text: dict[str, str] = field(default_factory=dict)

    def register(self, text: str) -> float:
        """Give ``text`` its own tone frequency, at least 25 Hz from every other text's."""
        if text in self.text_tones:
            return self.text_tones[text]
        f = 200.0 + (int(hashlib.sha256(text.encode()).hexdigest(), 16) % 3000)
        while any(abs(f - g) < 25 for g in self.tone_texts):
            f = 200.0 + ((f - 200.0 + 37.0) % 3300)
        self.tone_texts[f] = text
        self.text_tones[text] = f
        return f

    def make_wav(self, texts: list[str], sr: int = 24000, gap_s: float = 0.6, secs_per_char: float = 0.06) -> bytes:
        """Fake 'speech': one tone per text, separated by silence, ~0.06 s per character."""
        return _tone_wav([(self.register(t), max(0.4, len(t) * secs_per_char)) for t in texts], sr, gap_s)

    # -- helpers ---------------------------------------------------------------------------
    def _decode(self, data: bytes) -> list[str]:
        with wave.open(io.BytesIO(data), "rb") as wf:
            sr = wf.getframerate()
            x = np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2").astype(np.float32) / 32768
        frame = int(sr * 0.02)
        n = len(x) // frame
        if n == 0:
            return []
        rms = np.sqrt((x[: n * frame].reshape(n, frame) ** 2).mean(axis=1))
        voiced = rms > 0.05
        texts, i = [], 0
        while i < n:
            if not voiced[i]:
                i += 1
                continue
            j = i
            while j < n and voiced[j]:
                j += 1
            seg = x[i * frame: j * frame]
            if len(seg) > frame * 3:
                nfft = 1 << (len(seg) * 8 - 1).bit_length()  # zero-pad for sub-bin frequency accuracy
                spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), n=nfft))
                f = np.fft.rfftfreq(nfft, 1 / sr)[int(np.argmax(spec))]
                best = min(self.tone_texts, key=lambda k: abs(k - f), default=None)
                if best is not None and abs(best - f) < 8:
                    if not texts or texts[-1] != self.tone_texts[best]:
                        texts.append(self.tone_texts[best])
            i = j
        return texts

    @staticmethod
    def _err(status: int, code: str, message: str) -> httpx.Response:
        return httpx.Response(status, json={"error": {"code": code, "message": message, "request_id": "req_fake"}})

    # -- transport ---------------------------------------------------------------------------
    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if not request.headers.get("authorization", "").startswith("Bearer "):
            return self._err(401, "invalid_api_key_error", "missing key")
        if self.fail_next:
            status = self.fail_next.pop(0)
            self.calls["failed"] += 1
            return self._err(status, "rate_limit_exceeded_error" if status == 429 else "internal_error", "injected failure")
        body = request.content
        self.requests.append((path, hashlib.sha256(body).hexdigest()))
        if path == "/v1/audio/transcriptions":
            return self._asr(request)
        if path == "/translate":
            return self._mt(json.loads(body))
        if path == "/v1/audio/speech":
            return self._tts(json.loads(body))
        if path == "/v1/chat/completions":
            return self._ocr(json.loads(body))
        return self._err(404, "not_found", path)

    def _asr(self, request: httpx.Request) -> httpx.Response:
        self.calls["transcribe"] += 1
        ctype = request.headers["content-type"]
        boundary = ctype.split("boundary=")[1].encode()
        fields, audio = {}, b""
        for part in request.content.split(b"--" + boundary):
            if b"\r\n\r\n" not in part:
                continue
            head, _, value = part.partition(b"\r\n\r\n")
            if value.endswith(b"\r\n"):
                value = value[:-2]
            name = head.split(b'name="')[1].split(b'"')[0].decode()
            if name == "file":
                audio = value
            else:
                fields[name] = value.decode()
        if fields.get("model") != "indic-transcribe":
            return self._err(400, "invalid_request_error", "model")
        lang = fields.get("language")
        if lang and lang not in VALID_ASR:
            return self._err(400, "unsupported_language", lang)
        with wave.open(io.BytesIO(audio), "rb") as wf:
            secs = wf.getnframes() / wf.getframerate()
        if secs > 30.0:
            return self._err(400, "invalid_request_error", f"audio is {secs:.1f}s; max 30s")
        texts = self._decode(audio)
        return httpx.Response(200, json={"text": " ".join(texts)})

    def _mt(self, body: dict) -> httpx.Response:
        self.calls["translate"] += 1
        text, target = body.get("text", ""), body.get("target_language")
        if target not in VALID_MT:
            return self._err(400, "unsupported_language", f"unsupported target_language {target}")
        if len(text) > 5000:
            return self._err(413, "payload_too_large", "input above the documented limit")
        out = fake_translate(text, target)
        truncated = len(text) > self.truncate_over
        if truncated:
            out = out[: len(out) // 2]
        return httpx.Response(200, json={"translation": out, "target_language": target, "script": body.get("script", "native"),
                                         "truncated": truncated, "usage": {"prompt_tokens": len(text) // 2,
                                                                           "completion_tokens": max(1, len(out) // 2),
                                                                           "total_tokens": len(text)}})

    def _tts(self, body: dict) -> httpx.Response:
        self.calls["speak"] += 1
        text, voice = body.get("input", ""), body.get("voice")
        try:
            instr = json.loads(body.get("instructions", ""))
        except json.JSONDecodeError:
            return httpx.Response(422, json={"detail": "instructions must be a JSON string"})
        if not text or voice not in VOICES or "lang" not in instr:
            return httpx.Response(422, json={"detail": "bad input/voice/lang"})
        if len(text) > 600:
            return httpx.Response(422, json={"detail": "input too long for one request"})
        spoken = f"{text} {text}" if text in self.repeat_texts else text  # simulate Indic-Speak repeating content
        return httpx.Response(200, content=self.make_wav([spoken]), headers={"content-type": "audio/wav"})

    def _ocr(self, body: dict) -> httpx.Response:
        self.calls["ocr"] += 1
        if body.get("model") != "indic-ocr":
            return self._err(400, "invalid_request_error", "model")
        parts = body["messages"][0]["content"]
        if len(parts) != 1 or parts[0]["type"] != "image_url":
            return self._err(400, "invalid_request_error", "exactly one image_url part")
        url = parts[0]["image_url"]["url"]
        data = base64.b64decode(url.split(",", 1)[1])
        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            h = im.size[1]
        md = self.ocr_pages.get(h, self.ocr_default)
        blocks = [{"order": 0, "label": "Paragraph", "type": "Text", "bbox_xyxy": [0, 0, 10, 10], "conf": 0.9, "text": md[:40]}]
        if "|" in md:
            blocks.append({"order": 1, "label": "Table", "type": "Table", "bbox_xyxy": [0, 10, 10, 20], "conf": 0.9, "text": ""})
        return httpx.Response(200, json={"id": "x", "object": "chat.completion", "model": "indic-ocr",
                                         "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": md}}],
                                         "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}, "blocks": blocks})

    def duplicate_requests(self) -> list[tuple[str, str]]:
        c = Counter(self.requests)
        return [k for k, v in c.items() if v > 1]
