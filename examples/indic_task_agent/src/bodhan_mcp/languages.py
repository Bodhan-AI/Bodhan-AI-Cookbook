"""Language, voice and script tables for the Bodhan models.

Values come from the Bodhan API reference (console.bodhan.ai/api-docs) and were
checked against live responses on 2026-11-19 (see docs/api_notes.md). Keep this
module the single source of truth: tools validate against it *before* spending
money, so an unsupported request fails fast with an actionable error.
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from dataclasses import dataclass

LANGUAGE_NAMES: dict[str, str] = {
    "as": "Assamese", "bhb": "Bhili", "bho": "Bhojpuri", "bn": "Bengali", "brx": "Bodo",
    "doi": "Dogri", "en": "English", "gu": "Gujarati", "hi": "Hindi", "kn": "Kannada",
    "kok": "Konkani", "ks": "Kashmiri (Perso-Arabic)", "ks-Deva": "Kashmiri (Devanagari)",
    "mai": "Maithili", "ml": "Malayalam", "mni": "Manipuri (Meitei Mayek)",
    "mni-Beng": "Manipuri (Bengali script)", "mr": "Marathi", "ne": "Nepali", "or": "Odia",
    "pa": "Punjabi", "sa": "Sanskrit", "sat": "Santali", "sd": "Sindhi (Devanagari)",
    "sd-Arab": "Sindhi (Perso-Arabic)", "ta": "Tamil", "te": "Telugu", "ur": "Urdu",
}

TRANSCRIBE_LANGUAGES = frozenset(
    "as bhb bho bn brx doi en gu hi kn kok ks mai ml mni mr ne or pa sa sat sd ta te ur".split()
)
TRANSLATE_LANGUAGES = frozenset(
    "as bn brx doi en gu hi kn kok ks ks-Deva mai ml mni mni-Beng mr ne or pa sa sat sd sd-Arab ta te ur".split()
)
SPEAK_LANGUAGES = frozenset(
    "as bn brx doi en gu hi kn kok ks mai ml mni mr ne or pa sa sat sd ta te ur".split()
)

# Fixed voice library (no cloning). Names are case-sensitive on the API.
VOICES: dict[str, dict[str, list[str]]] = {
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
ALL_VOICES: dict[str, tuple[str, str]] = {
    name: (lang, gender) for lang, by_gender in VOICES.items() for gender, names in by_gender.items() for name in names
}
# English has no native voice; the API lets any voice read any language. We use an
# Indian-English-friendly default and say so in the result.
ENGLISH_DEFAULT_VOICES = {"female": "Kavya", "male": "Amit"}

SPEAK_STYLES = frozenset([
    "AIR style news", "Customer Care", "TV style news", "advertisements", "anger", "children's stories",
    "disgust", "educational lecture", "fear", "happy", "news", "sad", "single person narration audiobook", "surprise",
])

# Common aliases planners and users type. Anything else must be an ISO code above.
_ALIASES = {name.lower(): code for code, name in LANGUAGE_NAMES.items()}
_ALIASES.update({
    "kashmiri": "ks", "manipuri": "mni", "meitei": "mni", "sindhi": "sd", "oriya": "or", "bangla": "bn",
    "punjabi": "pa", "panjabi": "pa", "english": "en", "santhali": "sat", "konkani": "kok", "bodo": "brx",
    "hindi": "hi", "tamil": "ta", "telugu": "te", "marathi": "mr", "bengali": "bn", "gujarati": "gu",
    "kannada": "kn", "malayalam": "ml", "urdu": "ur", "nepali": "ne", "assamese": "as", "odia": "or",
    "sanskrit": "sa", "maithili": "mai", "dogri": "doi", "bhojpuri": "bho", "bhili": "bhb",
})
# Languages people commonly ask for that Bodhan does not support. Mapped so errors can
# name the language instead of echoing an opaque code.
KNOWN_UNSUPPORTED = {
    "fr": "French", "french": "French", "de": "German", "german": "German", "es": "Spanish", "spanish": "Spanish",
    "zh": "Chinese", "chinese": "Chinese", "ja": "Japanese", "japanese": "Japanese", "ar": "Arabic", "arabic": "Arabic",
    "ru": "Russian", "russian": "Russian", "pt": "Portuguese", "portuguese": "Portuguese", "it": "Italian",
    "italian": "Italian", "ko": "Korean", "korean": "Korean", "tulu": "Tulu", "tcy": "Tulu", "si": "Sinhala",
    "sinhala": "Sinhala", "bhojpuri-translation": "Bhojpuri",
}


def normalize_language(value: str) -> str:
    """Map a code or English name ('Tamil', 'ta', 'ta-IN') to a Bodhan code, or return it unchanged."""
    v = (value or "").strip()
    if not v:
        return v
    if v in LANGUAGE_NAMES:
        return v
    low = v.lower()
    if low in _ALIASES:
        return _ALIASES[low]
    if "-" in v:  # BCP-47 style region tags such as hi-IN
        head = v.split("-")[0].lower()
        if head in LANGUAGE_NAMES:
            return head
    return low


def language_label(code: str) -> str:
    return f"{LANGUAGE_NAMES.get(code, KNOWN_UNSUPPORTED.get(code, code))} ({code})"


def supported_list(codes: frozenset[str]) -> str:
    return ", ".join(sorted(codes))


# ---------------------------------------------------------------------------
# Script detection (character-set based language identification, allowed by the brief)
# ---------------------------------------------------------------------------

SCRIPT_RANGES: list[tuple[int, int, str]] = [
    (0x0900, 0x097F, "Devanagari"), (0x0980, 0x09FF, "Bengali"), (0x0A00, 0x0A7F, "Gurmukhi"),
    (0x0A80, 0x0AFF, "Gujarati"), (0x0B00, 0x0B7F, "Odia"), (0x0B80, 0x0BFF, "Tamil"),
    (0x0C00, 0x0C7F, "Telugu"), (0x0C80, 0x0CFF, "Kannada"), (0x0D00, 0x0D7F, "Malayalam"),
    (0x1C50, 0x1C7F, "OlChiki"), (0xABC0, 0xABFF, "MeeteiMayek"), (0xAAE0, 0xAAFF, "MeeteiMayek"),
    (0x0600, 0x06FF, "Arabic"), (0x0750, 0x077F, "Arabic"), (0xFB50, 0xFDFF, "Arabic"), (0xFE70, 0xFEFF, "Arabic"),
    (0x0400, 0x04FF, "Cyrillic"), (0x0370, 0x03FF, "Greek"), (0x4E00, 0x9FFF, "Han"), (0x3040, 0x30FF, "Kana"),
    (0xAC00, 0xD7AF, "Hangul"), (0x0E00, 0x0E7F, "Thai"), (0x0590, 0x05FF, "Hebrew"), (0x0D80, 0x0DFF, "Sinhala"),
]
INDIC_SCRIPTS = frozenset({
    "Devanagari", "Bengali", "Gurmukhi", "Gujarati", "Odia", "Tamil", "Telugu", "Kannada", "Malayalam",
    "OlChiki", "MeeteiMayek", "Arabic",
})
SCRIPT_TO_LANGS: dict[str, list[str]] = {
    "Devanagari": ["hi", "mr", "ne", "mai", "sa", "doi", "kok", "brx", "bho", "sd", "ks-Deva"],
    "Bengali": ["bn", "as", "mni-Beng"], "Gurmukhi": ["pa"], "Gujarati": ["gu"], "Odia": ["or"], "Tamil": ["ta"],
    "Telugu": ["te"], "Kannada": ["kn"], "Malayalam": ["ml"], "OlChiki": ["sat"], "MeeteiMayek": ["mni"],
    "Arabic": ["ur", "ks", "sd-Arab"], "Latin": ["en"],
}
LANG_TO_SCRIPT: dict[str, str] = {}
for _script, _langs in SCRIPT_TO_LANGS.items():
    for _l in _langs:
        LANG_TO_SCRIPT.setdefault(_l, _script)

# Very small function-word lists to separate the commonest Devanagari languages.
_DEVANAGARI_HINTS = {
    "hi": {"है", "हैं", "और", "के", "की", "में", "से", "को", "नहीं", "यह", "था", "थी", "लिए", "पर", "कि"},
    "mr": {"आहे", "आहेत", "आणि", "नाही", "हे", "या", "मध्ये", "होते", "आम्ही", "तुम्ही", "करा", "व", "त्या", "चे", "ची"},
    "ne": {"छ", "छन्", "र", "मा", "हो", "थियो", "गर्न", "भएको", "पनि", "हुन्छ", "गरेको", "लागि", "यो", "तर"},
}


def script_of_char(ch: str) -> str | None:
    cp = ord(ch)
    for lo, hi, name in SCRIPT_RANGES:
        if lo <= cp <= hi:
            return name
    if ch.isalpha() and cp < 0x0250:
        return "Latin"
    return None


def script_profile(text: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    for ch in text:
        if ch.isspace() or not (ch.isalpha() or unicodedata.category(ch).startswith("M")):
            continue
        s = script_of_char(ch)
        if s:
            counts[s] += 1
    return counts


def indic_fraction(text: str) -> float:
    prof = script_profile(text)
    total = sum(prof.values())
    if not total:
        return 0.0
    return sum(v for k, v in prof.items() if k in INDIC_SCRIPTS) / total


@dataclass
class LanguageGuess:
    language: str | None  # Bodhan code, or None when no letters / unsupported script
    script: str | None
    confidence: float  # 0..1, share of letters in the dominant script, reduced when ambiguous
    candidates: list[str]
    note: str

    def as_dict(self) -> dict:
        return {"language": self.language, "language_name": LANGUAGE_NAMES.get(self.language or "", None),
                "script": self.script, "confidence": round(self.confidence, 2), "candidates": self.candidates,
                "note": self.note}


def detect_language(text: str) -> LanguageGuess:
    """Guess the language of `text` from its script, plus function words for Devanagari/Bengali."""
    prof = script_profile(text)
    total = sum(prof.values())
    if total < 3:
        return LanguageGuess(None, None, 0.0, [], "too little text to identify a language")
    script, n = prof.most_common(1)[0]
    share = n / total
    candidates = SCRIPT_TO_LANGS.get(script)
    if not candidates:
        return LanguageGuess(None, script, share, [], f"script {script} is not used by any Bodhan-supported language")
    if len(candidates) == 1:
        return LanguageGuess(candidates[0], script, share, candidates, f"identified by {script} script")
    words = {w.strip(".,;:!?।॥\"'()[]") for w in text.split()}
    if script == "Devanagari":
        scores = {lang: len(words & hints) for lang, hints in _DEVANAGARI_HINTS.items()}
        best = max(scores, key=scores.get)
        if scores[best] == 0:
            return LanguageGuess("hi", script, share * 0.5, candidates,
                                 "Devanagari script; no function-word evidence, assumed Hindi (could be Marathi, Nepali, ...)")
        ranked = sorted(scores.values(), reverse=True)
        margin = (ranked[0] - ranked[1]) / max(ranked[0], 1)
        return LanguageGuess(best, script, share * (0.6 + 0.4 * margin), candidates,
                             f"Devanagari script; function words suggest {LANGUAGE_NAMES[best]} ({scores})")
    if script == "Bengali":
        if any(ch in text for ch in "ৰৱ"):  # ৰ ৱ are Assamese-only letters
            return LanguageGuess("as", script, share * 0.9, candidates, "Bengali-Assamese script with Assamese-only letters ৰ/ৱ")
        return LanguageGuess("bn", script, share * 0.8, candidates, "Bengali-Assamese script without Assamese-only letters")
    if script == "Arabic":
        return LanguageGuess("ur", script, share * 0.6, candidates, "Perso-Arabic script, assumed Urdu (could be Kashmiri or Sindhi)")
    if script == "Latin":
        return LanguageGuess("en", script, share * 0.7, candidates, "Latin script, assumed English (could be romanized Indic)")
    return LanguageGuess(candidates[0], script, share * 0.5, candidates, f"{script} script, ambiguous")


def script_matches_language(text: str, lang: str) -> float:
    """Share of letters in `text` written in the script expected for `lang` (1.0 = all)."""
    expected = LANG_TO_SCRIPT.get(lang)
    prof = script_profile(text)
    total = sum(prof.values())
    if not expected or not total:
        return 0.0
    return prof.get(expected, 0) / total
