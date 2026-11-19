from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class LanguageInfo:
    code: str
    name: str
    script: str
    unicode_range: Optional[Tuple[int, int]]
    tts_production_tier: bool = True


SUPPORTED_LANGUAGES: Dict[str, LanguageInfo] = {
    "en": LanguageInfo("en", "English", "Latin", (0x0041, 0x007A), True),
    "hi": LanguageInfo("hi", "Hindi", "Devanagari", (0x0900, 0x097F), True),
    "bn": LanguageInfo("bn", "Bengali", "Bengali", (0x0980, 0x09FF), True),
    "ta": LanguageInfo("ta", "Tamil", "Tamil", (0x0B80, 0x0BFF), True),
    "te": LanguageInfo("te", "Telugu", "Telugu", (0x0C00, 0x0C7F), True),
    "mr": LanguageInfo("mr", "Marathi", "Devanagari", (0x0900, 0x097F), True),
    "gu": LanguageInfo("gu", "Gujarati", "Gujarati", (0x0A80, 0x0AFF), True),
    "kn": LanguageInfo("kn", "Kannada", "Kannada", (0x0C80, 0x0CFF), True),
    "ml": LanguageInfo("ml", "Malayalam", "Malayalam", (0x0D00, 0x0D7F), True),
    "pa": LanguageInfo("pa", "Punjabi", "Gurmukhi", (0x0A00, 0x0A7F), True),
    "or": LanguageInfo("or", "Odia", "Odia", (0x0B00, 0x0B7F), True),
    "as": LanguageInfo("as", "Assamese", "Bengali-Assamese", (0x0980, 0x09FF), True),
    "ur": LanguageInfo("ur", "Urdu", "Perso-Arabic", (0x0600, 0x06FF), True),
    "sa": LanguageInfo("sa", "Sanskrit", "Devanagari", (0x0900, 0x097F), False),
    "ne": LanguageInfo("ne", "Nepali", "Devanagari", (0x0900, 0x097F), False),
    "sd": LanguageInfo("sd", "Sindhi", "Devanagari/Perso-Arabic", (0x0900, 0x097F), False),
    "ks": LanguageInfo("ks", "Kashmiri", "Perso-Arabic", (0x0600, 0x06FF), False),
    "kok": LanguageInfo("kok", "Konkani", "Devanagari", (0x0900, 0x097F), False),
    "doi": LanguageInfo("doi", "Dogri", "Devanagari", (0x0900, 0x097F), False),
    "mai": LanguageInfo("mai", "Maithili", "Devanagari", (0x0900, 0x097F), False),
    "mni": LanguageInfo("mni", "Manipuri", "Bengali/Meetei Mayek", (0x0980, 0x09FF), False),
    "brx": LanguageInfo("brx", "Bodo", "Devanagari", (0x0900, 0x097F), False),
    "sat": LanguageInfo("sat", "Santali", "Ol Chiki", (0x1C50, 0x1C7F), False),
}

LANGUAGE_ALIASES: Dict[str, str] = {
    "english": "en",
    "eng": "en",
    "hindi": "hi",
    "hin": "hi",
    "bengali": "bn",
    "bangla": "bn",
    "ben": "bn",
    "tamil": "ta",
    "tam": "ta",
    "telugu": "te",
    "tel": "te",
    "marathi": "mr",
    "mar": "mr",
    "gujarati": "gu",
    "guj": "gu",
    "kannada": "kn",
    "kan": "kn",
    "malayalam": "ml",
    "mal": "ml",
    "punjabi": "pa",
    "panjabi": "pa",
    "pan": "pa",
    "odia": "or",
    "oriya": "or",
    "ori": "or",
    "assamese": "as",
    "asm": "as",
    "urdu": "ur",
    "urd": "ur",
    "sanskrit": "sa",
    "san": "sa",
    "nepali": "ne",
    "nep": "ne",
    "sindhi": "sd",
    "snd": "sd",
    "kashmiri": "ks",
    "kas": "ks",
    "konkani": "kok",
    "dogri": "doi",
    "maithili": "mai",
    "manipuri": "mni",
    "meitei": "mni",
    "bodo": "brx",
    "santali": "sat",
}

UNSUPPORTED_LANGUAGES: Dict[str, str] = {
    "fr": "French",
    "french": "French",
    "es": "Spanish",
    "spanish": "Spanish",
    "de": "German",
    "german": "German",
    "zh": "Chinese",
    "chinese": "Chinese",
    "mandarin": "Chinese",
    "ja": "Japanese",
    "japanese": "Japanese",
    "ko": "Korean",
    "korean": "Korean",
    "ru": "Russian",
    "russian": "Russian",
    "ar": "Arabic",
    "arabic": "Arabic",
    "it": "Italian",
    "italian": "Italian",
    "pt": "Portuguese",
    "portuguese": "Portuguese",
    "nl": "Dutch",
    "dutch": "Dutch",
    "tr": "Turkish",
    "turkish": "Turkish",
    "vi": "Vietnamese",
    "vietnamese": "Vietnamese",
    "th": "Thai",
    "thai": "Thai",
    "id": "Indonesian",
    "indonesian": "Indonesian",
    "pl": "Polish",
    "polish": "Polish",
}

VOICE_LIBRARY: Dict[str, Dict[str, str]] = {
    "as": {"female": "Prastuti", "male": "Ankur"},
    "bn": {"female": "Ishita", "male": "Sourav"},
    "brx": {"female": "Gwrbw", "male": "Sansuma"},
    "doi": {"female": "Preeti", "male": "Sham"},
    "gu": {"female": "Dhara", "male": "Parth"},
    "hi": {"female": "Kavya", "male": "Amit"},
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
    "en": {"female": "Kavya", "male": "Amit"},
}

SPEAKING_STYLES: List[str] = [
    "educational lecture",
    "single person narration audiobook",
    "news",
    "AIR style news",
    "TV style news",
    "children's stories",
    "Customer Care",
    "advertisements",
    "happy",
    "sad",
    "anger",
    "fear",
    "surprise",
    "disgust",
]


def normalize_language_code(lang: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not lang:
        return None, "Language code was not provided."
    cleaned = lang.strip().lower()
    if cleaned in ("auto", "detect", "unknown"):
        return "auto", None
    if cleaned in SUPPORTED_LANGUAGES:
        return cleaned, None
    if cleaned in LANGUAGE_ALIASES:
        return LANGUAGE_ALIASES[cleaned], None
    if cleaned in UNSUPPORTED_LANGUAGES:
        lang_name = UNSUPPORTED_LANGUAGES[cleaned]
        supported_str = ", ".join(f"{info.name} ({code})" for code, info in list(SUPPORTED_LANGUAGES.items())[:13])
        return (
            None,
            f"Unsupported language '{lang_name}' ('{lang}'). Bodhan models only support "
            f"English and the 22 scheduled Indian languages (such as {supported_str}). "
            f"Cannot fabricate output for unsupported languages.",
        )
    return (
        None,
        f"Unrecognized or unsupported language '{lang}'. Supported codes: {', '.join(SUPPORTED_LANGUAGES.keys())}.",
    )


def select_voice(
    language_code: str,
    voice: Optional[str] = None,
    gender: Optional[str] = None,
) -> Tuple[str, str]:
    lang_voices = VOICE_LIBRARY.get(language_code, VOICE_LIBRARY["hi"])

    if voice:
        v_clean = voice.strip().lower()
        if v_clean in ("female", "woman", "girl"):
            return lang_voices["female"], "female"
        if v_clean in ("male", "man", "boy"):
            return lang_voices["male"], "male"
        for l_code, mapping in VOICE_LIBRARY.items():
            for g, v_name in mapping.items():
                if v_name.lower() == v_clean:
                    return v_name, g

    g_clean = (gender or "female").strip().lower()
    if g_clean in ("male", "m", "man"):
        return lang_voices["male"], "male"
    return lang_voices["female"], "female"


def contains_indic_script(text: str) -> bool:
    for ch in text:
        cp = ord(ch)
        if (0x0900 <= cp <= 0x0D7F) or (0x1C50 <= cp <= 0x1C7F) or (0x0600 <= cp <= 0x06FF):
            return True
    return False


def detect_language_from_text(text: str) -> Dict[str, object]:
    if not text or not text.strip():
        return {"language_code": "unknown", "language_name": "Unknown", "confidence": 0.0, "script": "None"}

    script_counts: Dict[str, int] = {
        "Devanagari": 0,
        "Bengali": 0,
        "Gurmukhi": 0,
        "Gujarati": 0,
        "Odia": 0,
        "Tamil": 0,
        "Telugu": 0,
        "Kannada": 0,
        "Malayalam": 0,
        "Perso-Arabic": 0,
        "Ol Chiki": 0,
        "Latin": 0,
    }

    for ch in text:
        cp = ord(ch)
        if 0x0900 <= cp <= 0x097F:
            script_counts["Devanagari"] += 1
        elif 0x0980 <= cp <= 0x09FF:
            script_counts["Bengali"] += 1
        elif 0x0A00 <= cp <= 0x0A7F:
            script_counts["Gurmukhi"] += 1
        elif 0x0A80 <= cp <= 0x0AFF:
            script_counts["Gujarati"] += 1
        elif 0x0B00 <= cp <= 0x0B7F:
            script_counts["Odia"] += 1
        elif 0x0B80 <= cp <= 0x0BFF:
            script_counts["Tamil"] += 1
        elif 0x0C00 <= cp <= 0x0C7F:
            script_counts["Telugu"] += 1
        elif 0x0C80 <= cp <= 0x0CFF:
            script_counts["Kannada"] += 1
        elif 0x0D00 <= cp <= 0x0D7F:
            script_counts["Malayalam"] += 1
        elif 0x0600 <= cp <= 0x06FF:
            script_counts["Perso-Arabic"] += 1
        elif 0x1C50 <= cp <= 0x1C7F:
            script_counts["Ol Chiki"] += 1
        elif (0x0041 <= cp <= 0x005A) or (0x0061 <= cp <= 0x007A):
            script_counts["Latin"] += 1

    total_alpha = sum(script_counts.values())
    if total_alpha == 0:
        return {"language_code": "unknown", "language_name": "Unknown", "confidence": 0.0, "script": "Numeric/Symbols"}

    dominant_script, dom_count = max(script_counts.items(), key=lambda kv: kv[1])
    confidence = round(dom_count / max(1, total_alpha), 3)

    direct_script_map = {
        "Tamil": ("ta", "Tamil"),
        "Telugu": ("te", "Telugu"),
        "Kannada": ("kn", "Kannada"),
        "Malayalam": ("ml", "Malayalam"),
        "Gujarati": ("gu", "Gujarati"),
        "Gurmukhi": ("pa", "Punjabi"),
        "Odia": ("or", "Odia"),
        "Ol Chiki": ("sat", "Santali"),
        "Perso-Arabic": ("ur", "Urdu"),
    }

    if dominant_script in direct_script_map:
        code, name = direct_script_map[dominant_script]
        return {
            "language_code": code,
            "language_name": name,
            "confidence": confidence,
            "script": dominant_script,
        }

    if dominant_script == "Bengali":
        if "\u09f0" in text or "\u09f1" in text or any(w in text for w in ("আৰু", "লগত", "শিকিম")):
            return {
                "language_code": "as",
                "language_name": "Assamese",
                "confidence": confidence,
                "script": "Bengali-Assamese",
            }
        return {"language_code": "bn", "language_name": "Bengali", "confidence": confidence, "script": "Bengali"}

    if dominant_script == "Devanagari":
        tokens = [t.strip("।॥,.!?;:()[]\"'") for t in text.split() if t.strip()]
        marathi_words = {"आहे", "आहेत", "आणि", "नाही", "आपण", "ती", "या", "दिली"}
        marathi_substrings = ("मध्ये", "च्या", "तील", "साठी", "\u0933")
        hindi_words = {"है", "हैं", "था", "थी", "और", "में", "के", "की", "का", "नहीं", "हम", "आज"}
        nepali_words = {"छ", "छन्", "हुन्छ", "गर्नु", "भने", "लागि"}
        sanskrit_words = {"इति", "अस्ति", "एव", "च", "भवति"}

        marathi_score = sum(1 for t in tokens if t in marathi_words) + sum(
            1 for sub in marathi_substrings if sub in text
        )
        hindi_score = sum(1 for t in tokens if t in hindi_words)
        nepali_score = sum(1 for t in tokens if t in nepali_words)
        sanskrit_score = sum(1 for t in tokens if t in sanskrit_words) + text.count("ः")

        if marathi_score > hindi_score and marathi_score >= 1:
            return {"language_code": "mr", "language_name": "Marathi", "confidence": confidence, "script": "Devanagari"}
        if nepali_score > hindi_score and nepali_score >= 1:
            return {"language_code": "ne", "language_name": "Nepali", "confidence": confidence, "script": "Devanagari"}
        if sanskrit_score > hindi_score + 2:
            return {
                "language_code": "sa",
                "language_name": "Sanskrit",
                "confidence": confidence,
                "script": "Devanagari",
            }
        return {"language_code": "hi", "language_name": "Hindi", "confidence": confidence, "script": "Devanagari"}

    return {
        "language_code": "en",
        "language_name": "English",
        "confidence": confidence,
        "script": "Latin",
    }


def detect_unsupported_language_in_task(task_text: str) -> Optional[Tuple[str, str]]:
    lowered = task_text.lower()
    for token, lang_name in UNSUPPORTED_LANGUAGES.items():
        if len(token) <= 2:
            continue
        pattern = rf"\b(in|into|to|from)\s+{re.escape(token)}\b|\b{re.escape(token)}\s+(audio|translation|subtitles|summary|text|voice|language)\b"
        if re.search(pattern, lowered):
            return token, lang_name
    return None
