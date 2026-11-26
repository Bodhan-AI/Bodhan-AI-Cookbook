"""All runtime settings come from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional

from dotenv import dotenv_values

from app.bodhan.languages import (
    ALL_VOICES, ASR_LANGUAGES, TRANSLATE_LANGUAGES, TTS_LANGUAGES, TTS_STYLES, default_voice,
)


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class RateLimits:
    rpm: int
    concurrency: int
    max_retries: int
    # Per-model requests/minute; None = use `rpm`. Keys are per model and the speech key
    # was seen hitting 429 far below 50 rpm while publishing no limit headers.
    transcribe_rpm: Optional[int] = None
    translate_rpm: Optional[int] = None
    speak_rpm: Optional[int] = None

    def rpm_for(self, model: str) -> int:
        override = getattr(self, f"{model}_rpm", None)
        return override if override is not None else self.rpm


@dataclass(frozen=True)
class VadSettings:
    threshold: float
    end_silence_ms: int
    soft_max_s: float
    hard_max_s: float


ASR_BACKENDS = ("hosted", "local")
DEFAULT_LOCAL_ASR_MODEL = "models/indic-transcribe-core"


@dataclass(frozen=True)
class Settings:
    base_url: str
    transcribe_key: str    # "" when ASR_BACKEND=local and no key is set (hosted ASR is never called)
    translate_key: str
    speak_key: str
    limits: RateLimits
    shared_limiter: bool

    my_language: str       # what you speak and want subtitles in
    their_language: str    # what the other side speaks and should hear
    subtitle_script: str   # native | roman | codemix (ignored when my_language == en)
    tts_voice: str
    tts_style: Optional[str]

    ptt_key: str
    mic_device: Optional[str]
    cable_device: str
    monitor_tts: bool

    subtitle_deadline_s: float
    interpret_deadline_s: float
    caption_hold_s: float
    # A subtitle that starts within this pause of the previous one continues it: the joined
    # text is translated and replaces the previous caption. 0 turns merging off.
    subtitle_merge_pause_s: float

    vad: VadSettings

    # hosted = Bodhan's API (default). local = the same Indic-Transcribe model on this PC's GPU.
    asr_backend: str = "hosted"
    local_asr_model: str = DEFAULT_LOCAL_ASR_MODEL
    # Local ASR only: while someone talks, re-transcribe and translate the sentence so far this
    # often (seconds), shown as a provisional caption. 0 turns live captions off.
    live_caption_interval_s: float = 2.0
    # Save each meeting's final captions (JSONL) and export Markdown + SRT on exit.
    record_sessions: bool = True
    session_dir: str = "out/sessions"
    # Incoming dubbing (optional): speak their translated subtitles to you on dub_device, which must
    # not be the output subtitles listen to (or the app would transcribe its own voice).
    dub_incoming: bool = False
    dub_device: Optional[str] = None
    dub_voice: str = ""


def read_env(env_file: str = ".env", environ: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """Settings as the app sees them: the .env file, overridden by the process environment.

    Unlike load_dotenv this never writes to os.environ, so after the settings window rewrites
    .env, reading again picks up the new values. `environ` defaults to os.environ; pass a copy
    taken at startup to keep the variables your shell set (they still win over .env).
    """
    values = {k: v for k, v in dotenv_values(env_file).items() if v is not None}
    values.update(os.environ if environ is None else environ)
    return values


class _Env:
    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = values

    def get(self, name: str, default: Optional[str] = None) -> Optional[str]:
        value = self._values.get(name)
        return value.strip() if value and value.strip() else default

    def int(self, name: str, default: int) -> int:
        return int(self.get(name, str(default)))

    def opt_int(self, name: str) -> Optional[int]:
        value = self.get(name)
        return int(value) if value is not None else None

    def float(self, name: str, default: float) -> float:
        return float(self.get(name, str(default)))

    def bool(self, name: str, default: bool) -> bool:
        return self.get(name, str(default)).lower() in {"1", "true", "yes", "on"}


def load_vad_settings(env_file: str = ".env", environ: Optional[Mapping[str, str]] = None) -> VadSettings:
    """VAD settings alone (no API keys needed), e.g. for tools/vad_probe.py."""
    e = _Env(read_env(env_file, environ))
    hard_max = e.float("VAD_HARD_MAX_S", 25.0)
    if hard_max >= 30:
        raise ConfigError("VAD_HARD_MAX_S must stay below 30 s (Bodhan rejects longer audio).")
    return VadSettings(
        threshold=e.float("VAD_THRESHOLD", 0.5),
        end_silence_ms=e.int("VAD_END_SILENCE_MS", 600),
        soft_max_s=e.float("VAD_SOFT_MAX_S", 8.0),
        hard_max_s=hard_max,
    )


def load_settings(env_file: str = ".env", environ: Optional[Mapping[str, str]] = None) -> Settings:
    vad = load_vad_settings(env_file, environ)
    e = _Env(read_env(env_file, environ))
    asr_backend = e.get("ASR_BACKEND", "hosted").lower()
    if asr_backend not in ASR_BACKENDS:
        raise ConfigError(f"ASR_BACKEND must be {' or '.join(ASR_BACKENDS)}, not {asr_backend!r}.")
    fallback = e.get("BODHAN_API_KEY")
    keys = {
        "transcribe": e.get("BODHAN_TRANSCRIBE_KEY", fallback),
        "translate": e.get("BODHAN_TRANSLATE_KEY", fallback),
        "speak": e.get("BODHAN_SPEAK_KEY", fallback),
    }
    needed = [k for k in keys if not (k == "transcribe" and asr_backend == "local")]
    missing = [k for k in needed if not keys[k]]
    if missing:
        raise ConfigError(
            f"Missing Bodhan key(s) for: {', '.join(missing)}. Keys are per model; "
            "set BODHAN_<MODEL>_KEY in .env (create them at console.bodhan.ai/dashboard)."
        )

    mine = e.get("MY_LANGUAGE", "en")
    theirs = e.get("THEIR_LANGUAGE", "hi")
    for lang, role in ((mine, "MY_LANGUAGE"), (theirs, "THEIR_LANGUAGE")):
        if lang not in ASR_LANGUAGES or lang not in TRANSLATE_LANGUAGES:
            raise ConfigError(f"{role}={lang!r} is not supported by both transcription and translation.")
    if theirs not in TTS_LANGUAGES:
        raise ConfigError(f"THEIR_LANGUAGE={theirs!r} has no text-to-speech support.")

    voice = e.get("TTS_VOICE", default_voice(theirs))
    if voice not in ALL_VOICES:
        raise ConfigError(f"TTS_VOICE={voice!r} is not a Bodhan voice.")
    style = e.get("TTS_STYLE")
    if style and style not in TTS_STYLES:
        raise ConfigError(f"TTS_STYLE={style!r} is not a Bodhan style.")
    script = e.get("SUBTITLE_SCRIPT", "native")
    if script not in {"native", "roman", "codemix"}:
        raise ConfigError("SUBTITLE_SCRIPT must be native, roman or codemix.")
    dub_incoming = e.bool("DUB_INCOMING", False)
    if dub_incoming and mine not in TTS_LANGUAGES:
        raise ConfigError(f"MY_LANGUAGE={mine!r} has no text-to-speech, so their side can't be spoken "
                          "to you. Set DUB_INCOMING=false.")
    dub_voice = e.get("DUB_VOICE", default_voice(mine))
    if dub_voice not in ALL_VOICES:
        raise ConfigError(f"DUB_VOICE={dub_voice!r} is not a Bodhan voice.")

    return Settings(
        base_url=e.get("BODHAN_BASE_URL", "https://api.bodhan.ai"),
        transcribe_key=keys["transcribe"] or "",
        translate_key=keys["translate"],
        speak_key=keys["speak"],
        limits=RateLimits(
            rpm=e.int("BODHAN_RPM", 50),
            concurrency=e.int("BODHAN_CONCURRENCY", 2),
            max_retries=e.int("BODHAN_MAX_RETRIES", 6),
            transcribe_rpm=e.opt_int("BODHAN_TRANSCRIBE_RPM"),
            translate_rpm=e.opt_int("BODHAN_TRANSLATE_RPM"),
            speak_rpm=e.opt_int("BODHAN_SPEAK_RPM"),
        ),
        shared_limiter=e.bool("BODHAN_SHARED_LIMITER", False),
        my_language=mine,
        their_language=theirs,
        subtitle_script=script,
        tts_voice=voice,
        tts_style=style,
        ptt_key=e.get("PTT_KEY", "ctrl_r"),
        mic_device=e.get("MIC_DEVICE"),
        cable_device=e.get("CABLE_DEVICE", "CABLE Input"),
        monitor_tts=e.bool("MONITOR_TTS", False),
        subtitle_deadline_s=e.float("SUBTITLE_DEADLINE_S", 15.0),
        interpret_deadline_s=e.float("INTERPRET_DEADLINE_S", 20.0),
        caption_hold_s=e.float("CAPTION_HOLD_S", 8.0),
        subtitle_merge_pause_s=e.float("SUBTITLE_MERGE_PAUSE_S", 1.5),
        vad=vad,
        asr_backend=asr_backend,
        local_asr_model=e.get("LOCAL_ASR_MODEL", DEFAULT_LOCAL_ASR_MODEL),
        live_caption_interval_s=max(0.0, e.float("LIVE_CAPTION_INTERVAL_S", 2.0)),
        record_sessions=e.bool("SESSION_RECORD", True),
        session_dir=e.get("SESSION_DIR", "out/sessions"),
        dub_incoming=dub_incoming,
        dub_device=e.get("DUB_DEVICE"),
        dub_voice=dub_voice,
    )
