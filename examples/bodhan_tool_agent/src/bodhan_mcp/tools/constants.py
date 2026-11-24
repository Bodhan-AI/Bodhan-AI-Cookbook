"""
Official voice mappings for Bodhan's Indic-Speak TTS.
Source: Bodhan API Documentation (https://console.bodhan.ai/api-docs/#text-to-speech-api)
"""

SUPPORTED_LANGUAGES: dict[str, str] = {
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

# 45 official voice names from Bodhan API Docs
DEFAULT_VOICES: dict[str, dict[str, str]] = {
    "as": {"female": "Prastuti", "male": "Ankur"},
    "bn": {"female": "Ishita", "male": "Sourav"},
    "brx": {"female": "Gwrbw", "male": "Sansuma"},
    "doi": {"female": "Preeti", "male": "Sham"},
    "en": {"female": "Kavya", "male": "Amit"},       # Any voice can read English
    "gu": {"female": "Dhara", "male": "Parth"},
    "hi": {"female": "Kavya", "male": "Amit"},       # Also Suhani available
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
}

SCRIPT_TO_LANG: dict[str, str] = {
    "Devanagari": "hi",
    "Bengali": "bn",
    "Tamil": "ta",
    "Telugu": "te",
    "Gujarati": "gu",
    "Gurmukhi": "pa",
    "Kannada": "kn",
    "Malayalam": "ml",
    "Oriya": "or",
}
