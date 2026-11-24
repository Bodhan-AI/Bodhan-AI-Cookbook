"""Language and script detection using Unicode ranges and Devanagari stop-word / grammatical heuristics.
Returns confidence score and marks weak determinations as assumed.
"""

import re
import unicodedata
from typing import Tuple
from bodhan_mcp.types import DetectLangOutput

# Distinctive grammatical markers and stop-words for Devanagari disambiguation
# Marathi specific tokens:
MARATHI_MARKERS = {
    # Auxiliary verbs & copulas
    "आहे", "आहेत", "आहोत", "नाही", "नाहीत", "होते", "होता", "होती", "असून", "नसून",
    # Past/aspectual forms
    "झाला", "झाली", "झाले", "झालो", "झाल्या", "गेला", "गेली", "गेले", "आला", "आली", "आले",
    # Present/habitual verbs
    "करतो", "करते", "करतोय", "करतेय", "करतात", "करणार", "सांगितले", "म्हणाला", "म्हणाली",
    # Postpositions & case markers
    "च्या", "चा", "ची", "चे", "हून", "मध्ये",
    # Conjunctions & adverbs
    "म्हणून", "पण", "आणि", "किंवा", "कसा", "कशी", "कसे", "कुठे", "केव्हा", "कशासाठी",
    # Pronouns
    "आपण", "आम्ही", "तुम्ही", "त्यांना", "यांना", "त्यांच्या", "याच्या", "यांच्या", "माहिती",
    # Lexical
    "खूप", "फार", "करायचे", "पाहिजे", "मित्रांनो"
}

# Hindi specific tokens:
HINDI_MARKERS = {
    # Auxiliary verbs & copulas
    "है", "हैं", "नहीं", "था", "थी", "थे", "हूँ", "हो", "होगा", "होगी", "होंगे",
    # Aspectual / continuous verbs
    "हुआ", "हुई", "हुए", "रहा", "रही", "रहे", "करता", "करती", "करते", "किया", "किये", "लिया", "दिये", "गया", "गई", "गए",
    # Postpositions
    "का", "के", "की", "को", "में", "से", "ने", "पर", "तक",
    # Conjunctions & adverbs
    "और", "या", "लेकिन", "किंतु", "परंतु", "क्योंकि", "इसलिए", "यहाँ", "वहाँ", "कहाँ", "कब", "कैसे", "क्या", "कौन",
    # Pronouns
    "यह", "वह", "ये", "वे", "हम", "तुम", "आप", "उनका", "उनकी", "उनके", "इसका", "इसकी", "इसके", "उनको", "इनको", "मुझे", "हमें",
    # Lexical / modal
    "सकता", "सकती", "सकते", "चाहिए", "बहुत"
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
    """Disambiguate Hindi vs. Marathi in Devanagari text using grammatical markers,
    unique phonemes (e.g. 'ळ'), and morphological suffix heuristics.
    Returns (lang_code, confidence, is_assumed).
    """
    # 1. Check for 'ळ' (Unicode \u0933), unique to Marathi in Devanagari texts
    if "\u0933" in text:
        return "mr", 0.99, False

    # 2. Extract Devanagari word tokens (without punctuation)
    tokens = re.findall(r"[\u0900-\u097F]+", text)
    if not tokens:
        return "hi", 0.50, True

    marathi_score = 0.0
    hindi_score = 0.0

    for token in tokens:
        # Lexical marker checks
        if token in MARATHI_MARKERS:
            marathi_score += 2.0
        elif token in HINDI_MARKERS:
            hindi_score += 2.0

        # Morphological suffix checks
        if len(token) >= 3:
            if token.endswith("ांनो"):  # Marathi vocative plural (e.g. मित्रांनो, बंधूंनो)
                marathi_score += 2.5
            elif token.endswith("च्या"):  # Marathi genitive / oblique (e.g. घराच्या)
                marathi_score += 2.0
            elif token.endswith("तोय") or token.endswith("तेय"):  # Marathi continuous verbs
                marathi_score += 2.0
            elif token.endswith("चा") or token.endswith("ची") or token.endswith("चे"):
                # Marathi possessive postposition endings
                if token not in {"चा", "ची", "चे"}:  # avoid double counting standalone
                    marathi_score += 1.5

            if token.endswith("ियों") or token.endswith("ों"):  # Hindi oblique plural (e.g. दोस्तों, किताबों)
                if token not in {"ों"}:
                    hindi_score += 1.0

    # Decision logic
    if marathi_score > 0 and marathi_score > hindi_score:
        ratio = marathi_score / (marathi_score + hindi_score + 0.5)
        conf = round(min(0.99, max(0.75, ratio)), 2)
        return "mr", conf, False
    elif hindi_score > 0 and hindi_score > marathi_score:
        ratio = hindi_score / (marathi_score + hindi_score + 0.5)
        conf = round(min(0.99, max(0.75, ratio)), 2)
        return "hi", conf, False
    else:
        # Ambiguous short text, neutral greeting (e.g. 'नमस्कार', single Tatsama words)
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
