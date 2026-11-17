"""Language support tables and helpers for Bodhan AI models."""

from __future__ import annotations

import unicodedata

# ---------------------------------------------------------------------------
# Language sets per model
# ---------------------------------------------------------------------------

TRANSCRIBE_LANGS: set[str] = {
    "as", "bhb", "bho", "bn", "brx", "doi", "en", "gu", "hi", "kn",
    "kok", "ks", "mai", "ml", "mni", "mr", "ne", "or", "pa", "sa",
    "sat", "sd", "ta", "te", "ur",
}

TRANSLATE_LANGS: set[str] = {
    "as", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks",
    "ks-Deva", "mai", "ml", "mni", "mni-Beng", "mr", "ne", "or", "pa",
    "sa", "sat", "sd", "sd-Arab", "ta", "te", "ur",
}

SPEAK_LANGS: set[str] = {
    "as", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks",
    "mai", "ml", "mni", "mr", "ne", "or", "pa", "sa", "sat", "sd",
    "ta", "te", "ur",
}

OCR_LANGS: set[str] = SPEAK_LANGS.copy()

# ---------------------------------------------------------------------------
# Voice data (from bodhan_api_rules.json)
# ---------------------------------------------------------------------------

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
    "en": {"female": ["Emma"], "male": ["James"]},
}

# ---------------------------------------------------------------------------
# Speaking styles (from bodhan_api_rules.json)
# ---------------------------------------------------------------------------

SPEAK_STYLES: list[str] = [
    "default",
    "news",
    "conversational",
    "storytelling",
    "formal",
    "calm",
    "energetic",
]

# ---------------------------------------------------------------------------
# Display names for language codes
# ---------------------------------------------------------------------------

LANG_NAMES: dict[str, str] = {
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
    "ks-Deva": "Kashmiri (Devanagari)",
    "mai": "Maithili",
    "ml": "Malayalam",
    "mni": "Manipuri (Meitei)",
    "mni-Beng": "Manipuri (Bengali script)",
    "mr": "Marathi",
    "ne": "Nepali",
    "or": "Odia",
    "pa": "Punjabi",
    "sa": "Sanskrit",
    "sat": "Santali",
    "sd": "Sindhi",
    "sd-Arab": "Sindhi (Arabic script)",
    "ta": "Tamil",
    "te": "Telugu",
    "ur": "Urdu",
}

# ---------------------------------------------------------------------------
# Script-ambiguous language groups
# ---------------------------------------------------------------------------

DEVANAGARI_LANGS: set[str] = {"hi", "mr", "sa", "kok", "mai", "ne", "sd"}
BENGALI_LANGS: set[str] = {"bn", "as", "mni-Beng"}

# ---------------------------------------------------------------------------
# Unicode block ranges for script detection
# ---------------------------------------------------------------------------

_SCRIPT_RANGES: list[tuple[int, int, list[str]]] = [
    (0x0900, 0x097F, list(DEVANAGARI_LANGS)),        # Devanagari
    (0x0980, 0x09FF, list(BENGALI_LANGS)),             # Bengali
    (0x0A00, 0x0A7F, ["pa"]),                          # Gurmukhi
    (0x0A80, 0x0AFF, ["gu"]),                          # Gujarati
    (0x0B00, 0x0B7F, ["or"]),                          # Odia
    (0x0B80, 0x0BFF, ["ta"]),                          # Tamil
    (0x0C00, 0x0C7F, ["te"]),                          # Telugu
    (0x0C80, 0x0CFF, ["kn"]),                          # Kannada
    (0x0D00, 0x0D7F, ["ml"]),                          # Malayalam
    (0x0E00, 0x0E7F, []),                              # Thai (not in scope)
    (0x1C50, 0x1C7F, ["sat"]),                         # Ol Chiki (Santali)
    (0xABC0, 0xABFF, ["mni"]),                         # Meetei Mayek
    (0x0600, 0x06FF, ["ur", "ks", "sd-Arab"]),         # Arabic
]


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def is_supported(lang: str, model: str) -> bool:
    """Return True if *lang* is supported by *model*.

    Args:
        lang: ISO language code.
        model: One of ``"transcribe"``, ``"translate"``, ``"speak"``, ``"ocr"``.
    """
    mapping: dict[str, set[str]] = {
        "transcribe": TRANSCRIBE_LANGS,
        "translate": TRANSLATE_LANGS,
        "speak": SPEAK_LANGS,
        "ocr": OCR_LANGS,
    }
    lang_set = mapping.get(model, set())
    return lang in lang_set


def detect_script(text: str) -> list[str]:
    """Return a list of likely language codes from Unicode block analysis.

    Scores each script block by counting codepoints that fall within it.
    Returns codes for all blocks with a non-zero hit, sorted by hit count
    descending.
    """
    scores: dict[str, int] = {}
    for ch in text:
        cp = ord(ch)
        for lo, hi, langs in _SCRIPT_RANGES:
            if lo <= cp <= hi:
                for lang in langs:
                    scores[lang] = scores.get(lang, 0) + 1
                break

    # Sort by score descending, return unique codes
    seen: set[str] = set()
    result: list[str] = []
    for lang in sorted(scores, key=lambda k: scores[k], reverse=True):
        if lang not in seen:
            result.append(lang)
            seen.add(lang)
    return result


def pick_voice(lang: str, gender: str = "female") -> str | None:
    """Return the first available voice for *lang* / *gender*, or None."""
    entry = VOICES.get(lang, {})
    voices = entry.get(gender, [])
    return voices[0] if voices else None
