"""Back-transcribe a speech candidate with Indic-Transcribe and account for input audio."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from tempfile import NamedTemporaryFile

import httpx

from ..config import BackendMode
from ..inference import Inference, ProviderError, post_json, release_cuda_cache
from ..models import SpeechCandidate, Usage

MODEL = "indic-transcribe"
LOCAL_MODEL = "bodhan-ai/indic-transcribe-core"
TRANSCRIBE_URL = "https://api.bodhan.ai/v1/audio/transcriptions"
MAX_HOSTED_SECONDS = 30
RATE_PER_AUDIO_MINUTE = 0.10


@dataclass(frozen=True)
class Transcription:
    text: str | None
    attempt_ids: tuple[str, ...] = ()
    reason: str | None = None
    error_category: str | None = None


class LocalTranscribe:
    def __init__(self) -> None:
        self.engine = None

    def __call__(self, audio: bytes, language: str) -> str:
        if self.engine is None:
            try:
                from bodhan_genai.asr import IndicASREngine
            except ImportError:
                raise ProviderError("local_setup", "Install the optional Bodhan ASR runtime in README.md.") from None
            self.engine = IndicASREngine(LOCAL_MODEL, device="cuda")
        with NamedTemporaryFile(suffix=".wav") as clip:
            clip.write(audio)
            clip.flush()
            (text,) = self.engine.transcribe_batch([clip.name], lang=language)
        return text

    def close(self) -> None:
        if self.engine is not None:
            self.release_after_oom()

    def release_after_oom(self) -> None:
        self.engine = None
        release_cuda_cache()


class IndicTranscribeProvider:
    def __init__(
        self, mode: BackendMode, inference: Inference, client: httpx.Client,
        env: Mapping[str, str], local: Callable[[bytes, str], str], release_local: Callable[[], None],
    ) -> None:
        self.mode = mode
        self.inference = inference
        self.client = client
        self.env = env
        self.local = local
        self.release_local = release_local

    def _checked_text(self, text: object) -> str:
        if not isinstance(text, str):
            raise ProviderError("response", "Indic-Transcribe response is missing its text field.")
        secrets = (value for key, value in self.env.items() if value and (
            key == "LLM_API_KEY" or key.startswith("BODHAN_") and key.endswith("_KEY")
        ))
        if any(secret in text for secret in secrets):
            raise ProviderError("response", "Indic-Transcribe returned credential-like text.")
        return text

    def _hosted(self, audio: bytes, language: str, candidate_id: str, duration: float) -> str:
        key = self.env.get("BODHAN_TRANSCRIBE_KEY")
        if not key:
            raise ProviderError("configuration", "Set BODHAN_TRANSCRIBE_KEY for hosted Indic-Transcribe.")
        if duration > MAX_HOSTED_SECONDS:
            raise ProviderError("duration_limit", "The candidate exceeds Indic-Transcribe's 30-second hosted limit.")
        response = post_json(
            self.client, TRANSCRIBE_URL, key, {"model": MODEL, "language": language},
            files={"file": (f"{candidate_id}.wav", audio, "audio/wav")},
        )
        return self._checked_text(response.get("text"))

    def transcribe(self, candidate: SpeechCandidate, language: str, out: Path) -> Transcription:
        path, candidate_id, duration = candidate.audio_path, candidate.attempt_id, candidate.duration_seconds
        if (candidate.status != "ok" or candidate.format != "wav" or not path or not candidate_id
                or duration is None or not isfinite(duration) or duration <= 0):
            return Transcription(None, reason=candidate.reason or "The speech candidate is unavailable.")
        try:
            audio = (out / path).read_bytes()
        except OSError:
            return Transcription(None, reason="Could not read the speech candidate WAV.")
        if not audio:
            return Transcription(None, reason="The speech candidate WAV is empty.")

        minutes = duration / 60
        usage = Usage(minutes, "audio_minute", RATE_PER_AUDIO_MINUTE,
                      minutes * RATE_PER_AUDIO_MINUTE, "estimated")
        attempts = self.inference.attempts.attempts
        start = len(attempts)
        try:
            text = self.inference.run_indic(
                mode=self.mode, model=MODEL, entity_ids=(candidate.chunk_id, language, candidate_id),
                local=lambda: self._checked_text(self.local(audio, language)), release_local=self.release_local,
                hosted=lambda: self._hosted(audio, language, candidate_id, duration), hosted_usage=usage,
                local_model=LOCAL_MODEL,
            )
        except ProviderError as error:
            return Transcription(None, tuple(attempt.id for attempt in attempts[start:]), str(error), error.category)
        return Transcription(text, tuple(attempt.id for attempt in attempts[start:]))
