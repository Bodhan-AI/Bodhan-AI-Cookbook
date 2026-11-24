"""Language, voice and capability data for the Bodhan models.

Source: scripts/bodhan_api_rules.json in the Bodhan AI Cookbook (version 2026-09-21),
which mirrors https://console.bodhan.ai/api-docs/. Copied here so this recipe is
self-contained. If the live API disagrees with this file, the API wins: update it.
"""
from __future__ import annotations

import re

SOURCE_VERSION = "2026-09-21"

LANGUAGE_NAMES = {
    "as": "Assamese", "bhb": "Bhili", "bho": "Bhojpuri", "bn": "Bengali", "brx": "Bodo",
    "doi": "Dogri", "en": "English", "gu": "Gujarati", "hi": "Hindi", "kn": "Kannada",
    "kok": "Konkani", "ks": "Kashmiri (Perso-Arabic)", "ks-Deva": "Kashmiri (Devanagari)",
    "mai": "Maithili", "ml": "Malayalam", "mni": "Manipuri (Meitei Mayek)",
    "mni-Beng": "Manipuri (Bengali script)", "mr": "Marathi", "ne": "Nepali", "or": "Odia",
    "pa": "Punjabi", "sa": "Sanskrit", "sat": "Santali", "sd": "Sindhi (Devanagari)",
    "sd-Arab": "Sindhi (Perso-Arabic)", "ta": "Tamil", "te": "Telugu", "ur": "Urdu",
}

STT_LANGUAGES = frozenset([
    "as", "bhb", "bho", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "mai",
    "ml", "mni", "mr", "ne", "or", "pa", "sa", "sat", "sd", "ta", "te", "ur",
])
TRANSLATE_LANGUAGES = frozenset([
    "as", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "ks-Deva", "mai", "ml",
    "mni", "mni-Beng", "mr", "ne", "or", "pa", "sa", "sat", "sd", "sd-Arab", "ta", "te", "ur",
])
TRANSLITERATE_LANGUAGES = frozenset([
    "as", "bn", "brx", "doi", "gu", "hi", "kn", "kok", "ks", "ks-Deva", "mai", "ml", "mni",
    "mni-Beng", "mr", "ne", "or", "pa", "sa", "sat", "sd", "sd-Arab", "ta", "te", "ur",
])
TTS_LANGUAGES = frozenset([
    "as", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "mai", "ml", "mni", "mr",
    "ne", "or", "pa", "sa", "sat", "sd", "ta", "te", "ur",
])

# The four capabilities the tools expose.
CAPABILITIES = {
    "transcribe": STT_LANGUAGES,
    "translate": TRANSLATE_LANGUAGES,
    "speak": TTS_LANGUAGES,
    "transliterate": TRANSLITERATE_LANGUAGES,
}

# Voices per language (voice name -> any voice can read any language, per the API docs).
VOICES = {
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

STYLES = [
    "AIR style news", "Customer Care", "TV style news", "advertisements", "anger",
    "children's stories", "disgust", "educational lecture", "fear", "happy", "news", "sad",
    "single person narration audiobook", "surprise",
]

# Informal names people use in tasks, mapped to codes.
_ALIASES = {
    "oriya": "or", "odia": "or", "punjabi": "pa", "panjabi": "pa", "bangla": "bn",
    "sindhi": "sd", "kashmiri": "ks", "manipuri": "mni", "meitei": "mni", "meiteilon": "mni",
    "konkani": "kok", "santhali": "sat", "santali": "sat", "bodo": "brx", "dogri": "doi",
}
# Well-known languages the models do NOT support: recognised so the error can name them.
_UNSUPPORTED_NAMES = {
    "french": "fr", "spanish": "es", "german": "de", "chinese": "zh", "mandarin": "zh",
    "japanese": "ja", "korean": "ko", "arabic": "ar", "russian": "ru", "portuguese": "pt",
    "italian": "it", "turkish": "tr", "indonesian": "id", "thai": "th", "vietnamese": "vi",
}

_CANONICAL = {code.lower(): code for code in LANGUAGE_NAMES}
_NAME_TO_CODE = {name.split(" (")[0].lower(): code for code, name in LANGUAGE_NAMES.items()
                 if "-" not in code}


def normalize_language(value: str | None) -> str | None:
    """Turn 'Hindi', 'hi-IN', 'KS-deva', 'French' into a language code.

    Returns None only when the input is empty or unrecognisable. Unsupported but
    recognised languages (e.g. 'French' -> 'fr') are returned so callers can explain
    the problem; use check_support() to decide whether a capability supports it.
    """
    if not value or not str(value).strip():
        return None
    text = str(value).strip().lower().replace("_", "-")
    if text in _CANONICAL:
        return _CANONICAL[text]
    if text in _ALIASES:
        return _ALIASES[text]
    if text in _NAME_TO_CODE:
        return _NAME_TO_CODE[text]
    if text in _UNSUPPORTED_NAMES:
        return _UNSUPPORTED_NAMES[text]
    base = text.split("-")[0]
    if base in _CANONICAL:
        return _CANONICAL[base]
    if re.fullmatch(r"[a-z]{2,3}", base):
        return base
    return None


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code, code)


def check_support(capability: str, language: str | None) -> dict:
    """Can this capability ('transcribe', 'translate', 'speak', 'transliterate') use this language?

    Always returns a dict: {supported, language, name, reason, supported_languages?}.
    """
    if capability not in CAPABILITIES:
        return {
            "supported": False, "language": None, "name": None,
            "reason": f"Unknown capability '{capability}'. Use one of: {', '.join(sorted(CAPABILITIES))}.",
        }
    code = normalize_language(language)
    supported = CAPABILITIES[capability]
    if code is None:
        return {
            "supported": False, "language": None, "name": None,
            "reason": f"Could not recognise the language '{language}'.",
            "supported_languages": sorted(supported),
        }
    if code in supported:
        return {"supported": True, "language": code, "name": language_name(code), "reason": "ok"}

    reason = f"{capability} does not support {language_name(code)} ('{code}')."
    others = [cap for cap, langs in CAPABILITIES.items() if cap != capability and code in langs]
    if others:
        reason += f" It is supported for: {', '.join(others)}."
    return {
        "supported": False, "language": code, "name": language_name(code), "reason": reason,
        "supported_languages": sorted(supported),
    }


def all_voice_names() -> list[str]:
    return sorted({v for genders in VOICES.values() for names in genders.values() for v in names})


def pick_voice(language: str | None, gender: str | None = None, name: str | None = None) -> dict:
    """Choose a voice. Raises ValueError with an actionable message on bad input.

    - name given: must be a known voice (any voice can read any language).
    - gender given ('female'/'male'): first voice of that gender for the language,
      falling back to Hindi's voice of that gender when the language has none.
    - neither: first female voice for the language, else Hindi's.
    """
    if name:
        for known in all_voice_names():
            if known.lower() == name.strip().lower():
                return {"voice": known, "reason": "voice chosen by name"}
        raise ValueError(f"Unknown voice '{name}'. Known voices: {', '.join(all_voice_names())}.")

    wanted = (gender or "female").strip().lower()
    if wanted not in ("female", "male"):
        raise ValueError("gender must be 'female' or 'male'.")
    code = normalize_language(language)
    if code in VOICES and VOICES[code][wanted]:
        return {"voice": VOICES[code][wanted][0], "reason": f"first {wanted} voice for {language_name(code)}"}
    return {"voice": VOICES["hi"][wanted][0], "reason": f"no {wanted} voice recorded for '{language}'; used Hindi's"}
