"""Language and voice tables, copied from the Bodhan API docs
(https://console.bodhan.ai/api-docs/, read 16 Nov 2026).

The four models do NOT share one language list, and that matters:
- STT accepts 25 codes (including Bhili ``bhb`` and Bhojpuri ``bho``).
- Translation accepts 26 codes over 23 languages; ``bhb`` and ``bho`` are
  refused with ``unsupported_language``. French (``fr``) is not supported
  at all, so a request to translate into French must fail honestly.
- TTS accepts 22 Indian languages plus English, with 45 named voices.
- OCR takes no language field; the script is inferred from the image.
"""

from __future__ import annotations

# --- Language names (for messages and result.md) ---------------------------
LANGUAGE_NAMES = {
    "as": "Assamese", "bhb": "Bhili", "bho": "Bhojpuri", "bn": "Bengali",
    "brx": "Bodo", "doi": "Dogri", "en": "English", "gu": "Gujarati",
    "hi": "Hindi", "kn": "Kannada", "kok": "Konkani", "ks": "Kashmiri",
    "ks-Deva": "Kashmiri (Devanagari)", "mai": "Maithili", "ml": "Malayalam",
    "mni": "Manipuri", "mni-Beng": "Manipuri (Bengali)",
    "mr": "Marathi", "ne": "Nepali", "or": "Odia", "pa": "Punjabi",
    "sa": "Sanskrit", "sat": "Santali", "sd": "Sindhi",
    "sd-Arab": "Sindhi (Perso-Arabic)", "ta": "Tamil", "te": "Telugu",
    "ur": "Urdu",
    # European / East Asian languages (not supported by Bodhan; named here
    # so refusal messages say "French", not "fr").
    "fr": "French", "de": "German", "es": "Spanish", "pt": "Portuguese",
    "ru": "Russian", "zh": "Chinese", "ja": "Japanese", "ko": "Korean",
    "ar": "Arabic",
}

# English names a user might write in a task, mapped to codes.
LANGUAGE_NAME_TO_CODE = {
    "assamese": "as", "bhili": "bhb", "bhojpuri": "bho", "bengali": "bn",
    "bodo": "brx", "dogri": "doi", "english": "en", "gujarati": "gu",
    "hindi": "hi", "kannada": "kn", "konkani": "kok", "kashmiri": "ks",
    "maithili": "mai", "malayalam": "ml", "manipuri": "mni",
    "marathi": "mr", "nepali": "ne", "odia": "or", "oriya": "or",
    "punjabi": "pa", "sanskrit": "sa", "santali": "sat", "sindhi": "sd",
    "tamil": "ta", "telugu": "te", "urdu": "ur", "french": "fr",
    "german": "de", "spanish": "es", "portuguese": "pt", "russian": "ru",
    "chinese": "zh", "japanese": "ja", "korean": "ko", "arabic": "ar",
}

# --- Per-model language support --------------------------------------------
STT_LANGUAGES = {
    "as", "bhb", "bho", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok",
    "ks", "mai", "ml", "mni", "mr", "ne", "or", "pa", "sa", "sat", "sd",
    "ta", "te", "ur",
}

TRANSLATE_LANGUAGES = {
    "as", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks",
    "ks-Deva", "mai", "ml", "mni", "mni-Beng", "mr", "ne", "or", "pa",
    "sa", "sat", "sd", "sd-Arab", "ta", "te", "ur",
}

# Codes the translation endpoint explicitly refuses.
TRANSLATE_REFUSED = {"bhb", "bho"}

TTS_LANGUAGES = {
    "as", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "mai",
    "ml", "mni", "mr", "ne", "or", "pa", "sa", "sat", "sd", "ta", "te",
    "ur",
}

# TTS voices, grouped by the language each was recorded in. Any voice can
# read any language; the default voice for a language is its female voice.
VOICES = {
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
    # English has no recorded voice in the table; any voice can read it.
    "en": {"female": "Kavya", "male": "Amit"},
}

ALL_VOICE_NAMES = {name for voices in VOICES.values() for name in voices.values()}

TTS_STYLES = {
    "AIR style news", "Customer Care", "TV style news", "advertisements",
    "anger", "children's stories", "disgust", "educational lecture", "fear",
    "happy", "news", "sad", "single person narration audiobook", "surprise",
}

# --- Script detection (deterministic, local, no model call) -----------------
# Unicode ranges for the major Indic scripts. Detection returns the script
# and the candidate languages that use it; it cannot pin one language when
# several share a script (e.g. Devanagari), and it says so.
_SCRIPT_RANGES = [
    ("Devanagari", (0x0900, 0x097F), ["hi", "mr", "ne", "sa", "bho", "doi", "mai"]),
    ("Bengali", (0x0980, 0x09FF), ["bn", "as"]),
    ("Gurmukhi", (0x0A00, 0x0A7F), ["pa"]),
    ("Gujarati", (0x0A80, 0x0AFF), ["gu"]),
    ("Odia", (0x0B00, 0x0B7F), ["or"]),
    ("Tamil", (0x0B80, 0x0BFF), ["ta"]),
    ("Telugu", (0x0C00, 0x0C7F), ["te"]),
    ("Kannada", (0x0C80, 0x0CFF), ["kn"]),
    ("Malayalam", (0x0D00, 0x0D7F), ["ml"]),
    ("Arabic", (0x0600, 0x06FF), ["ur", "ks", "sd-Arab"]),
    ("Latin", (0x0041, 0x007A), ["en"]),
]


def detect_script(text: str) -> dict:
    """Detect the dominant script of ``text`` from Unicode ranges.

    Returns ``{"script": ..., "candidates": [...], "confidence": ...}``.
    ``confidence`` is the share of script-bearing characters in the dominant
    script. Returns script ``"unknown"`` when there is nothing to read.
    """
    counts: dict[str, int] = {}
    total = 0
    for ch in text:
        cp = ord(ch)
        if cp < 0x41 or ch.isspace():
            continue
        total += 1
        for name, (lo, hi), _ in _SCRIPT_RANGES:
            if lo <= cp <= hi:
                counts[name] = counts.get(name, 0) + 1
                break
    if not counts:
        return {"script": "unknown", "candidates": [], "confidence": 0.0}
    script = max(counts, key=counts.get)
    candidates = next(c for n, _, c in _SCRIPT_RANGES if n == script)
    return {
        "script": script,
        "candidates": candidates,
        "confidence": round(counts[script] / total, 3),
    }


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code, code)


def default_voice(language: str, gender: str = "female") -> str:
    voices = VOICES.get(language)
    if not voices:
        raise ValueError(f"No voice table entry for language {language!r}")
    return voices[gender]
