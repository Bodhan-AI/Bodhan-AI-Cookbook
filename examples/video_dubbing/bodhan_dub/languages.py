"""Language registry and voice library.

Mirrors ``scripts/bodhan_api_rules.json`` in the Cookbook, which is generated
from https://console.bodhan.ai/api-docs/. Keeping the table here means a bad
``--langs`` value fails immediately, before any paid call.

Only languages that have a voice in the Indic-Speak library can be dubbed, so
that is the set accepted by ``--langs``. English transcription is handled
separately.
"""

from __future__ import annotations

from dataclasses import dataclass

# ISO codes are plain (hi), never the region-tagged form.
LANGUAGE_NAMES: dict[str, str] = {
    "as": "Assamese",
    "bn": "Bengali",
    "brx": "Bodo",
    "doi": "Dogri",
    "en": "English",
    "gu": "Gujarati",
    "hi": "Hindi",
    "kn": "Kannada",
    "kok": "Konkani",
    "ks": "Kashmiri",
    "mai": "Maithili",
    "ml": "Malayalam",
    "mni": "Manipuri",
    "mr": "Marathi",
    "ne": "Nepali",
    "or": "Odia",
    "pa": "Punjabi",
    "sa": "Sanskrit",
    "sat": "Santali",
    "sd": "Sindhi",
    "ta": "Tamil",
    "te": "Telugu",
    "ur": "Urdu",
}

VOICES: dict[str, dict[str, list[str]]] = {
    "as": {"female": ["Prastuti"], "male": ["Ankur"]},
    "bn": {"female": ["Ishita"], "male": ["Sourav"]},
    "brx": {"female": ["Gwrbw"], "male": ["Sansuma"]},
    "doi": {"female": ["Preeti"], "male": ["Sham"]},
    "gu": {"female": ["Dhara"], "male": ["Parth"]},
    "hi": {"female": ["Kavya", "Suhani"], "male": ["Amit"]},
    "kn": {"female": ["Deepika"], "male": ["Adarsh"]},
    "kok": {"female": ["Anjali"], "male": ["Sandeep"]},
    "ks": {"female": ["Zoon"], "male": ["Ishfaq"]},
    "mai": {"female": ["Vaidehi"], "male": ["Madhukar"]},
    "ml": {"female": ["Lakshmi"], "male": ["Kiran"]},
    "mni": {"female": ["Thoibi"], "male": ["Chaoba"]},
    "mr": {"female": ["Anagha"], "male": ["Chinmay"]},
    "ne": {"female": ["Srijana"], "male": ["Sagar"]},
    "or": {"female": ["Itishree"], "male": ["Akash"]},
    "pa": {"female": ["Kaur"], "male": ["Manpreet"]},
    "sa": {"female": ["Bharati"], "male": ["Aryaman"]},
    "sat": {"female": ["Phulmani"], "male": ["Sibu"]},
    "sd": {"female": ["Moomal"], "male": ["Rano"]},
    "ta": {"female": ["Anitha"], "male": ["Arun"]},
    "te": {"female": ["Sravani"], "male": ["Vamsi"]},
    "ur": {"female": ["Saba"], "male": ["Zaid"]},
}

# Languages that can be dubbed today: those with at least one voice.
DUBBABLE_LANGS: tuple[str, ...] = tuple(sorted(VOICES))

SOURCE_LANG = "en"

DEFAULT_TTS_STYLE = "educational lecture"

TTS_STYLES: tuple[str, ...] = (
    "AIR style news",
    "Customer Care",
    "TV style news",
    "advertisements",
    "anger",
    "children's stories",
    "disgust",
    "educational lecture",
    "fear",
    "happy",
    "news",
    "sad",
    "single person narration audiobook",
    "surprise",
)


@dataclass(frozen=True)
class Language:
    code: str
    name: str
    voices: dict[str, list[str]]

    def voice(self, gender: str = "female", preferred: str | None = None) -> str:
        """Pick a voice, falling back to the other gender rather than failing."""
        if preferred:
            for names in self.voices.values():
                if preferred in names:
                    return preferred
        pool = self.voices.get(gender) or next(iter(self.voices.values()))
        return pool[0]


def get(code: str) -> Language:
    normalized = code.strip().lower().replace("_", "-")
    if normalized not in VOICES:
        raise KeyError(code)
    return Language(normalized, LANGUAGE_NAMES[normalized], VOICES[normalized])


def is_dubbable(code: str) -> bool:
    return code.strip().lower() in VOICES


def parse_langs(value: str) -> list[str]:
    """Parse and validate a ``--langs`` value.

    Raises ``ValueError`` with the supported set so the CLI can print it as a
    usage error instead of a traceback.
    """
    raw = [part.strip().lower() for part in value.split(",") if part.strip()]
    if not raw:
        raise ValueError("--langs is empty; pass at least one code, for example hi,ta,bn")
    seen: list[str] = []
    for code in raw:
        if code not in VOICES:
            supported = ", ".join(DUBBABLE_LANGS)
            raise ValueError(f"unsupported language {code!r}. Supported: {supported}")
        if code not in seen:
            seen.append(code)
    return seen


# Names Indic-Translate expects in its instruction prompt. The model is
# prompted with the language *name*, never the ISO code:
#     Translate the following text into Hindi:
PROMPT_NAMES: dict[str, str] = {
    "as": "Assamese",
    "bn": "Bengali",
    "brx": "Bodo",
    "doi": "Dogri",
    "en": "English",
    "gu": "Gujarati",
    "hi": "Hindi",
    "kn": "Kannada",
    "kok": "Konkani",
    "ks": "Kashmiri (Perso-Arabic script)",
    "ks-Deva": "Kashmiri (Devanagari)",
    "mai": "Maithili",
    "ml": "Malayalam",
    "mni": "Manipuri (Meitei script)",
    "mni-Beng": "Manipuri (Bengali script)",
    "mr": "Marathi",
    "ne": "Nepali",
    "or": "Odia",
    "pa": "Punjabi",
    "sa": "Sanskrit",
    "sat": "Santali",
    "sd": "Sindhi (Devanagari script)",
    "sd-Arab": "Sindhi (Perso-Arabic script)",
    "ta": "Tamil",
    "te": "Telugu",
    "ur": "Urdu",
}


def prompt_name(code: str, script: str | None = None) -> str:
    """Target-language name for a translation instruction prompt."""
    base = PROMPT_NAMES.get(code) or LANGUAGE_NAMES.get(code) or code
    if script == "codemix":
        return f"{base} (code-mixed)"
    if script == "roman":
        return f"{base} (Roman)"
    return base


def name(code: str) -> str:
    return LANGUAGE_NAMES.get(code, code)


# Sentence terminators, so synthesized text gets a closing stop even when the
# transcription produced none (the ASR output is usually unpunctuated).
_DEVANAGARI_TERMINATOR = {"hi", "mr", "ne", "sa", "doi", "mai", "brx", "kok"}


def terminator(code: str) -> str:
    return "।" if code in _DEVANAGARI_TERMINATOR else "."
