"""Deterministic fake Bodhan clients for offline tests and demos without keys.

They go through the same cache + cost ledger as the real clients, so determinism and cost
accounting are exercised identically; only the network call is replaced by a deterministic
local computation. A `gold` map (translation -> English source) lets the fake back-translator
behave like a near-perfect checker for clean items and diverge for perturbed ones, which is
exactly what the round-trip check must cope with.
"""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

from ..cache import Cache, canonical_key
from ..cost import CostLedger, speak_cost_inr, transcribe_cost_inr, translate_cost_inr
from .base import BodhanClients, SpeakResult, TranscribeResult, TranslateResult


class FakeTranslate:
    def __init__(self, cache: Cache, ledger: CostLedger, gold: dict[str, str],
                 rate: float = 0.20, model: str = "indic-translate"):
        self.cache, self.ledger, self.gold, self.rate, self.model = cache, ledger, gold, rate, model

    async def translate(self, text: str, source_lang: str, target_lang: str) -> TranslateResult:
        params = {"model": self.model, "source": source_lang, "target": target_lang}
        key = canonical_key(self.model, "/fake/translate", params, text)
        cached = self.cache.get_json(key)
        if cached is not None:
            lc = translate_cost_inr(cached["output_tokens"], self.rate)
            return TranslateResult(cached["text"], cached["output_tokens"], 0.0, True, lc)
        if target_lang == "en":
            # back-translation: perfect for known (clean) translations, degraded otherwise
            out = self.gold.get(text)
            if out is None:
                # degrade: keep ASCII/numbers, drop the rest -> low chrF for perturbed items
                out = " ".join(w for w in text.split() if w.isascii()) or "untranslatable"
        else:
            out = f"[{target_lang}] {text}"
        tokens = max(1, len(out) // 3)
        cost = translate_cost_inr(tokens, self.rate)
        self.ledger.charge(self.model, cost)
        self.cache.put_json(key, {"text": out, "output_tokens": tokens})
        return TranslateResult(out, tokens, cost, False, cost)


class FakeSpeak:
    def __init__(self, cache: Cache, ledger: CostLedger, rate: float = 6.0,
                 model: str = "indic-speak", sample_rate: int = 24000):
        self.cache, self.ledger, self.rate, self.model, self.sr = cache, ledger, rate, model, sample_rate

    async def speak(self, text: str, lang: str, voice: str | None = None) -> SpeakResult:
        params = {"model": self.model, "voice": voice or lang, "lang": lang, "format": "wav"}
        key = canonical_key(self.model, "/fake/speak", params, text)
        cached = self.cache.get_bytes(key, ".wav")
        if cached is not None:
            lc = speak_cost_inr(len(text), self.rate)
            _SPOKEN[_wav_fingerprint(cached)] = text
            return SpeakResult(str(self.cache.path_for(key, ".wav")), len(text), 0.0, True,
                               cached, lc)
        # ~12 chars/sec of audio -> deterministic silent PCM WAV
        seconds = max(0.2, len(text) / 12.0)
        audio = _silent_wav(seconds, self.sr)
        cost = speak_cost_inr(len(text), self.rate)
        self.ledger.charge(self.model, cost)
        path = self.cache.put_bytes(key, audio, ".wav")
        # register spoken text so the fake transcriber can recover it
        _SPOKEN[_wav_fingerprint(audio)] = text
        return SpeakResult(str(path), len(text), cost, False, audio, cost)


class FakeTranscribe:
    def __init__(self, cache: Cache, ledger: CostLedger, rate: float = 0.10,
                 model: str = "indic-transcribe-core"):
        self.cache, self.ledger, self.rate, self.model = cache, ledger, rate, model

    async def transcribe(self, audio_path: str, lang: str) -> TranscribeResult:
        data = Path(audio_path).read_bytes()
        seconds = _wav_seconds(data)
        params = {"model": self.model, "lang": lang}
        key = canonical_key(self.model, "/fake/transcribe", params, data)
        cached = self.cache.get_json(key)
        if cached is not None:
            lc = transcribe_cost_inr(seconds, self.rate)
            return TranscribeResult(cached["text"], seconds, 0.0, True, lc)
        text = _SPOKEN.get(_wav_fingerprint(data), "")
        cost = transcribe_cost_inr(seconds, self.rate)
        self.ledger.charge(self.model, cost)
        self.cache.put_json(key, {"text": text})
        return TranscribeResult(text, seconds, cost, False, cost)


# in-process registry mapping synthesized audio -> spoken text (fake ASR ground truth)
_SPOKEN: dict[str, str] = {}


def _silent_wav(seconds: float, sr: int) -> bytes:
    n = int(seconds * sr)
    data = b"\x00\x00" * n  # 16-bit silence
    byte_rate = sr * 2
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, sr, byte_rate, 2, 16)
    header += b"data" + struct.pack("<I", len(data))
    return header + data


def _wav_seconds(data: bytes) -> float:
    if len(data) < 44:
        return 0.1
    sr = struct.unpack("<I", data[24:28])[0] or 24000
    nbytes = struct.unpack("<I", data[40:44])[0]
    return max(0.1, nbytes / (sr * 2))


def _wav_fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_fake_clients(cache: Cache, ledger: CostLedger, gold: dict[str, str] | None = None) -> BodhanClients:
    return BodhanClients(
        translate=FakeTranslate(cache, ledger, gold or {}),
        speak=FakeSpeak(cache, ledger),
        transcribe=FakeTranscribe(cache, ledger),
    )
