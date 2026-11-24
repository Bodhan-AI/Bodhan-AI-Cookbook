from __future__ import annotations

from typing import Literal

TRANSLATE_TARGETS: dict[str, str] = {
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
    "ks-Deva": "Kashmiri (Devanagari script)",
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

TranslateTarget = Literal[
    "as", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "ks-Deva", "mai", "ml",
    "mni", "mni-Beng", "mr", "ne", "or", "pa", "sa", "sat", "sd", "sd-Arab", "ta", "te", "ur",
]  # fmt: skip

TRANSCRIBE_LANGUAGES: dict[str, str] = {
    "as": "Assamese",
    "bhb": "Bhili",
    "bho": "Bhojpuri",
    "bn": "Bengali",
    "brx": "Bodo",
    "doi": "Dogri",
    "en": "English (Indian)",
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

SCRIPT_RANGES: tuple[tuple[int, int, str], ...] = (
    (0x0041, 0x005A, "Latin"),
    (0x0061, 0x007A, "Latin"),
    (0x00C0, 0x024F, "Latin"),
    (0x0600, 0x06FF, "Arabic"),
    (0x0750, 0x077F, "Arabic"),
    (0x08A0, 0x08FF, "Arabic"),
    (0x0900, 0x097F, "Devanagari"),
    (0x0980, 0x09FF, "Bengali"),
    (0x0A00, 0x0A7F, "Gurmukhi"),
    (0x0A80, 0x0AFF, "Gujarati"),
    (0x0B00, 0x0B7F, "Odia"),
    (0x0B80, 0x0BFF, "Tamil"),
    (0x0C00, 0x0C7F, "Telugu"),
    (0x0C80, 0x0CFF, "Kannada"),
    (0x0D00, 0x0D7F, "Malayalam"),
    (0x1C50, 0x1C7F, "Ol Chiki"),
    (0xA8E0, 0xA8FF, "Devanagari"),
    (0xABC0, 0xABFF, "Meetei Mayek"),
    (0xFB50, 0xFDFF, "Arabic"),
    (0xFE70, 0xFEFF, "Arabic"),
)

SCRIPT_LANGUAGES: dict[str, tuple[str, ...]] = {
    "Arabic": ("ks", "sd", "ur"),
    "Bengali": ("as", "bn", "mni"),
    "Devanagari": ("bhb", "bho", "brx", "doi", "hi", "kok", "ks", "mai", "mr", "ne", "sa", "sd"),
    "Gujarati": ("gu",),
    "Gurmukhi": ("pa",),
    "Kannada": ("kn",),
    "Latin": ("en",),
    "Malayalam": ("ml",),
    "Meetei Mayek": ("mni",),
    "Odia": ("or",),
    "Ol Chiki": ("sat",),
    "Tamil": ("ta",),
    "Telugu": ("te",),
}

LANGUAGE_SCRIPTS: dict[str, frozenset[str]] = {
    code: frozenset(script for script, codes in SCRIPT_LANGUAGES.items() if code in codes)
    for code in TRANSCRIBE_LANGUAGES
}

SPEAK_LANGUAGES: dict[str, str] = {
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

SpeakLanguage = Literal[
    "as", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "mai", "ml",
    "mni", "mr", "ne", "or", "pa", "sa", "sat", "sd", "ta", "te", "ur",
]  # fmt: skip

SPEAK_VOICES: dict[str, tuple[str, str]] = {
    "Prastuti": ("as", "female"),
    "Ankur": ("as", "male"),
    "Ishita": ("bn", "female"),
    "Sourav": ("bn", "male"),
    "Gwrbw": ("brx", "female"),
    "Sansuma": ("brx", "male"),
    "Preeti": ("doi", "female"),
    "Sham": ("doi", "male"),
    "Dhara": ("gu", "female"),
    "Parth": ("gu", "male"),
    "Kavya": ("hi", "female"),
    "Suhani": ("hi", "female"),
    "Amit": ("hi", "male"),
    "Deepika": ("kn", "female"),
    "Adarsh": ("kn", "male"),
    "Anjali": ("kok", "female"),
    "Sandeep": ("kok", "male"),
    "Zoon": ("ks", "female"),
    "Ishfaq": ("ks", "male"),
    "Vaidehi": ("mai", "female"),
    "Madhukar": ("mai", "male"),
    "Lakshmi": ("ml", "female"),
    "Kiran": ("ml", "male"),
    "Thoibi": ("mni", "female"),
    "Chaoba": ("mni", "male"),
    "Anagha": ("mr", "female"),
    "Chinmay": ("mr", "male"),
    "Srijana": ("ne", "female"),
    "Sagar": ("ne", "male"),
    "Itishree": ("or", "female"),
    "Akash": ("or", "male"),
    "Kaur": ("pa", "female"),
    "Manpreet": ("pa", "male"),
    "Bharati": ("sa", "female"),
    "Aryaman": ("sa", "male"),
    "Phulmani": ("sat", "female"),
    "Sibu": ("sat", "male"),
    "Moomal": ("sd", "female"),
    "Rano": ("sd", "male"),
    "Anitha": ("ta", "female"),
    "Arun": ("ta", "male"),
    "Sravani": ("te", "female"),
    "Vamsi": ("te", "male"),
    "Saba": ("ur", "female"),
    "Zaid": ("ur", "male"),
}

SpeakVoice = Literal[
    "Prastuti", "Ankur", "Ishita", "Sourav", "Gwrbw", "Sansuma", "Preeti", "Sham", "Dhara", "Parth",
    "Kavya", "Suhani", "Amit", "Deepika", "Adarsh", "Anjali", "Sandeep", "Zoon", "Ishfaq", "Vaidehi",
    "Madhukar", "Lakshmi", "Kiran", "Thoibi", "Chaoba", "Anagha", "Chinmay", "Srijana", "Sagar",
    "Itishree", "Akash", "Kaur", "Manpreet", "Bharati", "Aryaman", "Phulmani", "Sibu", "Moomal", "Rano",
    "Anitha", "Arun", "Sravani", "Vamsi", "Saba", "Zaid",
]  # fmt: skip

SPEAK_DEFAULT_VOICES: dict[tuple[str, str], str] = {
    **{(lang, gender): name for name, (lang, gender) in reversed(SPEAK_VOICES.items())},
    ("en", "female"): "Kavya",
    ("en", "male"): "Arun",
}

SpeakStyle = Literal[
    "AIR style news", "Customer Care", "TV style news", "advertisements", "anger", "children's stories",
    "disgust", "educational lecture", "fear", "happy", "news", "sad", "single person narration audiobook",
    "surprise",
]  # fmt: skip

TranscribeLanguage = Literal[
    "as", "bhb", "bho", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "mai",
    "ml", "mni", "mr", "ne", "or", "pa", "sa", "sat", "sd", "ta", "te", "ur",
]  # fmt: skip
