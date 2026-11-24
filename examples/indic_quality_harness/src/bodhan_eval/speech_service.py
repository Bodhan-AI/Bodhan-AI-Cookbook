"""TTS -> STT evaluation with the persistent speech cache.

Cache layout (unchanged from the original pipeline)::

    <cache_dir>/speech_cache.json   {record_id: {"cache_key", "transcribed_text", ...}}
    <cache_dir>/audio/<record_id>.wav

An entry is reused only when its ``cache_key`` matches *and* the WAV file
still exists. The key hashes every input that changes the audio or the
transcript, so changing the text, voice, language or a model setting
regenerates the entry.
"""

from __future__ import annotations

import json
from pathlib import Path

from bodhan_eval.audio_checks import (
    evaluate_audio_quality,
    evaluate_duration_vs_text,
    inspect_audio,
)
from bodhan_eval.cache import JsonCache, stable_hash
from bodhan_eval.cost import CostTracker
from bodhan_eval.lexicons import DEFAULT_VOICES
from bodhan_eval.ratelimit import RateLimiter
from bodhan_eval.speech_checks import (
    evaluate_speech,
    evaluate_speech_coverage,
    tokenize,
)

SPEAK_MODEL = "indic-speak"
TRANSCRIBE_MODEL = "indic-transcribe"
TRANSCRIBE_VAD = "true"


def default_voice(language: str) -> str:
    return DEFAULT_VOICES.get(language, "Amit")


def speech_cache_key(text: str, language: str, voice: str) -> str:
    return stable_hash({
        "model": SPEAK_MODEL,
        "transcription_model": TRANSCRIBE_MODEL,
        "text": text,
        "language": language,
        "voice": voice,
        # Exactly what BodhanSpeechClient.speak sends as `instructions`.
        "speak_instructions": json.dumps({"lang": language}),
        "transcribe_language": language,
        "transcribe_vad": TRANSCRIBE_VAD,
    })


class SpeechEvaluator:
    def __init__(
        self,
        client,
        cache_dir: str | Path,
        cost: CostTracker | None = None,
        voice: str | None = None,
        limiter: RateLimiter | None = None,
    ):
        self.client = client
        self.cache_dir = Path(cache_dir)
        self.cache = JsonCache(self.cache_dir / "speech_cache.json")
        self.cost = cost
        self.voice = voice
        self.limiter = limiter or RateLimiter("speech")

    def voice_for(self, language: str) -> str:
        return self.voice or default_voice(language)

    def synthesise_and_transcribe(self, record_id: str, text: str, language: str) -> dict:
        voice = self.voice_for(language)
        audio_path = self.cache_dir / "audio" / f"{record_id}.wav"
        key = speech_cache_key(text, language, voice)

        cached = self.cache.data.get(record_id)
        if (
            cached
            and cached.get("cache_key") == key
            and audio_path.exists()
            and cached.get("transcribed_text") is not None
        ):
            self.cache.hits += 1
            print(f"Speech cache hit: {record_id}")
            return {"audio_path": audio_path, "transcribed_text": cached["transcribed_text"],
                    "voice": voice, "cached": True}

        self.cache.misses += 1
        print(f"Generating speech: {record_id}")

        if self.cost:
            # Reserve both calls up front so we never pay for audio that we
            # cannot afford to transcribe.
            self.cost.reserve(
                "speech",
                self.cost.estimate_speak(text) + self.cost.estimate_transcribe(text),
            )

        audio_path.parent.mkdir(parents=True, exist_ok=True)
        self.limiter.call(
            self.client.speak, text=text, language=language, output_path=str(audio_path), voice=voice
        )
        if self.cost:
            self.cost.record(SPEAK_MODEL, self.cost.estimate_speak(text))

        transcribed_text = self.limiter.call(
            self.client.transcribe, audio_path=str(audio_path), language=language
        )
        if self.cost:
            try:
                seconds = inspect_audio(str(audio_path))["duration_seconds"]
                self.cost.record(TRANSCRIBE_MODEL, self.cost.transcribe_cost(seconds))
            except Exception:  # noqa: BLE001 - unreadable audio is reported later
                self.cost.record(TRANSCRIBE_MODEL, self.cost.estimate_transcribe(text))

        self.cache.set(record_id, {
            "cache_key": key,
            "transcribed_text": transcribed_text,
            "voice": voice,
            "language": language,
        })

        return {"audio_path": audio_path, "transcribed_text": transcribed_text,
                "voice": voice, "cached": False}

    def evaluate(self, record: dict) -> dict:
        """Return ``{"speech": {...}, "checks": [...]}`` for one record."""
        text = record["translation"]
        language = record["language"]

        produced = self.synthesise_and_transcribe(record["id"], text, language)
        transcribed_text = produced["transcribed_text"]
        audio_path = produced["audio_path"]

        comparison = evaluate_speech(
            intended_text=text,
            transcribed_text=transcribed_text,
            language=language,
        )
        coverage = evaluate_speech_coverage(text, transcribed_text, language)

        checks = [comparison, *coverage]

        try:
            audio_metrics = inspect_audio(str(audio_path))
            audio_quality = evaluate_audio_quality(audio_metrics)
            duration_check = evaluate_duration_vs_text(
                audio_metrics["duration_seconds"], len(tokenize(text))
            )
            if duration_check:
                audio_quality.append(duration_check)
        except (ValueError, EOFError, OSError) as exc:
            audio_metrics = {"error": str(exc)}
            audio_quality = [{
                "category": "audio_format", "status": "fail", "passed": False,
                "severity": "high", "evidence": {"error": str(exc)},
            }]

        checks.extend(audio_quality)

        return {
            "speech": {
                "audio_path": str(audio_path),
                "voice": produced["voice"],
                "cached": produced["cached"],
                "transcribed_text": transcribed_text,
                "comparison": comparison,
                "coverage": coverage,
                "audio_metrics": audio_metrics,
                "audio_checks": audio_quality,
            },
            "checks": checks,
        }
