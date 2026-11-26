"""Language codes and voices accepted by each Bodhan endpoint.

Source: https://console.bodhan.ai/api-docs/ (checked Nov 2026). The three
endpoints accept DIFFERENT lists, so validate against the right one.
"""

# POST /v1/audio/transcriptions  -> `language`
ASR_LANGUAGES = {
    "as", "bhb", "bho", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks",
    "mai", "ml", "mni", "mr", "ne", "or", "pa", "sa", "sat", "sd", "ta", "te", "ur",
}

# Local Indic-Transcribe Core (ASR_BACKEND=local) -> `lang`. From LANGUAGES in the model's own
# indic_transcribe.py (models/indic-transcribe-core). A superset of ASR_LANGUAGES (adds bgc, hne),
# so any language valid for hosted ASR is valid locally too (tested).
LOCAL_ASR_LANGUAGES = ASR_LANGUAGES | {"bgc", "hne"}

# Translation (POST /v1/chat/completions, model indic-translate) -> `target_language` (any pair works, Indic<->Indic included).
# bhb / bho are NOT accepted here even though ASR accepts them.
TRANSLATE_LANGUAGES = {
    "as", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "ks-Deva",
    "mai", "ml", "mni", "mni-Beng", "mr", "ne", "or", "pa", "sa", "sat", "sd",
    "sd-Arab", "ta", "te", "ur",
}

# POST /v1/audio/speech  -> instructions.lang
TTS_LANGUAGES = {
    "as", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "mai", "ml",
    "mni", "mr", "ne", "or", "pa", "sa", "sat", "sd", "ta", "te", "ur",
}

# Any voice can read any language; these are grouped by recording language.
VOICES = {
    "as": ["Prastuti", "Ankur"], "bn": ["Ishita", "Sourav"], "brx": ["Gwrbw", "Sansuma"],
    "doi": ["Preeti", "Sham"], "gu": ["Dhara", "Parth"], "hi": ["Kavya", "Suhani", "Amit"],
    "kn": ["Deepika", "Adarsh"], "kok": ["Anjali", "Sandeep"], "ks": ["Zoon", "Ishfaq"],
    "mai": ["Vaidehi", "Madhukar"], "ml": ["Lakshmi", "Kiran"], "mni": ["Thoibi", "Chaoba"],
    "mr": ["Anagha", "Chinmay"], "ne": ["Srijana", "Sagar"], "or": ["Itishree", "Akash"],
    "pa": ["Kaur", "Manpreet"], "sa": ["Bharati", "Aryaman"], "sat": ["Phulmani", "Sibu"],
    "sd": ["Moomal", "Rano"], "ta": ["Anitha", "Arun"], "te": ["Sravani", "Vamsi"],
    "ur": ["Saba", "Zaid"],
}
ALL_VOICES = {v for vs in VOICES.values() for v in vs}

TTS_STYLES = {
    "AIR style news", "Customer Care", "TV style news", "advertisements", "anger",
    "children's stories", "disgust", "educational lecture", "fear", "happy", "news",
    "sad", "single person narration audiobook", "surprise",
}


def default_voice(lang: str) -> str:
    """First voice recorded in `lang`, else a Hindi voice (works for English too)."""
    return VOICES.get(lang, VOICES["hi"])[0]


# Display names for user-facing text (the overlay), keyed by any code above.
LANGUAGE_NAMES = {
    "as": "Assamese", "bhb": "Bhili", "bho": "Bhojpuri", "bn": "Bengali", "brx": "Bodo", "doi": "Dogri",
    "en": "English", "gu": "Gujarati", "hi": "Hindi", "kn": "Kannada", "kok": "Konkani", "ks": "Kashmiri",
    "ks-Deva": "Kashmiri (Devanagari)", "mai": "Maithili", "ml": "Malayalam", "mni": "Manipuri",
    "mni-Beng": "Manipuri (Bengali script)", "mr": "Marathi", "ne": "Nepali", "or": "Odia", "pa": "Punjabi",
    "sa": "Sanskrit", "sat": "Santali", "sd": "Sindhi", "sd-Arab": "Sindhi (Arabic script)", "ta": "Tamil",
    "te": "Telugu", "ur": "Urdu",
}


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code, code)
