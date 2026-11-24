"""Language knowledge: which model supports which language, voices, and script detection.

All of this is local (no API calls, no cost). The tables come from the Bodhan API docs.
"""

from __future__ import annotations

from typing import Any

LANGUAGE_NAMES = {
    "as": "Assamese", "bhb": "Bhili", "bho": "Bhojpuri", "bn": "Bengali", "brx": "Bodo",
    "doi": "Dogri", "en": "English", "gu": "Gujarati", "hi": "Hindi", "kn": "Kannada",
    "kok": "Konkani", "ks": "Kashmiri (Perso-Arabic)", "ks-Deva": "Kashmiri (Devanagari)",
    "mai": "Maithili", "ml": "Malayalam", "mni": "Manipuri (Meitei Mayek)",
    "mni-Beng": "Manipuri (Bengali script)", "mr": "Marathi", "ne": "Nepali", "or": "Odia",
    "pa": "Punjabi", "sa": "Sanskrit", "sat": "Santali", "sd": "Sindhi (Devanagari)",
    "sd-Arab": "Sindhi (Perso-Arabic)", "ta": "Tamil", "te": "Telugu", "ur": "Urdu",
}

SUPPORT: dict[str, set[str]] = {
    "transcribe": {"as", "bhb", "bho", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks",
                   "mai", "ml", "mni", "mr", "ne", "or", "pa", "sa", "sat", "sd", "ta", "te", "ur"},
    "translate": {"as", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "ks-Deva", "mai",
                  "ml", "mni", "mni-Beng", "mr", "ne", "or", "pa", "sa", "sat", "sd", "sd-Arab",
                  "ta", "te", "ur"},
    "speak": {"as", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "mai", "ml", "mni",
              "mr", "ne", "or", "pa", "sa", "sat", "sd", "ta", "te", "ur"},
}

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

STYLES = [
    "AIR style news", "Customer Care", "TV style news", "advertisements", "anger",
    "children's stories", "disgust", "educational lecture", "fear", "happy", "news", "sad",
    "single person narration audiobook", "surprise",
]

_CODES = {c.lower(): c for c in LANGUAGE_NAMES}  # lowercase -> canonical ("ks-deva" -> "ks-Deva")
_ALIASES = {"bangla": "bn", "oriya": "or", "panjabi": "pa", "kashmiri": "ks", "sindhi": "sd",
            "manipuri": "mni", "meitei": "mni", "santhali": "sat"}
_NAME_TO_CODE = {n.lower(): c for c, n in LANGUAGE_NAMES.items()}
_NAME_TO_CODE.update(_ALIASES)
for _c, _n in LANGUAGE_NAMES.items():  # "Kashmiri (Devanagari)" -> also usable as "kashmiri (devanagari)"
    _NAME_TO_CODE.setdefault(_n.split(" (")[0].lower(), _c)


def normalize_language(value: str | None) -> str | None:
    """'Hindi', 'hindi', 'HI', 'hi_IN' and 'ks-deva' all map to a canonical code, else None."""
    if not value:
        return None
    v = value.strip().lower().replace("_", "-")
    if v in _CODES:
        return _CODES[v]
    if v in _NAME_TO_CODE:
        return _NAME_TO_CODE[v]
    base = v.split("-")[0]  # a region tag is dropped: "hi-xx" -> "hi"
    return _CODES.get(base)


def supports(model: str, code: str | None) -> bool:
    return code is not None and code in SUPPORT[model]


def support_report(language: str) -> dict[str, Any]:
    """What can each model do for this language? Used by the agent to refuse honestly."""
    code = normalize_language(language)
    if code is None:
        return {"language": language, "recognized": False,
                "message": f"'{language}' is not a language any Bodhan model supports."}
    return {
        "language": language, "recognized": True, "code": code, "name": LANGUAGE_NAMES[code],
        "transcribe": supports("transcribe", code),
        "translate": supports("translate", code),
        "speak": supports("speak", code),
        "voices": VOICES.get(code),
    }


def pick_voice(lang: str, gender: str | None = None, voice: str | None = None) -> str:
    """Return a valid voice name for `lang`. Raises ValueError with the valid choices."""
    table = VOICES.get(lang)
    if table is None:
        raise ValueError(f"No voices for language '{lang}'.")
    all_voices = [v for names in table.values() for v in names]
    if voice:
        match = next((v for v in all_voices if v.lower() == voice.lower()), None)
        if match is None:
            raise ValueError(f"Voice '{voice}' is not available for '{lang}'. Choose from {all_voices}.")
        return match
    g = (gender or "female").lower()
    if g not in table:
        raise ValueError(f"Gender must be 'female' or 'male', got '{gender}'.")
    return table[g][0]


# ---- script detection (allowed by the assignment: script / character set) ----------------
_SCRIPT_RANGES = {
    "Devanagari": (0x0900, 0x097F), "Bengali": (0x0980, 0x09FF), "Gurmukhi": (0x0A00, 0x0A7F),
    "Gujarati": (0x0A80, 0x0AFF), "Odia": (0x0B00, 0x0B7F), "Tamil": (0x0B80, 0x0BFF),
    "Telugu": (0x0C00, 0x0C7F), "Kannada": (0x0C80, 0x0CFF), "Malayalam": (0x0D00, 0x0D7F),
    "Arabic": (0x0600, 0x06FF), "OlChiki": (0x1C50, 0x1C7F), "MeiteiMayek": (0xABC0, 0xABFF),
}
_SCRIPT_LANGS = {
    "Devanagari": ["hi", "mr", "ne", "sa", "kok", "mai", "doi", "brx", "sd", "ks-Deva"],
    "Bengali": ["bn", "as", "mni-Beng"], "Gurmukhi": ["pa"], "Gujarati": ["gu"], "Odia": ["or"],
    "Tamil": ["ta"], "Telugu": ["te"], "Kannada": ["kn"], "Malayalam": ["ml"],
    "Arabic": ["ur", "ks", "sd-Arab"], "OlChiki": ["sat"], "MeiteiMayek": ["mni"],
    "Latin": ["en"],
}


def detect_language(text: str) -> dict[str, Any]:
    """Guess the language from the script. Script alone cannot separate Hindi from Marathi,
    so this returns the best guess PLUS the candidates, and says when it is ambiguous."""
    counts: dict[str, int] = {}
    for ch in text:
        o = ord(ch)
        if ch.isascii():
            if ch.isalpha():
                counts["Latin"] = counts.get("Latin", 0) + 1
            continue
        for name, (lo, hi) in _SCRIPT_RANGES.items():
            if lo <= o <= hi:
                counts[name] = counts.get(name, 0) + 1
                break
    total = sum(counts.values())
    if not total:
        return {"script": None, "language": None, "candidates": [], "ambiguous": True, "confidence": 0.0}
    script = max(counts, key=counts.get)
    cands = list(_SCRIPT_LANGS[script])
    if script == "Bengali" and ("\u09f0" in text or "\u09f1" in text):
        cands = ["as"]  # these two letters are Assamese-specific
    if script == "Devanagari" and "\u0933" in text:
        cands = ["mr"] + [c for c in cands if c != "mr"]  # ळ is typical of Marathi/Konkani
    return {
        "script": script,
        "language": cands[0],
        "candidates": cands,
        "ambiguous": len(cands) > 1,
        "confidence": round(counts[script] / total, 3),
        "note": "Script identifies the writing system, not always the language." if len(cands) > 1 else "",
    }
    


def expected_scripts(code: str) -> set[str]:
    """Scripts a correct output in this language may be written in (used for sanity checks)."""
    return {script for script, langs in _SCRIPT_LANGS.items() if code in langs}