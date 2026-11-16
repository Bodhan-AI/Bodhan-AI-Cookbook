"""Language, voice and script tables for the four Bodhan models.

Source: https://console.bodhan.ai/api-docs/ and the Cookbook's scripts/bodhan_api_rules.json
(version 2026-09-21). Codes are plain ISO codes such as ``hi``, without a region suffix.
"""

from __future__ import annotations

from .errors import UNSUPPORTED_LANGUAGE, ToolFailure

LANGUAGE_NAMES: dict[str, str] = {
    "as": "Assamese", "bhb": "Bhili", "bho": "Bhojpuri", "bn": "Bengali", "brx": "Bodo",
    "doi": "Dogri", "en": "English", "gu": "Gujarati", "hi": "Hindi", "kn": "Kannada",
    "kok": "Konkani", "ks": "Kashmiri (Perso-Arabic)", "ks-Deva": "Kashmiri (Devanagari)",
    "mai": "Maithili", "ml": "Malayalam", "mni": "Manipuri (Meitei Mayek)",
    "mni-Beng": "Manipuri (Bengali script)", "mr": "Marathi", "ne": "Nepali", "or": "Odia",
    "pa": "Punjabi", "sa": "Sanskrit", "sat": "Santali", "sd": "Sindhi (Devanagari)",
    "sd-Arab": "Sindhi (Perso-Arabic)", "ta": "Tamil", "te": "Telugu", "ur": "Urdu",
}

TRANSCRIBE_LANGUAGES = frozenset({
    "as", "bhb", "bho", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "mai", "ml",
    "mni", "mr", "ne", "or", "pa", "sa", "sat", "sd", "ta", "te", "ur",
})
TRANSLATE_LANGUAGES = frozenset({
    "as", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "ks-Deva", "mai", "ml", "mni",
    "mni-Beng", "mr", "ne", "or", "pa", "sa", "sat", "sd", "sd-Arab", "ta", "te", "ur",
})
SPEAK_LANGUAGES = frozenset({
    "as", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "mai", "ml", "mni", "mr", "ne",
    "or", "pa", "sa", "sat", "sd", "ta", "te", "ur",
})

# Voices are grouped by the language they were recorded in; any voice can read any language.
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
# English has no dedicated voices; these Hindi-recorded voices read English well (Cookbook sample uses Amit).
ENGLISH_DEFAULT_VOICES = {"female": "Kavya", "male": "Amit"}

STYLES = (
    "AIR style news", "Customer Care", "TV style news", "advertisements", "anger", "children's stories",
    "disgust", "educational lecture", "fear", "happy", "news", "sad", "single person narration audiobook",
    "surprise",
)

# Unicode script of each language's native writing system (used for language-mismatch checks).
LANGUAGE_SCRIPT: dict[str, str] = {
    "as": "Bengali", "bhb": "Devanagari", "bho": "Devanagari", "bn": "Bengali", "brx": "Devanagari",
    "doi": "Devanagari", "en": "Latin", "gu": "Gujarati", "hi": "Devanagari", "kn": "Kannada",
    "kok": "Devanagari", "ks": "Arabic", "ks-Deva": "Devanagari", "mai": "Devanagari", "ml": "Malayalam",
    "mni": "Meetei Mayek", "mni-Beng": "Bengali", "mr": "Devanagari", "ne": "Devanagari", "or": "Oriya",
    "pa": "Gurmukhi", "sa": "Devanagari", "sat": "Ol Chiki", "sd": "Devanagari", "sd-Arab": "Arabic",
    "ta": "Tamil", "te": "Telugu", "ur": "Arabic",
}

# Names and common spellings a caller (or an LLM) may send instead of a code.
_ALIASES: dict[str, str] = {
    "assamese": "as", "asamiya": "as", "bhili": "bhb", "bhojpuri": "bho", "bengali": "bn", "bangla": "bn",
    "bodo": "brx", "dogri": "doi", "english": "en", "gujarati": "gu", "hindi": "hi", "kannada": "kn",
    "konkani": "kok", "kashmiri": "ks", "maithili": "mai", "malayalam": "ml", "manipuri": "mni",
    "meitei": "mni", "meetei": "mni", "marathi": "mr", "nepali": "ne", "odia": "or", "oriya": "or",
    "punjabi": "pa", "panjabi": "pa", "sanskrit": "sa", "santali": "sat", "sindhi": "sd", "tamil": "ta",
    "telugu": "te", "urdu": "ur",
}
_CODES_LOWER = {code.lower(): code for code in LANGUAGE_NAMES}

# Codes outside Bodhan's coverage that callers commonly ask for, so errors can name them.
KNOWN_UNSUPPORTED: dict[str, str] = {
    "fr": "French", "es": "Spanish", "de": "German", "it": "Italian", "pt": "Portuguese", "ru": "Russian",
    "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "ar": "Arabic", "tr": "Turkish", "nl": "Dutch",
    "si": "Sinhala", "th": "Thai", "vi": "Vietnamese", "id": "Indonesian", "fa": "Persian", "sw": "Swahili",
    "tcy": "Tulu", "my": "Burmese",
}
_UNSUPPORTED_NAMES = {name.lower(): code for code, name in KNOWN_UNSUPPORTED.items()}

CAPABILITY_LANGUAGES = {
    "transcribe": TRANSCRIBE_LANGUAGES,
    "translate": TRANSLATE_LANGUAGES,
    "speak": SPEAK_LANGUAGES,
}
CAPABILITY_MODEL = {"transcribe": "indic-transcribe", "translate": "indic-translate", "speak": "indic-speak"}


def normalize_code(value: str) -> str | None:
    """Map a code or language name to a Bodhan code, or ``None`` if it is not a known Bodhan language."""
    raw = value.strip()
    if not raw:
        return None
    key = raw.lower().replace("_", "-")
    if key.endswith("-in"):  # tolerate BCP-47 region tags (India) sent by some clients
        key = key[:-3]
    if key in _CODES_LOWER:
        return _CODES_LOWER[key]
    return _ALIASES.get(key.split("(")[0].strip())


def describe(code: str) -> str:
    return f"{LANGUAGE_NAMES.get(code, code)} ({code})"


def resolve(value: str, capability: str) -> str:
    """Resolve ``value`` for ``capability`` or raise an actionable ``unsupported_language`` error."""
    supported = CAPABILITY_LANGUAGES[capability]
    code = normalize_code(value)
    if code == "ks-Deva" and capability != "translate":
        code = "ks"
    if code in ("mni-Beng",) and capability != "translate":
        code = "mni"
    if code == "sd-Arab" and capability != "translate":
        code = "sd"
    if code is None or code not in supported:
        key = value.strip().lower()
        name = KNOWN_UNSUPPORTED.get(key) or (value.strip().title() if key in _UNSUPPORTED_NAMES else None)
        label = name or (LANGUAGE_NAMES.get(code) if code else None) or value
        raise ToolFailure(
            UNSUPPORTED_LANGUAGE,
            f"{label!s} is not supported by {CAPABILITY_MODEL[capability]}.",
            hint=(
                "Bodhan models cover Indian languages and English only. Do not substitute another language "
                "or produce output with a different system; tell the user the language is unsupported."
            ),
            details={"requested": value, "supported": sorted(supported)},
        )
    return code


def voice_library() -> dict:
    """The full voice library, for the list_voices tool and the bodhan://voices resource."""
    return {
        "voices_by_recorded_language": VOICES,
        "english_default_voices": ENGLISH_DEFAULT_VOICES,
        "styles": list(STYLES),
        "note": "Voices come from a fixed library; any voice can read any supported language, but a voice "
        "recorded in the target language sounds most natural. Voices are not cloned.",
    }


def all_voice_names() -> dict[str, str]:
    """Lower-case voice name -> canonical name."""
    names = {}
    for groups in VOICES.values():
        for voices in groups.values():
            for v in voices:
                names[v.lower()] = v
    return names


def pick_voice(language: str, gender: str | None) -> str:
    gender = (gender or "female").lower()
    if gender not in ("female", "male"):
        gender = "female"
    if language == "en":
        return ENGLISH_DEFAULT_VOICES[gender]
    groups = VOICES.get(language) or VOICES["hi"]
    return groups[gender][0]


def voice_gender(voice: str) -> str | None:
    for groups in VOICES.values():
        for gender, voices in groups.items():
            if voice in voices:
                return gender
    return None
