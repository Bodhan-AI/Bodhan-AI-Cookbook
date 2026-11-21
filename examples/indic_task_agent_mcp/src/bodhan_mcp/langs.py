"""Language tables, name lookup and script-based language identification.

#Plan
- Mirror the Cookbook's scripts/bodhan_api_rules.json so the server knows, per model, which codes are valid.
- Resolve whatever a caller passes ("Tamil", "ta", "tamil") to one code, or fail with the supported list.
- Identify language by Unicode script (allowed by the brief); Devanagari and Arabic script are ambiguous,
  so return a best guess plus the alternatives instead of pretending to be sure.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

NAMES: dict[str, str] = {
    "as": "Assamese", "bhb": "Bhili", "bho": "Bhojpuri", "bn": "Bengali", "brx": "Bodo",
    "doi": "Dogri", "en": "English", "gu": "Gujarati", "hi": "Hindi", "kn": "Kannada",
    "kok": "Konkani", "ks": "Kashmiri", "ks-Deva": "Kashmiri (Devanagari)", "mai": "Maithili",
    "ml": "Malayalam", "mni": "Manipuri", "mni-Beng": "Manipuri (Bengali script)", "mr": "Marathi",
    "ne": "Nepali", "or": "Odia", "pa": "Punjabi", "sa": "Sanskrit", "sat": "Santali",
    "sd": "Sindhi", "sd-Arab": "Sindhi (Perso-Arabic)", "ta": "Tamil", "te": "Telugu", "ur": "Urdu",
}

TRANSCRIBE = {"as", "bhb", "bho", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "mai", "ml",
              "mni", "mr", "ne", "or", "pa", "sa", "sat", "sd", "ta", "te", "ur"}
TRANSLATE = {"as", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "ks-Deva", "mai", "ml", "mni",
             "mni-Beng", "mr", "ne", "or", "pa", "sa", "sat", "sd", "sd-Arab", "ta", "te", "ur"}
SPEAK = {"as", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "mai", "ml", "mni", "mr", "ne",
         "or", "pa", "sa", "sat", "sd", "ta", "te", "ur"}
# Indic-Speak's production tier (model card); the rest are preview quality.
SPEAK_PRODUCTION = {"en", "hi", "bn", "mr", "te", "ta", "gu", "kn", "ml", "or", "pa", "as", "ur"}

VOICES: dict[str, dict[str, list[str]]] = {
    "as": {"female": ["Prastuti"], "male": ["Ankur"]},
    "bn": {"female": ["Ishita"], "male": ["Sourav"]},
    "brx": {"female": ["Gwrbw"], "male": ["Sansuma"]},
    "doi": {"female": ["Preeti"], "male": ["Sham"]},
    "en": {"female": ["Kavya", "Anjali"], "male": ["Amit", "Adarsh"]},
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
ALL_VOICES: dict[str, str] = {}  # voice name -> gender
for _lang, _genders in VOICES.items():
    for _gender, _names in _genders.items():
        for _name in _names:
            ALL_VOICES[_name] = _gender

TTS_STYLES = ["AIR style news", "Customer Care", "TV style news", "advertisements", "anger", "children's stories",
              "disgust", "educational lecture", "fear", "happy", "news", "sad", "single person narration audiobook",
              "surprise"]

ALIASES: dict[str, str] = {
    "bangla": "bn", "oriya": "or", "odiya": "or", "panjabi": "pa", "meitei": "mni", "manipuri": "mni",
    "kashmiri": "ks", "sindhi": "sd", "santhali": "sat", "konkani": "kok", "maithili": "mai", "bodo": "brx",
    "dogri": "doi", "english": "en", "hindi": "hi", "tamil": "ta", "telugu": "te", "kannada": "kn",
    "malayalam": "ml", "marathi": "mr", "gujarati": "gu", "punjabi": "pa", "bengali": "bn", "assamese": "as",
    "urdu": "ur", "nepali": "ne", "sanskrit": "sa", "santali": "sat", "odia": "or", "bhojpuri": "bho", "bhili": "bhb",
}

# Languages people commonly ask for that no Bodhan model supports. Used for honest early refusal.
UNSUPPORTED_COMMON = {
    "french": "fr", "german": "de", "spanish": "es", "italian": "it", "portuguese": "pt", "russian": "ru",
    "chinese": "zh", "mandarin": "zh", "cantonese": "yue", "japanese": "ja", "korean": "ko", "arabic": "ar",
    "persian": "fa", "farsi": "fa", "turkish": "tr", "dutch": "nl", "swedish": "sv", "polish": "pl",
    "thai": "th", "vietnamese": "vi", "indonesian": "id", "malay": "ms", "swahili": "sw", "greek": "el",
    "hebrew": "he", "sinhala": "si", "sinhalese": "si", "burmese": "my", "tibetan": "bo", "khmer": "km",
    "ukrainian": "uk", "czech": "cs", "romanian": "ro", "hungarian": "hu", "finnish": "fi", "danish": "da",
    "norwegian": "no", "tagalog": "tl", "filipino": "tl",
}

MODEL_LANGS = {"transcribe": TRANSCRIBE, "translate": TRANSLATE, "speak": SPEAK}


def resolve(value: str | None) -> str | None:
    """Map a code or English name to a canonical code; None if unknown."""
    if not value:
        return None
    raw = value.strip()
    for code in NAMES:
        if raw.lower() == code.lower():
            return code
    low = raw.lower()
    if low in ALIASES:
        return ALIASES[low]
    for code, name in NAMES.items():
        if low == name.lower():
            return code
    base = low.split("-")[0].split("_")[0]  # tolerate region suffixes such as hi_IN
    if base in NAMES:
        return base
    return None


def name_of(code: str) -> str:
    return NAMES.get(code, code)


def supported_list(model: str) -> str:
    return ", ".join(f"{c} ({name_of(c)})" for c in sorted(MODEL_LANGS[model]))


def mentioned_languages(text: str) -> tuple[list[str], list[str]]:
    """Language names mentioned in free text: (supported codes, unsupported names)."""
    words = set(re.findall(r"[a-z]+", text.lower()))
    supported, unsupported = [], []
    for word in sorted(words):
        if word in ALIASES and ALIASES[word] not in supported:
            supported.append(ALIASES[word])
        elif word in UNSUPPORTED_COMMON:
            unsupported.append(word.capitalize())
    return supported, unsupported


# ---------------------------------------------------------------------------
# Script identification
# ---------------------------------------------------------------------------

SCRIPT_RANGES: list[tuple[int, int, str]] = [
    (0x0900, 0x097F, "Devanagari"), (0x0980, 0x09FF, "Bengali"), (0x0A00, 0x0A7F, "Gurmukhi"),
    (0x0A80, 0x0AFF, "Gujarati"), (0x0B00, 0x0B7F, "Oriya"), (0x0B80, 0x0BFF, "Tamil"),
    (0x0C00, 0x0C7F, "Telugu"), (0x0C80, 0x0CFF, "Kannada"), (0x0D00, 0x0D7F, "Malayalam"),
    (0x1C50, 0x1C7F, "Ol Chiki"), (0xABC0, 0xABFF, "Meetei Mayek"), (0x0600, 0x06FF, "Arabic"),
    (0x0750, 0x077F, "Arabic"), (0xFB50, 0xFDFF, "Arabic"), (0xFE70, 0xFEFF, "Arabic"),
]
INDIC_SCRIPTS = {s for _, _, s in SCRIPT_RANGES}
SCRIPT_DEFAULT_LANG = {
    "Devanagari": "hi", "Bengali": "bn", "Gurmukhi": "pa", "Gujarati": "gu", "Oriya": "or", "Tamil": "ta",
    "Telugu": "te", "Kannada": "kn", "Malayalam": "ml", "Ol Chiki": "sat", "Meetei Mayek": "mni", "Arabic": "ur",
    "Latin": "en",
}
SCRIPT_ALTERNATIVES = {
    "Devanagari": ["hi", "mr", "ne", "mai", "kok", "doi", "sa", "brx"],
    "Bengali": ["bn", "as", "mni-Beng"],
    "Arabic": ["ur", "ks", "sd-Arab"],
}
LANG_SCRIPT = {
    "hi": "Devanagari", "mr": "Devanagari", "ne": "Devanagari", "mai": "Devanagari", "kok": "Devanagari",
    "doi": "Devanagari", "sa": "Devanagari", "brx": "Devanagari", "ks-Deva": "Devanagari", "sd": "Devanagari",
    "bn": "Bengali", "as": "Bengali", "mni-Beng": "Bengali", "pa": "Gurmukhi", "gu": "Gujarati", "or": "Oriya",
    "ta": "Tamil", "te": "Telugu", "kn": "Kannada", "ml": "Malayalam", "sat": "Ol Chiki", "mni": "Meetei Mayek",
    "ur": "Arabic", "ks": "Arabic", "sd-Arab": "Arabic", "en": "Latin",
}

# Frequent function words that separate the common Devanagari languages.
_DEVANAGARI_MARKERS = {
    "hi": ["है", "हैं", "और", "में", "नहीं", "का", "की", "के", "यह", "था"],
    "mr": ["आहे", "आहेत", "आणि", "नाही", "हे", "होते", "च्या", "ळ"],
    "ne": ["छ", "छन्", "हुन्छ", "गर्न", "र", "थियो", "मा", "भएको", "गरेको"],
}


def char_script(ch: str) -> str | None:
    cp = ord(ch)
    for lo, hi, name in SCRIPT_RANGES:
        if lo <= cp <= hi:
            return name
    if ch.isalpha() and unicodedata.name(ch, "").startswith("LATIN"):
        return "Latin"
    return None


def script_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for ch in text:
        script = char_script(ch)
        if script:
            counts[script] = counts.get(script, 0) + 1
    return counts


def has_indic(text: str) -> bool:
    return any(char_script(ch) in INDIC_SCRIPTS for ch in text)


@dataclass
class LanguageGuess:
    language: str | None
    script: str | None
    confidence: float
    alternatives: list[str] = field(default_factory=list)
    note: str = ""

    def as_dict(self) -> dict:
        return {"language": self.language, "script": self.script, "confidence": round(self.confidence, 2),
                "alternatives": self.alternatives, "note": self.note}


def detect_language(text: str) -> LanguageGuess:
    counts = script_counts(text)
    letters = sum(counts.values())
    if letters == 0:
        return LanguageGuess(None, None, 0.0, note="no letters found")
    script, n = max(counts.items(), key=lambda kv: kv[1])
    share = n / letters
    lang = SCRIPT_DEFAULT_LANG.get(script)
    alternatives = [a for a in SCRIPT_ALTERNATIVES.get(script, []) if a != lang]
    confidence = share
    note = ""
    if script == "Devanagari":
        tokens = re.findall(r"[ऀ-ॿ]+", text)
        scores = {k: sum(tokens.count(w) for w in words) + (text.count("ळ") if k == "mr" else 0)
                  for k, words in _DEVANAGARI_MARKERS.items()}
        best = max(scores, key=lambda k: scores[k])
        if scores[best] > 0:
            lang = best
            total = sum(scores.values())
            confidence = share * (scores[best] / total)
        else:
            confidence = share * 0.5
        alternatives = [a for a in SCRIPT_ALTERNATIVES["Devanagari"] if a != lang]
        note = "Devanagari is shared by several languages; guess based on common function words."
    elif script == "Bengali":
        if "ৰ" in text or "ৱ" in text:
            lang, note = "as", "Assamese-specific letters found."
            alternatives = ["bn"]
        else:
            confidence = share * 0.85
            note = "Bengali script; Assamese is possible."
    elif script == "Arabic":
        confidence = share * 0.7
        note = "Perso-Arabic script; assumed Urdu (Kashmiri and Sindhi also use it)."
    return LanguageGuess(lang, script, confidence, alternatives, note)


def script_matches(text: str, lang: str) -> float:
    """Share of letters written in the script expected for `lang` (0..1)."""
    expected = LANG_SCRIPT.get(lang)
    counts = script_counts(text)
    letters = sum(counts.values())
    if not expected or letters == 0:
        return 0.0
    return counts.get(expected, 0) / letters
