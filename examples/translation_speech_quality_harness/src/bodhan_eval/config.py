"""Static facts: languages, prices, endpoints. The only place language-specific knowledge lives."""

BODHAN_BASE_URL = "https://api.bodhan.ai"

# Hosted prices from the assignment brief, in rupees.
PRICE_TRANSLATE_PER_OUTPUT_TOKEN = 0.20 / 10_000
PRICE_SPEAK_PER_CHAR = 6.0 / 10_000
PRICE_TRANSCRIBE_PER_MINUTE = 0.10

TRANSCRIBE_MAX_SECONDS = 30.0  # documented window; longer audio is chunked

# code: (prompt name used in the translate prompt, Unicode script word, female voice, male voice, Indic-Speak lang)
# Prompt names follow the Indic-Translate model card; voices follow the API docs.
_LANGS = {
    "as": ("Assamese", "BENGALI", "Prastuti", "Ankur", "as"),
    "bn": ("Bengali", "BENGALI", "Ishita", "Sourav", "bn"),
    "brx": ("Bodo", "DEVANAGARI", "Gwrbw", "Sansuma", "brx"),
    "doi": ("Dogri", "DEVANAGARI", "Preeti", "Sham", "doi"),
    "en": ("English", "LATIN", None, None, None),
    "gu": ("Gujarati", "GUJARATI", "Dhara", "Parth", "gu"),
    "hi": ("Hindi", "DEVANAGARI", "Kavya", "Amit", "hi"),
    "kn": ("Kannada", "KANNADA", "Deepika", "Adarsh", "kn"),
    "kok": ("Konkani", "DEVANAGARI", "Anjali", "Sandeep", "kok"),
    "ks": ("Kashmiri (Perso-Arabic script)", "ARABIC", "Zoon", "Ishfaq", "ks"),
    "ks-Deva": ("Kashmiri (Devanagari script)", "DEVANAGARI", "Zoon", "Ishfaq", "ks"),
    "mai": ("Maithili", "DEVANAGARI", "Vaidehi", "Madhukar", "mai"),
    "ml": ("Malayalam", "MALAYALAM", "Lakshmi", "Kiran", "ml"),
    "mni": ("Manipuri (Meitei script)", "MEETEI", "Thoibi", "Chaoba", "mni"),
    "mni-Beng": ("Manipuri (Bengali script)", "BENGALI", "Thoibi", "Chaoba", "mni"),
    "mr": ("Marathi", "DEVANAGARI", "Anagha", "Chinmay", "mr"),
    "ne": ("Nepali", "DEVANAGARI", "Srijana", "Sagar", "ne"),
    "or": ("Odia", "ORIYA", "Itishree", "Akash", "or"),
    "pa": ("Punjabi", "GURMUKHI", "Kaur", "Manpreet", "pa"),
    "sa": ("Sanskrit", "DEVANAGARI", "Bharati", "Aryaman", "sa"),
    "sat": ("Santali", "OL", "Phulmani", "Sibu", "sat"),
    "sd": ("Sindhi (Devanagari script)", "DEVANAGARI", "Moomal", "Rano", "sd"),
    "sd-Arab": ("Sindhi (Perso-Arabic script)", "ARABIC", "Moomal", "Rano", "sd"),
    "ta": ("Tamil", "TAMIL", "Anitha", "Arun", "ta"),
    "te": ("Telugu", "TELUGU", "Sravani", "Vamsi", "te"),
    "ur": ("Urdu", "ARABIC", "Saba", "Zaid", "ur"),
}
# shortcut: multi-script prompt names (ks, mni, sd variants) are inferred from the model card summary; check the card's table before relying on them.

TARGET_LANGS = sorted(c for c in _LANGS if c != "en")
_BY_LOWER = {c.lower(): c for c in _LANGS}
_BY_NAME = {v[0].split(" (")[0].lower(): c for c, v in _LANGS.items() if "(" not in v[0]}


def prompt_name(code: str) -> str:
    return _LANGS[code][0]


def script_of(code: str) -> str:
    return _LANGS[code][1]


def default_voice(code: str) -> str | None:
    return _LANGS[code][2]


def voices(code: str) -> list[str]:
    """The two Indic-Speak voices for a language, default first."""
    return [v for v in _LANGS[code][2:4] if v]


def speak_lang(code: str) -> str | None:
    return _LANGS[code][4]


def normalize_lang(value) -> str | None:
    """Map 'hi', 'HI', region-suffixed forms such as 'hi_IN', and 'Hindi' to a known code; None if unknown."""
    if not isinstance(value, str) or not value.strip():
        return None
    v = value.strip()
    if v.lower() in _BY_LOWER:
        return _BY_LOWER[v.lower()]
    if v.lower() in _BY_NAME:
        return _BY_NAME[v.lower()]
    base = v.replace("_", "-").split("-")[0].lower()
    return _BY_LOWER.get(base)
