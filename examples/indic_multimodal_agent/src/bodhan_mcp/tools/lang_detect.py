"""Language and script detection using Unicode ranges and Devanagari stop-word heuristics.
Returns confidence score and marks weak determinations as assumed.
"""

import unicodedata
from typing import Tuple
from bodhan_mcp.types import DetectLangOutput

# Distinctive grammatical markers for Devanagari disambiguation
MARATHI_STOPWORDS = {
    "आहे", "आहेत", "नाही", "झाले", "झाला", "झाली", "होते", "होता", "होती",
    "करतो", "करते", "करतोय", "च्या", "त्यांच्या", "म्हणून", "पण", "आणि", "कसे",
    "काय", "येथे", "सर्व", "त्यांना", "याच्या", "माहिती", "आपण", "मध्ये", "हे"
}

HINDI_STOPWORDS = {
    "है", "हैं", "नहीं", "हुआ", "हुई", "हुए", "था", "थी", "थे",
    "करता", "करती", "करते", "रहा", "रही", "रहे", "का", "के", "की",
    "क्योंकि", "लेकिन", "और", "कैसे", "क्या", "यहाँ", "सभी", "उनको", "इसका", "हम", "में", "से", "यह", "वह"
}


def detect_script(text: str) -> str:
    """Identify dominant script from Unicode character blocks."""
    counts = {
        "Devanagari": 0,
        "Tamil": 0,
        "Telugu": 0,
        "Kannada": 0,
        "Bengali": 0,
        "Gujarati": 0,
        "Malayalam": 0,
        "Gurmukhi": 0,
        "Odia": 0,
        "Latin": 0,
    }

    for ch in text:
        cp = ord(ch)
        if 0x0900 <= cp <= 0x097F:
            counts["Devanagari"] += 1
        elif 0x0B80 <= cp <= 0x0BFF:
            counts["Tamil"] += 1
        elif 0x0C00 <= cp <= 0x0C7F:
            counts["Telugu"] += 1
        elif 0x0C80 <= cp <= 0x0CFF:
            counts["Kannada"] += 1
        elif 0x0980 <= cp <= 0x09FF:
            counts["Bengali"] += 1
        elif 0x0A80 <= cp <= 0x0AFF:
            counts["Gujarati"] += 1
        elif 0x0D00 <= cp <= 0x0D7F:
            counts["Malayalam"] += 1
        elif 0x0A00 <= cp <= 0x0A7F:
            counts["Gurmukhi"] += 1
        elif 0x0B00 <= cp <= 0x0B7F:
            counts["Odia"] += 1
        elif ('a' <= ch <= 'z') or ('A' <= ch <= 'Z'):
            counts["Latin"] += 1

    dominant = max(counts.items(), key=lambda x: x[1])
    if dominant[1] == 0:
        return "Unknown"
    return dominant[0]


def disambiguate_devanagari(text: str) -> Tuple[str, float, bool]:
    """Disambiguate Hindi vs. Marathi in Devanagari text.
    Returns (lang_code, confidence, is_assumed).
    """
    # 1. Check for 'ळ' (Unicode \u0933), unique to Marathi in Devanagari texts
    if "\u0933" in text:
        return "mr", 0.99, False

    # 2. Tokenize and count stop-words
    words = set(text.split())
    marathi_hits = len(words.intersection(MARATHI_STOPWORDS))
    hindi_hits = len(words.intersection(HINDI_STOPWORDS))

    total_hits = marathi_hits + hindi_hits

    if marathi_hits > hindi_hits:
        confidence = round(min(0.99, max(0.55, marathi_hits / (total_hits + 0.2))), 2)
        is_assumed = confidence < 0.70
        return "mr", confidence, is_assumed
    elif hindi_hits > marathi_hits:
        confidence = round(min(0.99, max(0.55, hindi_hits / (total_hits + 0.2))), 2)
        is_assumed = confidence < 0.70
        return "hi", confidence, is_assumed
    else:
        # Tied or zero hits: default to Hindi with weak assumption
        return "hi", 0.50, True


def detect_text_language(text: str) -> DetectLangOutput:
    """Determine language, script, and confidence from text."""
    normalized = unicodedata.normalize("NFC", text.strip())
    script = detect_script(normalized)

    script_map = {
        "Tamil": ("ta", 0.99, False),
        "Telugu": ("te", 0.99, False),
        "Kannada": ("kn", 0.99, False),
        "Bengali": ("bn", 0.95, False),
        "Gujarati": ("gu", 0.99, False),
        "Malayalam": ("ml", 0.99, False),
        "Gurmukhi": ("pa", 0.99, False),
        "Odia": ("or", 0.99, False),
        "Latin": ("en", 0.95, False),
    }

    if script in script_map:
        lang, conf, assumed = script_map[script]
        return DetectLangOutput(language=lang, script=script, confidence=conf, is_assumed=assumed)

    if script == "Devanagari":
        lang, conf, assumed = disambiguate_devanagari(normalized)
        return DetectLangOutput(language=lang, script="Devanagari", confidence=conf, is_assumed=assumed)

    return DetectLangOutput(language="unknown", script="Unknown", confidence=0.0, is_assumed=True)
