from __future__ import annotations

from enum import Enum

from .models import ErrorCode, ToolError


class Script(Enum):
    # Store explicit Unicode integer ranges (start, end+1)
    DEVANAGARI = ((0x0900, 0x0980), ["hi", "mr", "ne", "sa", "kok", "mai", "brx", "doi"])
    BENGALI = ((0x0980, 0x0A00), ["bn", "as"])
    TAMIL = ((0x0B80, 0x0C00), ["ta"])
    MALAYALAM = ((0x0D00, 0x0D80), ["ml"])
    TELUGU = ((0x0C00, 0x0C80), ["te"])
    KANNADA = ((0x0C80, 0x0D00), ["kn"])
    GUJARATI = ((0x0A80, 0x0B00), ["gu"])
    ORIYA = ((0x0B00, 0x0B80), ["or"])
    PUNJABI = ((0x0A00, 0x0A80), ["pa"])  # Gurmukhi
    URDU = ((0x0600, 0x0700), ["ur", "ks", "sd"])  # Arabic script subset
    MEITEI = ((0xABC0, 0xAC00), ["mni"])
    OL_CHIKI = ((0x1C50, 0x1C80), ["sat"])
    ENGLISH = ((0x0041, 0x007B), ["en"])  # ASCII letters


class Language(Enum):
    AS = "as"
    BN = "bn"
    BRX = "brx"
    DOI = "doi"
    EN = "en"
    GU = "gu"
    HI = "hi"
    KN = "kn"
    KOK = "kok"
    KS = "ks"
    MAI = "mai"
    ML = "ml"
    MNI = "mni"
    MR = "mr"
    NE = "ne"
    OR = "or"
    PA = "pa"
    SA = "sa"
    SAT = "sat"
    SD = "sd"
    TA = "ta"
    TE = "te"
    UR = "ur"


SUPPORTED_LANGUAGES = {lang.value for lang in Language}

# Verified Bodhan Indic-Speak speaker library (45 voices across 22 Indic languages;
# all voices also support English input).
DEFAULT_VOICES: dict[str, dict[str, str]] = {
    "as": {"female": "Prastuti", "male": "Ankur"},
    "bn": {"female": "Ishita", "male": "Sourav"},
    "brx": {"female": "Gwrbw", "male": "Sansuma"},
    "doi": {"female": "Preeti", "male": "Sham"},
    "gu": {"female": "Dhara", "male": "Parth"},
    "hi": {"female": "Kavya", "male": "Amit"},
    "kn": {"female": "Deepika", "male": "Adarsh"},
    "kok": {"female": "Anjali", "male": "Sandeep"},
    "ks": {"female": "Zoon", "male": "Ishfaq"},
    "mai": {"female": "Vaidehi", "male": "Madhukar"},
    "ml": {"female": "Lakshmi", "male": "Kiran"},
    "mni": {"female": "Thoibi", "male": "Chaoba"},
    "mr": {"female": "Anagha", "male": "Chinmay"},
    "ne": {"female": "Srijana", "male": "Sagar"},
    "or": {"female": "Itishree", "male": "Akash"},
    "pa": {"female": "Kaur", "male": "Manpreet"},
    "sa": {"female": "Bharati", "male": "Aryaman"},
    "sat": {"female": "Phulmani", "male": "Sibu"},
    "sd": {"female": "Moomal", "male": "Rano"},
    "ta": {"female": "Anitha", "male": "Arun"},
    "te": {"female": "Sravani", "male": "Vamsi"},
    "ur": {"female": "Saba", "male": "Zaid"},
    "en": {"female": "Kavya", "male": "Amit"},
}

ALL_SPEAK_VOICES: frozenset[str] = frozenset(
    {
        *(voice for pair in DEFAULT_VOICES.values() for voice in pair.values()),
        "Suhani",
    }
)
_VOICES_BY_LOWER: dict[str, str] = {name.lower(): name for name in ALL_SPEAK_VOICES}

_ALIASES = {
    "asm": "as",
    "assamese": "as",
    "ben": "bn",
    "bengali": "bn",
    "bangla": "bn",
    "bodo": "brx",
    "dogri": "doi",
    "eng": "en",
    "english": "en",
    "guj": "gu",
    "gujarati": "gu",
    "hind": "hi",
    "hindi": "hi",
    "kan": "kn",
    "kannada": "kn",
    "konkani": "kok",
    "kashmiri": "ks",
    "maithili": "mai",
    "mal": "ml",
    "malayalam": "ml",
    "manipuri": "mni",
    "meitei": "mni",
    "mar": "mr",
    "marathi": "mr",
    "nep": "ne",
    "nepali": "ne",
    "ori": "or",
    "odia": "or",
    "oriya": "or",
    "pan": "pa",
    "punjabi": "pa",
    "san": "sa",
    "sanskrit": "sa",
    "santali": "sat",
    "sindhi": "sd",
    "tam": "ta",
    "tamil": "ta",
    "tel": "te",
    "telugu": "te",
    "urd": "ur",
    "urdu": "ur",
}


def contains_non_english_script(text: str) -> bool:
    return any(
        start <= ord(char) < end
        for char in text
        for script in Script
        if script is not Script.ENGLISH
        for start, end in (script.value[0],)
    )


def normalize_lang_code(code: str) -> str:
    """Normalize variations like 'Hindi', 'HI', 'hi-IN' to canonical ISO code."""
    if not isinstance(code, str):
        raise ToolError(
            ErrorCode.INVALID_ARGUMENT,
            f"Language code must be a string, got {type(code).__name__}.",
        )

    cleaned = code.strip().lower().split("-")[0]
    resolved = _ALIASES.get(cleaned, cleaned)
    if resolved not in SUPPORTED_LANGUAGES:
        raise ToolError(
            ErrorCode.UNSUPPORTED_LANGUAGE,
            f"Unsupported language code: {code!r}.",
            hint=f"Supported languages: {', '.join(sorted(SUPPORTED_LANGUAGES))}.",
        )
    return resolved


def resolve_voice_id(
    language: str,
    voice_id: str | None = None,
    voice_gender: str | None = None,
) -> str:
    """Resolve and validate a Bodhan Indic-Speak voice ID before calling the API."""
    norm_lang = normalize_lang_code(language)
    lang_defaults = DEFAULT_VOICES.get(norm_lang, DEFAULT_VOICES["en"])

    if voice_gender is not None:
        cleaned_gender = voice_gender.strip().lower()
        if cleaned_gender not in {"male", "female"}:
            raise ToolError(
                ErrorCode.INVALID_ARGUMENT,
                f"`voice_gender` must be 'male' or 'female', got {voice_gender!r}.",
                hint="Pass 'female', 'male', or an explicit Bodhan voice_id.",
            )
        if voice_id is None:
            return lang_defaults[cleaned_gender]

    if voice_id is None:
        return lang_defaults["female"]

    cleaned = voice_id.strip()
    if cleaned.lower() in {"female", "male"}:
        return lang_defaults[cleaned.lower()]

    matched = _VOICES_BY_LOWER.get(cleaned.lower())
    if matched is None:
        raise ToolError(
            ErrorCode.INVALID_ARGUMENT,
            f"Unknown Indic-Speak voice {voice_id!r}.",
            hint=(
                f"Use a verified Bodhan voice such as {lang_defaults['female']!r} "
                f"(female) or {lang_defaults['male']!r} (male) for {norm_lang}."
            ),
        )
    return matched


def detect_script_language(text: str) -> dict:
    """Detect Unicode script and return matching language candidates."""
    if not text or not text.strip():
        return {"script": "UNKNOWN", "candidates": [], "ambiguous": False, "confidence": 0.0}

    indic_counts = {
        script.name: 0 for script in Script if script is not Script.ENGLISH
    }
    english_count = 0

    for char in text:
        code_point = ord(char)
        if (0x0041 <= code_point <= 0x005A) or (0x0061 <= code_point <= 0x007A):
            english_count += 1
            continue
        for script in Script:
            if script is Script.ENGLISH:
                continue
            start, end = script.value[0]
            if start <= code_point < end:
                indic_counts[script.name] += 1
                break

    indic_total = sum(indic_counts.values())
    if indic_total > 0:
        max_script = max(indic_counts, key=indic_counts.__getitem__)
        max_count = indic_counts[max_script]
        confidence = max_count / (indic_total + english_count)
        candidates = list(Script[max_script].value[1])
        ambiguous = max_script in {"DEVANAGARI", "BENGALI", "URDU"} or len(candidates) > 1
        return {
            "script": max_script,
            "candidates": candidates,
            "ambiguous": ambiguous,
            "confidence": round(confidence, 2),
        }

    if english_count > 0:
        return {
            "script": "ENGLISH",
            "candidates": ["en"],
            "ambiguous": False,
            "confidence": 1.0,
        }

    return {"script": "UNKNOWN", "candidates": [], "ambiguous": False, "confidence": 0.0}