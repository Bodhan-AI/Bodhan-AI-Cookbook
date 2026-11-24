"""Indic-Speak adapters and the reusable script-to-candidate synthesis step."""

import io
import json
import struct
import wave
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

import httpx

from ..config import LANGUAGES, BackendMode
from ..inference import Inference, ProviderError, post_response, release_cuda_cache
from ..models import NarrationScript, ScriptChunk, SpeechCandidate, TargetSpeech, Usage

MODEL = "indic-speak"
LOCAL_MODEL = f"bodhan-ai/{MODEL}"
SPEAK_URL = "https://api.bodhan.ai/v1/audio/speech"
RATE_PER_INPUT_CHARACTER = 6 / 10_000

# The recipe selects voices recorded for the target language. English uses the
# shared library. Suhani is advertised by the hosted API only.
HOSTED_VOICES = {language: frozenset(entry.voices) for language, entry in LANGUAGES.items()}
HOSTED_VOICES["en"] = frozenset().union(*HOSTED_VOICES.values())
LOCAL_VOICES = {language: voices - {"Suhani"} for language, voices in HOSTED_VOICES.items()}


@dataclass(frozen=True)
class ModelAudio:
    content: bytes
    format: str
    duration_seconds: float


def _read_audio(content: bytes) -> ModelAudio:
    """Validate documented PCM WAV and calculate duration from actual audio frames."""
    try:
        with wave.open(io.BytesIO(content), "rb") as audio:
            frame_size = audio.getnchannels() * audio.getsampwidth()
            declared_frames = audio.getnframes()
            frames = audio.readframes(declared_frames)
            count, remainder = divmod(len(frames), frame_size)
            # A streamed RIFF can advertise an unknown data size of 0xFFFFFFFF.
            unknown_size = declared_frames == 0xFFFFFFFF // frame_size
            if not count or remainder or (count != declared_frames and not unknown_size):
                raise wave.Error("incomplete audio frames")
            return ModelAudio(content, "wav", count / audio.getframerate())
    except (wave.Error, EOFError, struct.error, ZeroDivisionError):
        raise ProviderError("response", "Indic-Speak returned invalid or empty PCM WAV audio.") from None


class LocalSpeak:
    def __init__(self) -> None:
        self.engine = None

    def __call__(self, text: str, voice: str) -> bytes:
        try:
            from bodhan_genai.tts import IndicTTSEngine
            from soundfile import write
        except ImportError:
            raise ProviderError("local_setup", "Install the optional Bodhan speech runtime in README.md.") from None
        if self.engine is None:
            # The in-process HF backend keeps CUDA errors visible to the router.
            self.engine = IndicTTSEngine(LOCAL_MODEL, backend="hf", device="cuda")
        result = self.engine.synthesize(text, speaker=voice)
        buffer = io.BytesIO()
        write(buffer, result.audio, result.sample_rate, format="WAV", subtype="PCM_16")
        return buffer.getvalue()

    def close(self) -> None:
        engine, self.engine = self.engine, None
        if engine is not None:
            engine.close()

    def release_after_oom(self) -> None:
        try:
            self.close()
        finally:
            release_cuda_cache()


class IndicSpeakProvider:
    def __init__(
        self, mode: BackendMode, inference: Inference, client: httpx.Client,
        env: Mapping[str, str], local: Callable[[str, str], bytes], release_local: Callable[[], None],
    ) -> None:
        self.mode = mode
        self.inference = inference
        self.client = client
        self.env = env
        self.local = local
        self.release_local = release_local

    def validate_voice(self, language: str, voice: str, *, hosted: bool = False) -> None:
        use_hosted = hosted or self.mode == "hosted"
        voices = HOSTED_VOICES if use_hosted else LOCAL_VOICES
        if voice not in voices.get(language, ()):
            backend = "hosted" if use_hosted else "local"
            raise ProviderError("configuration", f"Voice {voice!r} is unavailable for {language} on {backend} Indic-Speak.")

    def _hosted(self, text: str, language: str, voice: str) -> ModelAudio:
        self.validate_voice(language, voice, hosted=True)
        key = self.env.get("BODHAN_SPEAK_KEY")
        if not key:
            raise ProviderError("configuration", "Set BODHAN_SPEAK_KEY for hosted Indic-Speak.")
        response = post_response(self.client, SPEAK_URL, key, {
            "model": MODEL, "input": text, "voice": voice,
            "instructions": json.dumps({"lang": language}),
        })
        return _read_audio(response.content)

    def speak(self, chunk: ScriptChunk, language: str, voice: str) -> ModelAudio:
        self.validate_voice(language, voice)
        text = chunk.text
        usage = Usage(len(text), "input_character", RATE_PER_INPUT_CHARACTER,
                      len(text) * RATE_PER_INPUT_CHARACTER, "estimated")
        return self.inference.run_indic(
            mode=self.mode, model=MODEL, entity_ids=(chunk.id, language),
            local=lambda: _read_audio(self.local(text, voice)), release_local=self.release_local,
            hosted=lambda: self._hosted(text, language, voice), hosted_usage=usage, local_model=LOCAL_MODEL,
        )


def synthesize_chunk(
    chunk: ScriptChunk, language: str, voice: str, out: Path, provider: IndicSpeakProvider,
) -> SpeechCandidate:
    """Create a new candidate for original or repair text without replacing earlier audio."""
    attempts = provider.inference.attempts.attempts
    first_attempt = len(attempts)
    reason = None
    artifact = None
    audio = None
    try:
        audio = provider.speak(chunk, language, voice)
        directory = out / language / "work" / "audio"
        directory.mkdir(parents=True, exist_ok=True)
        # Run-scoped attempt IDs may repeat on later invocations in the same output folder.
        with NamedTemporaryFile(dir=directory, prefix=f"{chunk.id}-{attempts[-1].id}-",
                                suffix=f".{audio.format}", delete=False) as handle:
            handle.write(audio.content)
            artifact = (Path(language) / "work" / "audio" / Path(handle.name).name).as_posix()
    except ProviderError as error:
        reason = str(error)
    except OSError:
        artifact = None
        reason = "Could not write the speech candidate file."
    attempt_ids = tuple(attempt.id for attempt in attempts[first_attempt:])
    return SpeechCandidate(
        chunk.id, chunk.section_id, voice, len(chunk.text), "ok" if artifact else "failed",
        attempt_ids=attempt_ids, attempt_id=attempt_ids[-1] if attempt_ids else None,
        audio_path=artifact, format=audio.format if audio else None,
        duration_seconds=audio.duration_seconds if audio else None, reason=reason,
    )


def synthesize_script(
    script: NarrationScript, out: Path, provider: IndicSpeakProvider, *,
    synthesize: Callable[[ScriptChunk, str, str], SpeechCandidate] | None = None,
) -> TargetSpeech:
    """Validate the target voice before calls and synthesize at most one request at a time."""
    language = script.language
    voice = script.voice if script.voice is not None else LANGUAGES[language].voices[0]
    chunks = tuple(chunk for section in script.sections for chunk in section.chunks)
    try:
        provider.validate_voice(language, voice)
    except ProviderError as error:
        reason = str(error)
        return TargetSpeech(language, voice, "failed", tuple(
            SpeechCandidate(chunk.id, chunk.section_id, voice, len(chunk.text), "failed", reason=reason)
            for chunk in chunks
        ), reason)
    if not chunks:
        return TargetSpeech(language, voice, "failed", (), "The script has no eligible speech chunks.")

    if synthesize is None:
        def synthesize(chunk: ScriptChunk, language: str, voice: str) -> SpeechCandidate:
            return synthesize_chunk(chunk, language, voice, out, provider)
    candidates = tuple(synthesize(chunk, language, voice) for chunk in chunks)
    if all(candidate.status == "failed" for candidate in candidates):
        return TargetSpeech(language, voice, "failed", candidates, "No speech candidates were saved.")
    status = script.status
    if status == "completed" and any(candidate.status == "failed" for candidate in candidates):
        status = "completed_with_misses"
    return TargetSpeech(language, voice, status, candidates)
