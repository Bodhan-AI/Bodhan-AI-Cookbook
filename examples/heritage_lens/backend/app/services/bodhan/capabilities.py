"""Per-service capability tables transcribed from the Bodhan API reference.

Source: https://console.bodhan.ai/api-docs/  (read 2026-11-22)
Each service has its OWN language list and limits. Never share one list.
"""
from __future__ import annotations

from app.services.bodhan.exceptions import BodhanRequestInvalid

DOCS_URL = "https://console.bodhan.ai/api-docs/"
DOCS_READ_ON = "2026-11-22"

# --- indic-transcribe: language is optional upstream; we always send it. ---
TRANSCRIBE_LANGUAGES: dict[str, str] = {
    "as": "Assamese", "bhb": "Bhili", "bho": "Bhojpuri", "bn": "Bengali", "brx": "Bodo",
    "doi": "Dogri", "en": "English", "gu": "Gujarati", "hi": "Hindi", "kn": "Kannada",
    "kok": "Konkani", "ks": "Kashmiri", "mai": "Maithili", "ml": "Malayalam", "mni": "Manipuri",
    "mr": "Marathi", "ne": "Nepali", "or": "Odia", "pa": "Punjabi", "sa": "Sanskrit",
    "sat": "Santali", "sd": "Sindhi", "ta": "Tamil", "te": "Telugu", "ur": "Urdu",
}
TRANSCRIBE_MAX_SECONDS = 30.0
TRANSCRIBE_FORMATS = ("wav", "flac", "ogg", "mp3")  # documented; WebM/M4A/AAC/AMR -> HTTP 415

# --- indic-translate: target only; there is NO source-language field. ---
TRANSLATE_TARGETS: dict[str, str] = {
    "as": "Assamese", "bn": "Bengali", "brx": "Bodo", "doi": "Dogri", "en": "English",
    "gu": "Gujarati", "hi": "Hindi", "kn": "Kannada", "kok": "Konkani",
    "ks": "Kashmiri (Perso-Arabic)", "ks-Deva": "Kashmiri (Devanagari)", "mai": "Maithili",
    "ml": "Malayalam", "mni": "Manipuri (Meitei)", "mni-Beng": "Manipuri (Bengali)",
    "mr": "Marathi", "ne": "Nepali", "or": "Odia", "pa": "Punjabi", "sa": "Sanskrit",
    "sat": "Santali", "sd": "Sindhi (Devanagari)", "sd-Arab": "Sindhi (Perso-Arabic)",
    "ta": "Tamil", "te": "Telugu", "ur": "Urdu",
}
TRANSLATE_UNSUPPORTED = {"bhb": "Bhili", "bho": "Bhojpuri"}  # explicitly refused upstream
TRANSLATE_SCRIPTS = ("native", "roman", "codemix")
TRANSLATE_CODEMIX_UNAVAILABLE = {"en", "ks-Deva", "sd-Arab"}

# --- indic-transliterate: Indic only, ONE language (names both sides), roman|native. ---
TRANSLITERATE_LANGUAGES: dict[str, str] = {k: v for k, v in TRANSLATE_TARGETS.items() if k != "en"}
TRANSLITERATE_SCRIPTS = ("roman", "native")  # default upstream: roman. codemix rejected.

# --- indic-speak ---
SPEAK_LANGUAGES: dict[str, str] = {
    "as": "Assamese", "bn": "Bengali", "brx": "Bodo", "doi": "Dogri", "en": "English",
    "gu": "Gujarati", "hi": "Hindi", "kn": "Kannada", "kok": "Konkani", "ks": "Kashmiri",
    "mai": "Maithili", "ml": "Malayalam", "mni": "Manipuri", "mr": "Marathi", "ne": "Nepali",
    "or": "Odia", "pa": "Punjabi", "sa": "Sanskrit", "sat": "Santali", "sd": "Sindhi",
    "ta": "Tamil", "te": "Telugu", "ur": "Urdu",
}
# voice -> (gender, language the voice was recorded in). Any voice can read any language.
SPEAK_VOICES: dict[str, tuple[str, str]] = {}
for _lang, _female, _male in [
    ("as", ["Prastuti"], ["Ankur"]), ("bn", ["Ishita"], ["Sourav"]), ("brx", ["Gwrbw"], ["Sansuma"]),
    ("doi", ["Preeti"], ["Sham"]), ("gu", ["Dhara"], ["Parth"]), ("hi", ["Kavya", "Suhani"], ["Amit"]),
    ("kn", ["Deepika"], ["Adarsh"]), ("kok", ["Anjali"], ["Sandeep"]), ("ks", ["Zoon"], ["Ishfaq"]),
    ("mai", ["Vaidehi"], ["Madhukar"]), ("ml", ["Lakshmi"], ["Kiran"]), ("mni", ["Thoibi"], ["Chaoba"]),
    ("mr", ["Anagha"], ["Chinmay"]), ("ne", ["Srijana"], ["Sagar"]), ("or", ["Itishree"], ["Akash"]),
    ("pa", ["Kaur"], ["Manpreet"]), ("sa", ["Bharati"], ["Aryaman"]), ("sat", ["Phulmani"], ["Sibu"]),
    ("sd", ["Moomal"], ["Rano"]), ("ta", ["Anitha"], ["Arun"]), ("te", ["Sravani"], ["Vamsi"]),
    ("ur", ["Saba"], ["Zaid"]),
]:
    for _v in _female:
        SPEAK_VOICES[_v] = ("female", _lang)
    for _v in _male:
        SPEAK_VOICES[_v] = ("male", _lang)
SPEAK_STYLES = (
    "AIR style news", "Customer Care", "TV style news", "advertisements", "anger",
    "children's stories", "disgust", "educational lecture", "fear", "happy", "news", "sad",
    "single person narration audiobook", "surprise",
)
SPEAK_OUTPUT = {"format": "wav", "encoding": "PCM16", "sample_rate_hz": 24000, "channels": 1}
SPEAK_APPROX_MAX_SECONDS = 30.0

# --- indic-ocr ---
OCR_FORMATS = ("png", "jpeg")  # PDFs are NOT accepted upstream
OCR_TABLE_FORMATS = ("html", "markdown")
OCR_DEFAULT_MAX_TOKENS = 2048  # per block, not per page


def _invalid(msg: str, code: str = "unsupported_option") -> BodhanRequestInvalid:
    return BodhanRequestInvalid(msg, code=code)


def ensure_transcribe_language(code: str) -> str:
    if code not in TRANSCRIBE_LANGUAGES:
        raise _invalid(f"Language '{code}' is not supported for transcription.", "unsupported_language")
    return code


def ensure_translate_target(code: str) -> str:
    if code in TRANSLATE_UNSUPPORTED:
        raise _invalid(
            f"{TRANSLATE_UNSUPPORTED[code]} ('{code}') is not supported for translation. "
            "It is supported for transcription only.", "unsupported_language")
    if code not in TRANSLATE_TARGETS:
        raise _invalid(f"Target language '{code}' is not supported for translation.", "unsupported_language")
    return code


def ensure_translate_script(target: str, script: str) -> str:
    if script not in TRANSLATE_SCRIPTS:
        raise _invalid(f"Script must be one of {', '.join(TRANSLATE_SCRIPTS)}.", "invalid_script")
    if target == "en" and script != "native":
        raise _invalid("Script options do not apply when translating into English.", "invalid_script")
    if script == "codemix" and target in TRANSLATE_CODEMIX_UNAVAILABLE:
        raise _invalid(f"Code-mixed output is unavailable for '{target}'.", "unsupported_script_for_language")
    return script


def ensure_transliterate_language(code: str) -> str:
    if code == "en":
        raise _invalid("English cannot be transliterated: it is already written in Latin letters.",
                       "unsupported_language")
    if code not in TRANSLITERATE_LANGUAGES:
        raise _invalid(f"Language '{code}' is not supported for transliteration.", "unsupported_language")
    return code


def ensure_transliterate_script(script: str) -> str:
    if script not in TRANSLITERATE_SCRIPTS:
        raise _invalid("Output script must be 'roman' or 'native'.", "invalid_script")
    return script


def ensure_speak_language(code: str) -> str:
    if code not in SPEAK_LANGUAGES:
        raise _invalid(f"Language '{code}' is not supported for speech.", "unsupported_language")
    return code


def canonical_voice(name: str) -> str:
    for voice in SPEAK_VOICES:
        if voice.lower() == (name or "").strip().lower():
            return voice
    raise _invalid(f"Voice '{name}' is not one of the documented voices.", "unsupported_voice")


def canonical_style(style: str | None) -> str | None:
    if style is None or not str(style).strip():
        return None
    for s in SPEAK_STYLES:
        if s.lower() == style.strip().lower():
            return s
    raise _invalid(f"Style '{style}' is not one of the documented styles.", "unsupported_style")


def build_capabilities(configured: dict[str, bool], ffmpeg_available: bool | None = None) -> dict:
    """Payload for GET /api/ai/capabilities so the frontend can disable unsupported options."""
    return {
        "docs_url": DOCS_URL,
        "docs_read_on": DOCS_READ_ON,
        "ffmpeg_available": ffmpeg_available,
        "services": {
            "indic-transcribe": {
                "configured": configured.get("transcribe", False),
                "languages": TRANSCRIBE_LANGUAGES,
                "max_seconds_per_request": TRANSCRIBE_MAX_SECONDS,
                "upstream_formats": list(TRANSCRIBE_FORMATS),
                "notes": ["Long audio is split into segments of 30 s or less by the backend.",
                          "Browser WebM/M4A is converted to WAV on the backend before sending."],
            },
            "indic-translate": {
                "configured": configured.get("translate", False),
                "target_languages": TRANSLATE_TARGETS,
                "unsupported_languages": TRANSLATE_UNSUPPORTED,
                "scripts": list(TRANSLATE_SCRIPTS),
                "codemix_unavailable_for": sorted(TRANSLATE_CODEMIX_UNAVAILABLE),
                "notes": ["There is no source-language field; the model infers the source.",
                          "'roman' writes the target language in Latin letters; it is not translation into English."],
            },
            "indic-transliterate": {
                "configured": configured.get("transliterate", False),
                "languages": TRANSLITERATE_LANGUAGES,
                "scripts": list(TRANSLITERATE_SCRIPTS),
                "default_script": "roman",
                "notes": ["Same language in and out; only the writing system changes.",
                          "English is not supported."],
            },
            "indic-speak": {
                "configured": configured.get("speak", False),
                "languages": SPEAK_LANGUAGES,
                "voices": {v: {"gender": g, "recorded_in": l} for v, (g, l) in SPEAK_VOICES.items()},
                "styles": list(SPEAK_STYLES),
                "output": SPEAK_OUTPUT,
                "notes": ["Any voice can read any supported language.",
                          "Long text is split at sentence boundaries and the WAV segments are joined."],
            },
            "indic-ocr": {
                "configured": configured.get("ocr", False),
                "upstream_formats": list(OCR_FORMATS),
                "table_formats": list(OCR_TABLE_FORMATS),
                "default_max_tokens_per_block": OCR_DEFAULT_MAX_TOKENS,
                "notes": ["PNG/JPEG only upstream; PDFs are rendered to PNG pages by the backend.",
                          "No language parameter: the script is inferred from the image."],
            },
        },
    }
