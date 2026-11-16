"""Language tables for the Bodhan models, plus script-based language identification.

Tables mirror https://console.bodhan.ai/api-docs/ (and the Cookbook's
scripts/bodhan_api_rules.json). Language ID is by Unicode script and a few
marker characters and words. It is deterministic and costs nothing, which
the ground rules explicitly allow ("language-identification by script or
character set").
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

LANGUAGE_NAMES: dict[str, str] = {
    "as": "Assamese", "bhb": "Bhili", "bho": "Bhojpuri", "bn": "Bengali", "brx": "Bodo",
    "doi": "Dogri", "en": "English", "gu": "Gujarati", "hi": "Hindi", "kn": "Kannada",
    "kok": "Konkani", "ks": "Kashmiri (Perso-Arabic)", "ks-Deva": "Kashmiri (Devanagari)",
    "mai": "Maithili", "ml": "Malayalam", "mni": "Manipuri (Meitei Mayek)",
    "mni-Beng": "Manipuri (Bengali script)", "mr": "Marathi", "ne": "Nepali", "or": "Odia",
    "pa": "Punjabi", "sa": "Sanskrit", "sat": "Santali", "sd": "Sindhi (Devanagari)",
    "sd-Arab": "Sindhi (Perso-Arabic)", "ta": "Tamil", "te": "Telugu", "ur": "Urdu",
}

TRANSCRIBE_LANGS = frozenset(
    "as bhb bho bn brx doi en gu hi kn kok ks mai ml mni mr ne or pa sa sat sd ta te ur".split()
)
TRANSLATE_LANGS = frozenset(
    "as bn brx doi en gu hi kn kok ks ks-Deva mai ml mni mni-Beng mr ne or pa sa sat sd sd-Arab ta te ur".split()
)
SPEAK_LANGS = frozenset("as bn brx doi en gu hi kn kok ks mai ml mni mr ne or pa sa sat sd ta te ur".split())
CODEMIX_UNSUPPORTED = frozenset({"en", "ks-Deva", "sd-Arab"})

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
    v: (lang, gender) for lang, g in VOICES.items() for gender, names in g.items() for v in names
}
# English has no voices of its own; any voice can read any language. Use the Hindi ones.
DEFAULT_VOICE_LANG_FOR = {"en": "hi"}

STYLES = (
    "AIR style news", "Customer Care", "TV style news", "advertisements", "anger", "children's stories",
    "disgust", "educational lecture", "fear", "happy", "news", "sad", "single person narration audiobook",
    "surprise",
)

# Common names people use for languages -> codes (for tools that accept either).
_ALIASES = {name.lower().split(" (")[0]: code for code, name in LANGUAGE_NAMES.items() if "-" not in code}
_ALIASES.update({
    "oriya": "or", "bangla": "bn", "panjabi": "pa", "manipuri": "mni", "meitei": "mni", "kashmiri": "ks",
    "sindhi": "sd", "english": "en",
})


def normalize_lang(value: str | None) -> str | None:
    """Accept 'hi', 'HI', 'Hindi', or a regional tag -> 'hi'. Keeps API script suffixes (ks-Deva). None if unknown."""
    if value is None:
        return None
    v = value.strip()
    if not v:
        return None
    for code in LANGUAGE_NAMES:
        if v.lower() == code.lower():
            return code
    low = v.lower()
    if low in _ALIASES:
        return _ALIASES[low]
    base = low.split("-")[0].split("_")[0]
    if base in LANGUAGE_NAMES:
        return base
    return None


def lang_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code, code)


# ---------------------------------------------------------------------------
# Script detection
# ---------------------------------------------------------------------------

_SCRIPT_RANGES: list[tuple[int, int, str]] = [
    (0x0900, 0x097F, "Devanagari"), (0xA8E0, 0xA8FF, "Devanagari"),
    (0x0980, 0x09FF, "Bengali"),
    (0x0A00, 0x0A7F, "Gurmukhi"),
    (0x0A80, 0x0AFF, "Gujarati"),
    (0x0B00, 0x0B7F, "Odia"),
    (0x0B80, 0x0BFF, "Tamil"),
    (0x0C00, 0x0C7F, "Telugu"),
    (0x0C80, 0x0CFF, "Kannada"),
    (0x0D00, 0x0D7F, "Malayalam"),
    (0x0600, 0x06FF, "Arabic"), (0x0750, 0x077F, "Arabic"), (0xFB50, 0xFDFF, "Arabic"), (0xFE70, 0xFEFF, "Arabic"),
    (0x1C50, 0x1C7F, "OlChiki"),
    (0xABC0, 0xABFF, "MeeteiMayek"), (0xAAE0, 0xAAFF, "MeeteiMayek"),
    (0x0400, 0x04FF, "Cyrillic"),
    (0x4E00, 0x9FFF, "Han"), (0x3040, 0x30FF, "Kana"), (0xAC00, 0xD7AF, "Hangul"),
    (0x0E00, 0x0E7F, "Thai"), (0x0370, 0x03FF, "Greek"), (0x0590, 0x05FF, "Hebrew"),
]
INDIC_SCRIPTS = frozenset(
    {"Devanagari", "Bengali", "Gurmukhi", "Gujarati", "Odia", "Tamil", "Telugu", "Kannada", "Malayalam",
     "Arabic", "OlChiki", "MeeteiMayek"}
)
_SCRIPT_TO_LANG = {
    "Gurmukhi": "pa", "Gujarati": "gu", "Odia": "or", "Tamil": "ta", "Telugu": "te", "Kannada": "kn",
    "Malayalam": "ml", "OlChiki": "sat", "MeeteiMayek": "mni",
}
_UNSUPPORTED_SCRIPTS = {"Cyrillic", "Han", "Kana", "Hangul", "Thai", "Greek", "Hebrew"}


def char_script(ch: str) -> str | None:
    o = ord(ch)
    if ("a" <= ch <= "z") or ("A" <= ch <= "Z") or 0x00C0 <= o <= 0x024F:
        return "Latin"
    for lo, hi, name in _SCRIPT_RANGES:
        if lo <= o <= hi:
            return name
    return None


def script_shares(text: str) -> dict[str, float]:
    counts: Counter[str] = Counter()
    for ch in text:
        if ch.isalpha() or unicodedata.category(ch).startswith("M"):
            s = char_script(ch)
            if s:
                counts[s] += 1
    total = sum(counts.values())
    return {s: round(c / total, 4) for s, c in counts.most_common()} if total else {}


def contains_indic(text: str, min_chars: int = 1) -> bool:
    n = 0
    for ch in text:
        s = char_script(ch)
        if s in INDIC_SCRIPTS:
            n += 1
            if n >= min_chars:
                return True
    return False


_EN_STOPWORDS = frozenset(
    "the a an and or of to in is are was were be been it this that for on with as by at from not but "
    "have has had you your we our they their he she his her i my me do does did can will would should "
    "there what which who how when where if so than then these those into about".split()
)
# Indic vowel signs and viramas are combining marks, which \w does not match; without the explicit
# ranges every Devanagari word would be split at its first matra and marker words could never match.
_WORD = re.compile(r"(?:[^\W\d_]|[ऀ-෿؀-ۿ᱐-᱿ꫠ-꫿ꯀ-꯿])+", re.UNICODE)
_DEVA_MARKERS = {
    "mr": {"आहे", "आहेत", "आणि", "नाही", "होते", "करा", "मध्ये", "त्यांनी", "आम्ही", "तुम्ही", "होता", "केले"},
    "ne": {"छ", "छन्", "हुन्छ", "गर्न", "भएको", "थियो", "हुने", "गर्ने", "पनि", "तपाईं", "छैन"},
    "sa": {"अस्ति", "इति", "तस्य", "सः", "भवति", "एव", "अहम्", "त्वम्", "वयम्"},
    "hi": {"है", "हैं", "और", "का", "की", "के", "में", "नहीं", "था", "थे", "यह", "वह", "से", "को", "पर"},
    "mai": {"छथि", "अछि", "छल", "हमरा", "अहाँ"},
}


@dataclass
class LangGuess:
    language: str | None  # best guess code, None if no supported language detected
    script: str | None
    confidence: float  # 0..1, rough
    candidates: list[str] = field(default_factory=list)
    script_shares: dict[str, float] = field(default_factory=dict)
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "language": self.language,
            "language_name": lang_name(self.language) if self.language else None,
            "script": self.script,
            "confidence": round(self.confidence, 2),
            "candidates": self.candidates,
            "script_shares": self.script_shares,
            "note": self.note,
        }


def detect_language(text: str) -> LangGuess:
    """Guess the language of `text` from its script, with marker words for shared scripts."""
    shares = script_shares(text)
    if not shares:
        return LangGuess(None, None, 0.0, [], {}, "no letters found")
    script, share = next(iter(shares.items()))
    words = [w.lower() for w in _WORD.findall(text)]

    if script in _UNSUPPORTED_SCRIPTS:
        return LangGuess(None, script, share, [], shares, f"{script} script: not a language Bodhan models support")
    if script in _SCRIPT_TO_LANG:
        code = _SCRIPT_TO_LANG[script]
        return LangGuess(code, script, share, [code], shares)
    if script == "Bengali":
        if any(ch in text for ch in "ৰৱ"):  # ৰ ৱ only occur in Assamese
            return LangGuess("as", script, share, ["as", "bn"], shares)
        return LangGuess("bn", script, share * 0.9, ["bn", "as", "mni-Beng"], shares,
                         "Bengali script; Assamese/Manipuri also possible")
    if script == "Arabic":
        if any(ch in text for ch in "ڄٻڀٽڏڙڳڻڱ"):
            return LangGuess("sd-Arab", script, share, ["sd-Arab", "ur"], shares)
        if any(ch in text for ch in "ۄٲٳ"):
            return LangGuess("ks", script, share, ["ks", "ur"], shares)
        return LangGuess("ur", script, share * 0.85, ["ur", "ks", "sd-Arab"], shares,
                         "Perso-Arabic script; assumed Urdu (Arabic/Persian are not supported)")
    if script == "Devanagari":
        wset = set(words)
        scores = {lang: len(wset & markers) for lang, markers in _DEVA_MARKERS.items()}
        best = max(scores, key=lambda k: scores[k])
        if scores[best] == 0:
            return LangGuess("hi", script, share * 0.6, ["hi", "mr", "ne", "sa", "mai", "kok", "doi", "brx"], shares,
                             "Devanagari script; no marker words, assumed Hindi")
        ranked = sorted(scores, key=lambda k: -scores[k])
        conf = share * (0.6 + 0.4 * scores[best] / max(1, sum(scores.values())))
        return LangGuess(best, script, conf, [r for r in ranked if scores[r] > 0] or [best], shares)
    if script == "Latin":
        if not words:
            return LangGuess("en", script, 0.3, ["en"], shares)
        en_ratio = sum(w in _EN_STOPWORDS for w in words) / len(words)
        if len(words) >= 8 and en_ratio < 0.08:
            return LangGuess(None, script, share, ["en"], shares,
                             "Latin script but few English function words: another Latin-script language "
                             "or romanized Indic text")
        return LangGuess("en", script, share * min(1.0, 0.5 + en_ratio * 2), ["en"], shares)
    return LangGuess(None, script, share, [], shares, f"unrecognised script {script}")


# Common languages Bodhan does not support, so refusals can name what was asked for.
FOREIGN_LANGUAGE_NAMES: dict[str, str] = {
    "fr": "French", "de": "German", "es": "Spanish", "pt": "Portuguese", "it": "Italian", "nl": "Dutch",
    "ru": "Russian", "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "ar": "Arabic", "fa": "Persian",
    "tr": "Turkish", "th": "Thai", "vi": "Vietnamese", "id": "Indonesian", "ms": "Malay", "si": "Sinhala",
    "my": "Burmese", "sw": "Swahili", "pl": "Polish", "uk": "Ukrainian", "el": "Greek", "he": "Hebrew",
    "tl": "Tagalog", "bo": "Tibetan", "dz": "Dzongkha", "tcy": "Tulu", "kha": "Khasi", "lus": "Mizo",
}
_FOREIGN_BY_NAME = {name.lower(): code for code, name in FOREIGN_LANGUAGE_NAMES.items()}
_FOREIGN_BY_NAME.update({"mandarin": "zh", "farsi": "fa", "castilian": "es"})


def describe_language_request(value: str) -> str:
    """A readable name for whatever the caller asked for, supported or not."""
    v = value.strip()
    code = normalize_lang(v)
    if code:
        return f"{lang_name(code)} ('{code}')"
    low = v.lower()
    base = low.split("-")[0].split("_")[0]
    if base in FOREIGN_LANGUAGE_NAMES:
        return f"{FOREIGN_LANGUAGE_NAMES[base]} ('{base}')"
    if low in _FOREIGN_BY_NAME:
        return f"{v.title()} ('{_FOREIGN_BY_NAME[low]}')"
    return f"'{v}'"


def supported_list(codes: frozenset[str] | set[str]) -> str:
    return ", ".join(f"{c} ({lang_name(c)})" for c in sorted(codes))
