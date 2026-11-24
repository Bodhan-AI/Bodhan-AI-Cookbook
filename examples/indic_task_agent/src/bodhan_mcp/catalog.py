"""What each Bodhan model supports and what it costs.

Support comes from the vendored copy of the Cookbook's bodhan_api_rules.json. All language checks go
through here, so an unsupported language is rejected the same way everywhere, with the list of what *is*
supported. Prices (INR) are the hosted prices from the assignment brief; indic-transliterate's price is
not published and is assumed to match indic-translate. Estimates are upper bounds, used before spending.
"""

from __future__ import annotations

import json
import math
from functools import cache
from importlib import resources

from .client import OCR, SPEAK, TRANSCRIBE, TRANSLATE, TRANSLITERATE, Code, ToolFailure


@cache
def rules() -> dict:
    text = resources.files("bodhan_mcp").joinpath("data/bodhan_api_rules.json").read_text(encoding="utf-8")
    return json.loads(text)


def language_names() -> dict[str, str]:
    return dict(rules()["language_names"])


def languages(model: str) -> list[str]:
    if model == OCR:
        return []  # script-agnostic
    return list(rules()["models"][model].get("languages", []))


def scripts(model: str) -> list[str]:
    return list(rules()["models"][model].get("scripts", []))


def speak_styles() -> list[str]:
    return list(rules()["models"][SPEAK]["styles"])


# Names for common languages Bodhan does *not* support, so refusals read naturally.
OTHER_LANGUAGES = {"fr": "French", "es": "Spanish", "de": "German", "it": "Italian", "pt": "Portuguese",
                   "ru": "Russian", "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "ar": "Arabic",
                   "fa": "Persian", "tr": "Turkish", "nl": "Dutch", "si": "Sinhala", "th": "Thai", "vi": "Vietnamese",
                   "id": "Indonesian", "sw": "Swahili", "pl": "Polish", "uk": "Ukrainian", "he": "Hebrew"}


def display_name(code: str) -> str:
    """'ta' -> 'Tamil', 'fr' -> 'French'; unknown codes are returned as given."""
    return language_names().get(code) or OTHER_LANGUAGES.get((code or "").lower()) or code


def describe(codes: list[str]) -> str:
    names = language_names()
    return ", ".join(f"{c} ({names.get(c, c)})" for c in codes)


def normalize_language(value: str | None) -> str | None:
    """Accept ISO codes ('hi'), regional tags (hi-IN) and English names ('Hindi')."""
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    names = language_names()
    known = set(names)
    if raw in known:
        return raw
    lowered = raw.lower()
    for code in known:
        if code.lower() == lowered:
            return code
    if lowered.endswith("-in") and lowered[:-3] in known:
        return lowered[:-3]
    for code, name in names.items():
        if name.lower() == lowered or name.lower().split(" (")[0] == lowered:
            return code
    for code, name in OTHER_LANGUAGES.items():
        if name.lower() == lowered:
            return code
    return lowered  # unknown code: the caller's support check reports it


def require_language(model: str, value: str | None, *, field: str = "language") -> str:
    code = normalize_language(value)
    if not code:
        raise ToolFailure(Code.INVALID_ARGUMENT, f"`{field}` is required.", f"Use one of: {describe(languages(model))}.")
    if code not in languages(model):
        raise ToolFailure(
            Code.UNSUPPORTED_LANGUAGE,
            f"{model} does not support {display_name(code)} ('{value}').",
            f"Supported by {model}: {describe(languages(model))}. Read the bodhan://languages resource for all models.",
        )
    return code


def voices(lang: str) -> dict[str, list[str]]:
    table = rules()["models"][SPEAK]["voices"]
    # There are no English-only voices; English is spoken with the Hindi voices (as the Cookbook does).
    return dict(table.get(lang) or (table["hi"] if lang == "en" else {}))


def pick_voice(lang: str, gender: str | None, voice: str | None) -> tuple[str, str]:
    """Return (voice, gender). Explicit voice wins, then gender, then the first female voice."""
    table = voices(lang)
    if not table:
        raise ToolFailure(Code.UNSUPPORTED_LANGUAGE, f"No voices for language '{lang}'.",
                          f"Speech is available for: {describe(languages(SPEAK))}.")
    if voice:
        for g, names in table.items():
            for name in names:
                if name.lower() == voice.strip().lower():
                    return name, g
        listing = "; ".join(f"{g}: {', '.join(n)}" for g, n in table.items())
        raise ToolFailure(Code.UNSUPPORTED_VOICE, f"Voice '{voice}' is not available for '{lang}'.",
                          f"Voices for {lang}: {listing}. Or pass gender instead of voice.")
    if gender:
        g = gender.strip().lower()
        if g not in ("female", "male"):
            raise ToolFailure(Code.INVALID_ARGUMENT, f"gender must be 'female' or 'male', not '{gender}'.")
        if table.get(g):
            return table[g][0], g
        raise ToolFailure(Code.UNSUPPORTED_VOICE, f"No {g} voice for '{lang}'.", f"Available: {table}.")
    g = "female" if table.get("female") else next(iter(table))
    return table[g][0], g


def check_style(style: str | None) -> str | None:
    if style is None or not style.strip():
        return None
    for s in speak_styles():
        if s.lower() == style.strip().lower():
            return s
    raise ToolFailure(Code.UNSUPPORTED_STYLE, f"Unknown speaking style '{style}'.",
                      f"Use one of: {', '.join(speak_styles())}, or omit style.")


def capability_summary() -> dict:
    """Machine-readable support table, served as the bodhan://languages resource."""
    names = language_names()
    out = {}
    for model in (TRANSCRIBE, TRANSLATE, TRANSLITERATE, SPEAK):
        out[model] = {c: names.get(c, c) for c in languages(model)}
    out[OCR] = "script-agnostic: any printed or handwritten page image"
    return out


TRANSCRIBE_PER_MINUTE = 0.10
TRANSLATE_PER_10K_OUTPUT_TOKENS = 0.20
TRANSLITERATE_PER_10K_OUTPUT_TOKENS = 0.20  # assumption, see module docstring
SPEAK_PER_10K_CHARS = 6.0
OCR_PER_IMAGE = 0.20

# Measured: Indic-Speak produces about 12 characters of text per second of audio.
SPEECH_CHARS_PER_SECOND = 12.0


def transcribe_cost(seconds: float) -> float:
    return max(0.0, seconds) / 60.0 * TRANSCRIBE_PER_MINUTE


def translate_cost(output_tokens: int) -> float:
    return max(0, output_tokens) / 10_000 * TRANSLATE_PER_10K_OUTPUT_TOKENS


def transliterate_cost(output_tokens: int) -> float:
    return max(0, output_tokens) / 10_000 * TRANSLITERATE_PER_10K_OUTPUT_TOKENS


def speak_cost(chars: int) -> float:
    return max(0, chars) / 10_000 * SPEAK_PER_10K_CHARS


def ocr_cost(pages: int) -> float:
    return max(0, pages) * OCR_PER_IMAGE


def translate_tokens_upper_bound(source_chars: int) -> int:
    # Indic output tokenises heavily (measured ~2.5 output tokens per input token); 1.5 tokens per
    # source character is a safe ceiling for any script.
    return math.ceil(source_chars * 1.5) + 50


def estimate(operation: str, *, chars: int = 0, seconds: float = 0.0, pages: int = 0,
             include_verification: bool = False) -> float:
    """Upper-bound cost in INR for one operation. `include_verification` adds the back-transcription
    the agent runs on generated speech."""
    if operation == "transcribe":
        return transcribe_cost(math.ceil(seconds))
    if operation in ("translate", "subtitles"):
        return translate_cost(translate_tokens_upper_bound(chars))
    if operation == "transliterate":
        return transliterate_cost(translate_tokens_upper_bound(chars))
    if operation == "speak":
        cost = speak_cost(chars)
        if include_verification:
            cost += transcribe_cost(math.ceil(chars / SPEECH_CHARS_PER_SECOND * 1.3))
        return cost
    if operation == "ocr":
        return ocr_cost(pages)
    raise ValueError(f"unknown operation {operation!r}")
