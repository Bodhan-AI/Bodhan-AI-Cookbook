"""Language detection and validation utilities for Bodhan Indic models."""

from __future__ import annotations

import re
import unicodedata
from typing import Tuple

# ISO 639-1 / 639-2 codes mapped to official English names
# Based on Bodhan AI API specifications (22 official Indic languages + English)
SUPPORTED_INDIC_LANGUAGES: dict[str, str] = {
    "as": "Assamese",
    "bhb": "Bhili",
    "bho": "Bhojpuri",
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

# Reverse mapping from lower-case name to code
_NAME_TO_CODE: dict[str, str] = {
    name.lower(): code for code, name in SUPPORTED_INDIC_LANGUAGES.items()
}
_NAME_TO_CODE.update({
    "bangla": "bn",
    "oriya": "or",
})

# Script code to readable script family name
SCRIPT_NAMES: dict[str, str] = {
    "hi": "Devanagari",
    "bn": "Bengali",
    "pa": "Gurmukhi",
    "gu": "Gujarati",
    "or": "Odia",
    "ta": "Tamil",
    "te": "Telugu",
    "kn": "Kannada",
    "ml": "Malayalam",
    "ur": "Arabic",
    "en": "Latin",
}

# Mapping from language code to its expected standard script code
EXPECTED_SCRIPT_FOR_LANG: dict[str, str] = {
    "hi": "hi", "mr": "hi", "sa": "hi", "ne": "hi", "mai": "hi", "bho": "hi", "doi": "hi", "kok": "hi", "brx": "hi",
    "bn": "bn", "as": "bn", "mni": "bn",
    "pa": "pa",
    "gu": "gu",
    "or": "or",
    "ta": "ta",
    "te": "te",
    "kn": "kn",
    "ml": "ml",
    "ur": "ur", "ks": "ur", "sd": "ur",
    "en": "en",
}


def find_language_mentions(text: str) -> list[tuple[int, int, str, str, str]]:
    """Finds all explicit language mentions in text.

    Only matches full language names (e.g. Hindi, Tamil, Bangla) and explicit code
    forms such as 'language=hi', 'lang=ta', 'in hi', 'into bn', 'to hi', 'from hi'.
    Common English words that happen to be codes ('hi', 'or', 'as') are never matched
    as standalone words.

    Returns:
        List of (start_idx, end_idx, lang_code, lang_name, match_type) sorted by start_idx.
    """
    if not text:
        return []

    mentions: list[tuple[int, int, str, str, str]] = []

    # 1. Full language names and aliases (case-insensitive whole words)
    for name, code in sorted(_NAME_TO_CODE.items(), key=lambda x: len(x[0]), reverse=True):
        pattern = r"\b" + re.escape(name) + r"\b"
        for m in re.finditer(pattern, text, re.IGNORECASE):
            mentions.append((m.start(), m.end(), code, SUPPORTED_INDIC_LANGUAGES[code], "name"))

    # 2. Explicit code forms: language=xx, lang=xx
    for m in re.finditer(r"\b(?:language|lang)\s*[:=]\s*([a-zA-Z]{2,3})\b", text, re.IGNORECASE):
        c = m.group(1).lower()
        if c in SUPPORTED_INDIC_LANGUAGES:
            mentions.append((m.start(), m.end(), c, SUPPORTED_INDIC_LANGUAGES[c], "explicit_code"))

    # 3. Explicit preposition forms: in xx, into xx, to xx, from xx
    # Common English words like 'as' and 'or' are excluded here to prevent matching phrases like
    # 'in as much as' or 'refer to as'.
    for m in re.finditer(r"\b(?:in|into|to|from)\s+([a-zA-Z]{2,3})\b", text, re.IGNORECASE):
        c = m.group(1).lower()
        if c in SUPPORTED_INDIC_LANGUAGES and c not in ("as", "or"):
            mentions.append((m.start(), m.end(), c, SUPPORTED_INDIC_LANGUAGES[c], "explicit_code"))

    # Deduplicate overlapping spans (keep earliest / longest)
    mentions.sort(key=lambda x: x[0])
    filtered: list[tuple[int, int, str, str, str]] = []
    for m in mentions:
        if not any(f[0] <= m[0] and m[1] <= f[1] for f in filtered):
            filtered.append(m)
    return filtered


def _get_clause_for_pos(text: str, pos: int) -> str:
    """Finds the grammatical clause containing the character index pos."""
    clause_delims = [0] + [m.end() for m in re.finditer(r"\b(?:and\s+then|and|then|after|while|but|,|;|\.)\b", text, re.IGNORECASE)] + [len(text)]
    clause_delims = sorted(list(set(clause_delims)))
    for i in range(len(clause_delims) - 1):
        c_start = clause_delims[i]
        c_end = clause_delims[i + 1]
        if c_start <= pos <= c_end:
            return text[c_start:c_end]
    return text


def _score_mention_role(text: str, mention: tuple[int, int, str, str, str]) -> tuple[int, int]:
    """Scores a language mention for audio vs target role in the task."""
    start, end, code, name, _ = mention
    clause = _get_clause_for_pos(text, start).lower()
    pre_word = text[max(0, start - 25):start].lower()
    post_word = text[end:min(len(text), end + 25)].lower()

    audio_score = 0
    target_score = 0

    if re.search(r"\b(?:transcrib\w*|audio|recording|speech|voice|wav|listen|clip)\b", clause):
        audio_score += 3
    if re.search(r"^\s*(?:audio|clip|recording|voice|speech|wav)\b", post_word):
        audio_score += 3
    if re.search(r"\bfrom\s*$", pre_word.strip()):
        audio_score += 2

    if re.search(r"\b(?:translat\w*|synthesiz\w*|speak|tts)\b", clause):
        target_score += 3
    if re.search(r"\b(?:to|into)\s*$", pre_word.strip()):
        target_score += 4
    if re.search(r"\b(?:target|destination)\b", clause):
        target_score += 3

    return audio_score, target_score


def extract_task_languages(text: str) -> dict[str, Any]:
    """Extracts and separates audio_language and target_language from a task description.

    Handles single-language tasks, multi-clause tasks (e.g. 'Transcribe Hindi audio
    and translate to Tamil'), and flags ambiguous language requests.

    Returns:
        dict with keys:
            - 'audio_language': ISO code (or None)
            - 'target_language': ISO code (or None)
            - 'audio_language_name': English name (or None)
            - 'target_language_name': English name (or None)
            - 'is_ambiguous': bool
            - 'ambiguity_reason': str | None
    """
    mentions = find_language_mentions(text)
    if not mentions:
        return {
            "audio_language": None,
            "target_language": None,
            "audio_language_name": None,
            "target_language_name": None,
            "is_ambiguous": False,
            "ambiguity_reason": None,
        }

    distinct_codes = list(dict.fromkeys(m[2] for m in mentions))

    # Single distinct language
    if len(distinct_codes) == 1:
        code = distinct_codes[0]
        name = SUPPORTED_INDIC_LANGUAGES[code]
        a_score, t_score = _score_mention_role(text, mentions[0])
        if a_score > t_score:
            return {
                "audio_language": code,
                "target_language": None,
                "audio_language_name": name,
                "target_language_name": None,
                "is_ambiguous": False,
                "ambiguity_reason": None,
            }
        elif t_score > a_score:
            return {
                "audio_language": None,
                "target_language": code,
                "audio_language_name": None,
                "target_language_name": name,
                "is_ambiguous": False,
                "ambiguity_reason": None,
            }
        else:
            return {
                "audio_language": code,
                "target_language": code,
                "audio_language_name": name,
                "target_language_name": name,
                "is_ambiguous": False,
                "ambiguity_reason": None,
            }

    # More than 2 distinct languages is ambiguous
    if len(distinct_codes) > 2:
        names = [SUPPORTED_INDIC_LANGUAGES[c] for c in distinct_codes]
        return {
            "audio_language": None,
            "target_language": None,
            "audio_language_name": None,
            "target_language_name": None,
            "is_ambiguous": True,
            "ambiguity_reason": f"More than two languages specified in task: {', '.join(names)}",
        }

    # Exactly 2 distinct languages (e.g. Hindi audio and translate to Tamil)
    c1, c2 = distinct_codes
    m1 = next(m for m in mentions if m[2] == c1)
    m2 = next(m for m in mentions if m[2] == c2)

    a1, t1 = _score_mention_role(text, m1)
    a2, t2 = _score_mention_role(text, m2)

    if a1 > t1 and t2 > a2:
        return {
            "audio_language": c1,
            "target_language": c2,
            "audio_language_name": SUPPORTED_INDIC_LANGUAGES[c1],
            "target_language_name": SUPPORTED_INDIC_LANGUAGES[c2],
            "is_ambiguous": False,
            "ambiguity_reason": None,
        }
    elif a2 > t2 and t1 > a1:
        return {
            "audio_language": c2,
            "target_language": c1,
            "audio_language_name": SUPPORTED_INDIC_LANGUAGES[c2],
            "target_language_name": SUPPORTED_INDIC_LANGUAGES[c1],
            "is_ambiguous": False,
            "ambiguity_reason": None,
        }
    else:
        # Preposition check: e.g. "translate from Tamil to Hindi" (text translation)
        pre1 = text[max(0, m1[0] - 15):m1[0]].lower().strip()
        pre2 = text[max(0, m2[0] - 15):m2[0]].lower().strip()
        if pre1.endswith("to") or pre1.endswith("into"):
            return {
                "audio_language": None,
                "target_language": c1,
                "audio_language_name": None,
                "target_language_name": SUPPORTED_INDIC_LANGUAGES[c1],
                "is_ambiguous": False,
                "ambiguity_reason": None,
            }
        if pre2.endswith("to") or pre2.endswith("into"):
            return {
                "audio_language": None,
                "target_language": c2,
                "audio_language_name": None,
                "target_language_name": SUPPORTED_INDIC_LANGUAGES[c2],
                "is_ambiguous": False,
                "ambiguity_reason": None,
            }

        return {
            "audio_language": None,
            "target_language": None,
            "audio_language_name": None,
            "target_language_name": None,
            "is_ambiguous": True,
            "ambiguity_reason": f"Ambiguous language roles between {SUPPORTED_INDIC_LANGUAGES[c1]} and {SUPPORTED_INDIC_LANGUAGES[c2]}",
        }


def extract_language_from_text(text: str) -> tuple[str | None, str | None]:
    """Extracts spoken/written Indic language from a natural language task description.

    Returns:
        (iso_code, language_name) or (None, None) if no language was explicitly named or ambiguous.
    """
    info = extract_task_languages(text)
    if info["is_ambiguous"]:
        return None, None
    code = info["audio_language"] or info["target_language"]
    if code:
        return code, SUPPORTED_INDIC_LANGUAGES[code]
    return None, None


def is_language_supported(lang_query: str) -> Tuple[bool, str | None, str]:
    """Checks whether a language (code or English name) is supported by Bodhan models.

    Returns:
        (is_supported, iso_code_or_none, message)
    """
    clean = lang_query.strip().lower()
    if not clean:
        return False, None, "No language provided."

    # Direct match on code
    if clean in SUPPORTED_INDIC_LANGUAGES:
        return True, clean, f"Supported: {SUPPORTED_INDIC_LANGUAGES[clean]} ({clean})"

    # Match on language name
    if clean in _NAME_TO_CODE:
        code = _NAME_TO_CODE[clean]
        return True, code, f"Supported: {SUPPORTED_INDIC_LANGUAGES[code]} ({code})"

    # Unsupported language
    return (
        False,
        None,
        f"Language {lang_query!r} is not supported. Bodhan models only support 22 official Indian languages and English.",
    )


def detect_script_language(text: str) -> str | None:
    """Detects primary language/script from Unicode character distribution.

    Returns ISO code (e.g. 'hi', 'ta', 'te', 'bn', 'en') or None if undetermined.
    """
    if not text:
        return None

    counts: dict[str, int] = {
        "hi": 0,  # Devanagari (Hindi, Marathi, Sanskrit, Nepali)
        "bn": 0,  # Bengali / Assamese
        "pa": 0,  # Gurmukhi (Punjabi)
        "gu": 0,  # Gujarati
        "or": 0,  # Odia
        "ta": 0,  # Tamil
        "te": 0,  # Telugu
        "kn": 0,  # Kannada
        "ml": 0,  # Malayalam
        "ur": 0,  # Arabic / Perso-Arabic (Urdu, Kashmiri, Sindhi)
        "en": 0,  # Basic Latin
    }

    for char in text:
        cp = ord(char)
        if 0x0900 <= cp <= 0x097F:
            counts["hi"] += 1
        elif 0x0980 <= cp <= 0x09FF:
            counts["bn"] += 1
        elif 0x0A00 <= cp <= 0x0A7F:
            counts["pa"] += 1
        elif 0x0A80 <= cp <= 0x0AFF:
            counts["gu"] += 1
        elif 0x0B00 <= cp <= 0x0B7F:
            counts["or"] += 1
        elif 0x0B80 <= cp <= 0x0BFF:
            counts["ta"] += 1
        elif 0x0C00 <= cp <= 0x0C7F:
            counts["te"] += 1
        elif 0x0C80 <= cp <= 0x0CFF:
            counts["kn"] += 1
        elif 0x0D00 <= cp <= 0x0D7F:
            counts["ml"] += 1
        elif 0x0600 <= cp <= 0x06FF:
            counts["ur"] += 1
        elif (0x0041 <= cp <= 0x005A) or (0x0061 <= cp <= 0x007A):
            counts["en"] += 1

    total_indic_or_latin = sum(counts.values())
    if total_indic_or_latin == 0:
        return None

    # Get dominant script
    top_lang, top_count = max(counts.items(), key=lambda item: item[1])
    if top_count / float(total_indic_or_latin) >= 0.3:
        return top_lang

    return None
