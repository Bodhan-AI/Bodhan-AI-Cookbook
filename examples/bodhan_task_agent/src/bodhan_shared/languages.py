"""Language and voice registry, from Cookbook's bodhan_api_rules.json (Sept 2026)."""
LANGUAGES = {
    "as": "Assamese", "bn": "Bengali", "brx": "Bodo", "doi": "Dogri",
    "en": "English", "gu": "Gujarati", "hi": "Hindi", "kn": "Kannada",
    "kok": "Konkani", "ks": "Kashmiri", "mai": "Maithili", "ml": "Malayalam",
    "mni": "Manipuri", "mr": "Marathi", "ne": "Nepali", "or": "Odia",
    "pa": "Punjabi", "sa": "Sanskrit", "sat": "Santali", "sd": "Sindhi",
    "ta": "Tamil", "te": "Telugu", "ur": "Urdu",
}
# Fixed voices, not clones. English voice choice is published in the Cookbook TTS tutorial.
FEMALE_VOICES = {
    "as": "Prastuti", "bn": "Ishita", "brx": "Gwrbw", "doi": "Preeti",
    "gu": "Dhara", "hi": "Kavya", "kn": "Deepika", "kok": "Anjali",
    "ks": "Zoon", "mai": "Vaidehi", "ml": "Lakshmi", "mni": "Thoibi",
    "mr": "Anagha", "ne": "Srijana", "or": "Itishree", "pa": "Kaur",
    "sa": "Bharati", "sat": "Phulmani", "sd": "Moomal", "ta": "Anitha",
    "te": "Sravani", "ur": "Saba", "en": "Kavya",
}
MALE_VOICES = {
    "as": "Ankur", "bn": "Sourav", "brx": "Sansuma", "doi": "Sham",
    "gu": "Parth", "hi": "Amit", "kn": "Adarsh", "kok": "Sandeep",
    "ks": "Ishfaq", "mai": "Madhukar", "ml": "Kiran", "mni": "Chaoba",
    "mr": "Chinmay", "ne": "Sagar", "or": "Akash", "pa": "Manpreet",
    "sa": "Aryaman", "sat": "Sibu", "sd": "Rano", "ta": "Arun",
    "te": "Vamsi", "ur": "Zaid", "en": "Amit",
}
SUPPORTED_TRANSLATION = set(LANGUAGES) | {"ks-Deva", "mni-Beng", "sd-Arab"}


def normalize_lang(value: str) -> str:
    value = value.strip()
    if value in SUPPORTED_TRANSLATION:
        return value
    for code, label in LANGUAGES.items():
        if value.casefold() == label.casefold():
            return code
    raise ValueError(f"Unsupported language '{value}'. Supported languages: {', '.join(sorted(LANGUAGES))}")


def infer_script_lang(text: str) -> str | None:
    """Conservative script inference; ambiguous Devanagari and Bengali are not disambiguated."""
    ranges = {
        "te": (0x0C00, 0x0C7F), "ta": (0x0B80, 0x0BFF),
        "kn": (0x0C80, 0x0CFF), "ml": (0x0D00, 0x0D7F),
        "gu": (0x0A80, 0x0AFF), "pa": (0x0A00, 0x0A7F),
        "bn": (0x0980, 0x09FF), "or": (0x0B00, 0x0B7F),
        "hi": (0x0900, 0x097F),
    }
    counts = {lang: sum(lo <= ord(c) <= hi for c in text) for lang, (lo, hi) in ranges.items()}
    lang = max(counts, key=counts.get) if counts else None
    return lang if lang and counts[lang] >= 2 else None
