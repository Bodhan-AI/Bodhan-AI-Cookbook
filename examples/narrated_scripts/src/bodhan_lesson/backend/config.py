"""Settings shared by the CLI and model adapters."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from dotenv import dotenv_values

BackendMode = Literal["local-first", "hosted"]

@dataclass(frozen=True)
class LanguageCapability:
    name: str
    voices: tuple[str, ...]
    speech_tier: Literal["production", "preview"] = "production"
    local_translation_target: str | None = None


# Base codes are shared by OCR, hosted translation and speech. Voices are the
# documented hosted catalog grouped by recording language. English can use any.
# https://console.bodhan.ai/api-docs/
# https://huggingface.co/bodhan-ai/indic-speak#languages-supported
LANGUAGES = {
    "as": LanguageCapability("Assamese", ("Prastuti", "Ankur")),
    "bn": LanguageCapability("Bengali", ("Ishita", "Sourav")),
    "brx": LanguageCapability("Bodo", ("Gwrbw", "Sansuma"), "preview"),
    "doi": LanguageCapability("Dogri", ("Preeti", "Sham"), "preview"),
    "en": LanguageCapability("English", ("Arun",)),
    "gu": LanguageCapability("Gujarati", ("Dhara", "Parth")),
    "hi": LanguageCapability("Hindi", ("Kavya", "Amit", "Suhani")),
    "kn": LanguageCapability("Kannada", ("Deepika", "Adarsh")),
    "kok": LanguageCapability("Konkani", ("Anjali", "Sandeep"), "preview"),
    "ks": LanguageCapability("Kashmiri", ("Zoon", "Ishfaq"), "preview", "Kashmiri (Perso-Arabic script)"),
    "mai": LanguageCapability("Maithili", ("Vaidehi", "Madhukar"), "preview"),
    "ml": LanguageCapability("Malayalam", ("Lakshmi", "Kiran")),
    "mni": LanguageCapability("Manipuri", ("Thoibi", "Chaoba"), "preview", "Manipuri (Meitei script)"),
    "mr": LanguageCapability("Marathi", ("Anagha", "Chinmay")),
    "ne": LanguageCapability("Nepali", ("Srijana", "Sagar"), "preview"),
    "or": LanguageCapability("Odia", ("Itishree", "Akash")),
    "pa": LanguageCapability("Punjabi", ("Kaur", "Manpreet")),
    "sa": LanguageCapability("Sanskrit", ("Bharati", "Aryaman"), "preview"),
    "sat": LanguageCapability("Santali", ("Phulmani", "Sibu"), "preview"),
    "sd": LanguageCapability("Sindhi", ("Moomal", "Rano"), "preview", "Sindhi (Devanagari script)"),
    "ta": LanguageCapability("Tamil", ("Anitha", "Arun")),
    "te": LanguageCapability("Telugu", ("Sravani", "Vamsi")),
    "ur": LanguageCapability("Urdu", ("Saba", "Zaid")),
}
LANGUAGE_NAMES = {code: capability.name for code, capability in LANGUAGES.items()}
DEFAULT_SPEECH_MAX_CHARS = 300
DEFAULT_SPEECH_CER_THRESHOLD = 0.15


def read_environment(env: Mapping[str, str]) -> dict[str, str]:
    """Read .env in the invocation directory, with exported values taking precedence."""
    file_values = dotenv_values(Path(".env"), interpolate=False)
    return {**{key: value for key, value in file_values.items() if value is not None}, **env}


def backend_mode(env: Mapping[str, str]) -> BackendMode:
    value = env.get("BODHAN_BACKEND") or "local-first"
    if value == "local-first" or value == "hosted":
        return value
    raise ValueError("BODHAN_BACKEND must be local-first or hosted.")


@dataclass(frozen=True)
class LessonRequest:
    input_path: Path
    target_languages: tuple[str, ...]
    out: Path
    source_lang: str | None
    until: str | None
    glossary: Path | None = None
    voices: Path | None = None
    force: bool = False


def speech_input_limit(env: Mapping[str, str]) -> int:
    configured = env.get("BODHAN_SPEECH_MAX_CHARS")
    if not configured:
        return DEFAULT_SPEECH_MAX_CHARS
    message = "BODHAN_SPEECH_MAX_CHARS must be a positive integer."
    try:
        limit = int(configured)
    except ValueError:
        raise ValueError(message) from None
    if limit < 1:
        raise ValueError(message)
    return limit


def load_voices(path: Path) -> dict[str, str]:
    """Read language-to-voice overrides. The speech stage validates names against its backend."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeError) as error:
        raise ValueError(f"Invalid voices YAML in {path}.") from error
    if data is None:
        return {}
    if not isinstance(data, dict) or not all(
        isinstance(code, str) and code in LANGUAGES and isinstance(voice, str) and voice.strip()
        for code, voice in data.items()
    ):
        raise ValueError("The voices file must map supported language codes to nonempty voice names.")
    return {code: voice.strip() for code, voice in data.items()}


def speech_cer_threshold(env: Mapping[str, str]) -> float:
    configured = env.get("BODHAN_SPEECH_CER_THRESHOLD")
    if not configured:
        return DEFAULT_SPEECH_CER_THRESHOLD
    message = "BODHAN_SPEECH_CER_THRESHOLD must be a number between 0 and 1."
    try:
        value = float(configured)
    except ValueError:
        raise ValueError(message) from None
    if not 0 <= value <= 1:
        raise ValueError(message)
    return value
