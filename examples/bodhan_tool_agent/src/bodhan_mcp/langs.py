"""Languages, voices and script detection.

Language identification here uses only the Unicode script of the text, which
the assignment allows. Several languages share a script (Hindi and Marathi
both use Devanagari), so detection returns candidates, not one answer.
"""

from __future__ import annotations

from collections import Counter

from .errors import ToolError

LANGUAGE_NAMES = {
    "as": "Assamese", "bhb": "Bhili", "bho": "Bhojpuri", "bn": "Bengali", "brx": "Bodo", "doi": "Dogri",
    "en": "English", "gu": "Gujarati", "hi": "Hindi", "kn": "Kannada", "kok": "Konkani", "ks": "Kashmiri",
    "mai": "Maithili", "ml": "Malayalam", "mni": "Manipuri", "mr": "Marathi", "ne": "Nepali", "or": "Odia",
    "pa": "Punjabi", "sa": "Sanskrit", "sat": "Santali", "sd": "Sindhi", "ta": "Tamil", "te": "Telugu", "ur": "Urdu",
}

TRANSCRIBE_LANGS = set(LANGUAGE_NAMES)
TRANSLATE_LANGS = set(LANGUAGE_NAMES) - {"bhb", "bho"}

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
# English speech reuses the Hindi voices (the Cookbook's own example does this).
VOICES["en"] = VOICES["hi"]
SPEAK_LANGS = set(VOICES)

STYLES = [
    "AIR style news", "Customer Care", "TV style news", "advertisements", "anger", "children's stories", "disgust",
    "educational lecture", "fear", "happy", "news", "sad", "single person narration audiobook", "surprise",
]

# Unicode block ranges for each script.
SCRIPT_RANGES = {
    "Devanagari": [(0x0900, 0x097F)],
    "Bengali": [(0x0980, 0x09FF)],
    "Gurmukhi": [(0x0A00, 0x0A7F)],
    "Gujarati": [(0x0A80, 0x0AFF)],
    "Odia": [(0x0B00, 0x0B7F)],
    "Tamil": [(0x0B80, 0x0BFF)],
    "Telugu": [(0x0C00, 0x0C7F)],
    "Kannada": [(0x0C80, 0x0CFF)],
    "Malayalam": [(0x0D00, 0x0D7F)],
    "Arabic": [(0x0600, 0x06FF), (0x0750, 0x077F), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF)],
    "Ol Chiki": [(0x1C50, 0x1C7F)],
    "Meetei Mayek": [(0xABC0, 0xABFF), (0xAAE0, 0xAAFF)],
    "Latin": [(0x0041, 0x005A), (0x0061, 0x007A)],
}

# The script each language is normally written in.
LANG_SCRIPT = {
    "hi": "Devanagari", "mr": "Devanagari", "ne": "Devanagari", "sa": "Devanagari", "kok": "Devanagari",
    "mai": "Devanagari", "doi": "Devanagari", "brx": "Devanagari", "sd": "Devanagari", "bhb": "Devanagari",
    "bho": "Devanagari", "bn": "Bengali", "as": "Bengali", "pa": "Gurmukhi", "gu": "Gujarati", "or": "Odia",
    "ta": "Tamil", "te": "Telugu", "kn": "Kannada", "ml": "Malayalam", "ur": "Arabic", "ks": "Arabic",
    "sat": "Ol Chiki", "mni": "Meetei Mayek", "en": "Latin",
}

# When a script is shared, list the most widely spoken language first.
_PRIORITY = ["hi", "mr", "ne", "bn", "as", "ur", "ks"]


def script_of_char(ch: str) -> str | None:
    cp = ord(ch)
    for script, ranges in SCRIPT_RANGES.items():
        for lo, hi in ranges:
            if lo <= cp <= hi:
                return script
    return None


def detect_script(text: str) -> dict:
    """Return the dominant script, its share of letters, and candidate languages."""
    counts = Counter(s for s in map(script_of_char, text) if s)
    total = sum(counts.values())
    if not total:
        return {"script": None, "share": 0.0, "language_candidates": []}
    script, n = counts.most_common(1)[0]
    langs = [code for code, s in LANG_SCRIPT.items() if s == script]
    langs.sort(key=lambda c: (_PRIORITY.index(c) if c in _PRIORITY else len(_PRIORITY), c))
    return {"script": script, "share": round(n / total, 3), "language_candidates": langs}


def contains_indic(text: str) -> bool:
    """True if the text has any letter from an Indian-language script."""
    return any((s := script_of_char(ch)) and s != "Latin" for ch in text)


def normalize_language(value: str, supported: set[str], purpose: str) -> str:
    """Accept a code ('ta') or a name ('Tamil') and return the code, or raise."""
    raw = (value or "").strip()
    code = raw.lower().split("-")[0] if raw.lower().endswith("-in") else raw.lower()
    by_name = {name.lower(): c for c, name in LANGUAGE_NAMES.items()}
    code = by_name.get(code, code)
    if code in supported:
        return code
    listing = ", ".join(f"{c} ({LANGUAGE_NAMES[c]})" for c in sorted(supported))
    raise ToolError(
        "unsupported_language",
        f"'{raw}' is not supported for {purpose}.",
        hint=f"Supported languages: {listing}. If the task needs another language, it cannot be done with these tools.",
    )


def pick_voice(language: str, voice: str | None, gender: str | None) -> str:
    table = VOICES[language]
    if voice:
        for names in table.values():
            for name in names:
                if name.lower() == voice.strip().lower():
                    return name
        options = ", ".join(f"{n} ({g})" for g, names in table.items() for n in names)
        raise ToolError("invalid_arguments", f"Voice '{voice}' does not exist for language '{language}'.", hint=f"Choose one of: {options}.")
    return table[gender or "female"][0]
