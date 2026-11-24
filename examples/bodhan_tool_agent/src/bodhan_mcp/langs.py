"""Language tables, voices, script detection (by Unicode range only - no ML)."""
from __future__ import annotations

import re
import unicodedata
from collections import Counter

from .errors import ToolFailure

# ---- what each Bodhan model accepts (from https://console.bodhan.ai/api-docs/) ----
TRANSCRIBE = {"as", "bhb", "bho", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "mai", "ml",
              "mni", "mr", "ne", "or", "pa", "sa", "sat", "sd", "ta", "te", "ur"}
TRANSLATE = {"as", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "ks-Deva", "mai", "ml", "mni",
             "mni-Beng", "mr", "ne", "or", "pa", "sa", "sat", "sd", "sd-Arab", "ta", "te", "ur"}
SPEAK = {"as", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "mai", "ml", "mni", "mr", "ne",
         "or", "pa", "sa", "sat", "sd", "ta", "te", "ur"}
SERVICES = {"transcribe": TRANSCRIBE, "translate": TRANSLATE, "speak": SPEAK}

NAMES = {
    "assamese": "as", "bengali": "bn", "bangla": "bn", "bodo": "brx", "dogri": "doi", "english": "en",
    "gujarati": "gu", "hindi": "hi", "kannada": "kn", "konkani": "kok", "kashmiri": "ks",
    "maithili": "mai", "malayalam": "ml", "manipuri": "mni", "meitei": "mni", "marathi": "mr",
    "nepali": "ne", "odia": "or", "oriya": "or", "punjabi": "pa", "panjabi": "pa", "sanskrit": "sa",
    "santali": "sat", "sindhi": "sd", "tamil": "ta", "telugu": "te", "urdu": "ur",
    "bhili": "bhb", "bhojpuri": "bho",
}
CODE_NAMES = {"as": "Assamese", "bn": "Bengali", "brx": "Bodo", "doi": "Dogri", "en": "English",
              "gu": "Gujarati", "hi": "Hindi", "kn": "Kannada", "kok": "Konkani", "ks": "Kashmiri",
              "mai": "Maithili", "ml": "Malayalam", "mni": "Manipuri", "mr": "Marathi", "ne": "Nepali",
              "or": "Odia", "pa": "Punjabi", "sa": "Sanskrit", "sat": "Santali", "sd": "Sindhi",
              "ta": "Tamil", "te": "Telugu", "ur": "Urdu", "bhb": "Bhili", "bho": "Bhojpuri"}

# ---- Indic-Speak voice library (45 voices). Any voice can read any language. ----
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
ALL_VOICES = {v.lower(): (v, lang, g) for lang, d in VOICES.items() for g, vs in d.items() for v in vs}
STYLES = ["AIR style news", "Customer Care", "TV style news", "advertisements", "anger", "children's stories",
          "disgust", "educational lecture", "fear", "happy", "news", "sad",
          "single person narration audiobook", "surprise"]


def supported_list(service: str) -> list[str]:
    return sorted(SERVICES[service])


def normalize_lang(value: str | None, service: str) -> str:
    """Accept 'ta', 'Tamil', 'tamil'... and return a code the given service supports."""
    if value is None or not str(value).strip():
        raise ToolFailure("missing_language", f"A language is required for {service}.",
                          hint=f"Use one of: {', '.join(supported_list(service))}")
    raw = str(value).strip()
    codes = SERVICES[service]
    cand = raw if raw in codes else raw.lower()
    if cand not in codes:
        cand = NAMES.get(raw.lower(), cand)
    if cand not in codes:
        # e.g. 'ks-deva' / case variants
        for c in codes:
            if c.lower() == raw.lower():
                cand = c
    if cand in codes:
        return cand
    known = CODE_NAMES.get(NAMES.get(raw.lower(), raw.lower()), raw)
    raise ToolFailure(
        "unsupported_language",
        f"'{raw}' is not supported by the {service} model.",
        hint=(f"{service} supports: {', '.join(supported_list(service))}. If the task needs {known}, "
              "it cannot be done with these tools - say so plainly instead of inventing output."),
        details={"supported": supported_list(service)})


def pick_voice(lang: str, voice: str | None, gender: str | None) -> tuple[str, str, bool]:
    """Returns (voice, gender, assumed). `assumed` is True when we chose the voice ourselves."""
    if voice:
        hit = ALL_VOICES.get(voice.strip().lower())
        if not hit:
            raise ToolFailure("unknown_voice", f"Unknown voice '{voice}'.",
                              hint="Call list_capabilities to see the 45 voices.",
                              details={"voices": sorted(v for v, _, _ in ALL_VOICES.values())})
        return hit[0], hit[2], False
    g = (gender or "female").strip().lower()
    if g not in ("female", "male"):
        raise ToolFailure("invalid_gender", f"gender must be 'female' or 'male', got '{gender}'.")
    pool = VOICES.get(lang) or VOICES["hi"]
    return pool[g][0], g, gender is None


# ---- script detection ----
SCRIPT_RANGES = [
    ("Devanagari", 0x0900, 0x097F), ("Bengali", 0x0980, 0x09FF), ("Gurmukhi", 0x0A00, 0x0A7F),
    ("Gujarati", 0x0A80, 0x0AFF), ("Odia", 0x0B00, 0x0B7F), ("Tamil", 0x0B80, 0x0BFF),
    ("Telugu", 0x0C00, 0x0C7F), ("Kannada", 0x0C80, 0x0CFF), ("Malayalam", 0x0D00, 0x0D7F),
    ("Arabic", 0x0600, 0x06FF), ("Arabic", 0x0750, 0x077F), ("Arabic", 0xFB50, 0xFDFF),
    ("Arabic", 0xFE70, 0xFEFF), ("OlChiki", 0x1C50, 0x1C7F), ("MeeteiMayek", 0xABC0, 0xABFF),
]
SCRIPT_LANGS = {
    "Devanagari": ["hi", "mr", "ne", "sa", "mai", "kok", "doi", "brx", "sd"],
    "Bengali": ["bn", "as", "mni"], "Gurmukhi": ["pa"], "Gujarati": ["gu"], "Odia": ["or"],
    "Tamil": ["ta"], "Telugu": ["te"], "Kannada": ["kn"], "Malayalam": ["ml"],
    "Arabic": ["ur", "ks", "sd"], "OlChiki": ["sat"], "MeeteiMayek": ["mni"], "Latin": ["en"],
}
# script that a target language is written in, for translation checks
LANG_SCRIPT = {"hi": "Devanagari", "mr": "Devanagari", "ne": "Devanagari", "sa": "Devanagari",
               "mai": "Devanagari", "kok": "Devanagari", "doi": "Devanagari", "brx": "Devanagari",
               "sd": "Devanagari", "ks-Deva": "Devanagari", "bn": "Bengali", "as": "Bengali",
               "mni-Beng": "Bengali", "pa": "Gurmukhi", "gu": "Gujarati", "or": "Odia", "ta": "Tamil",
               "te": "Telugu", "kn": "Kannada", "ml": "Malayalam", "ur": "Arabic", "ks": "Arabic",
               "sd-Arab": "Arabic", "sat": "OlChiki", "mni": "MeeteiMayek", "en": "Latin"}


def script_of(ch: str) -> str | None:
    cp = ord(ch)
    for name, lo, hi in SCRIPT_RANGES:
        if lo <= cp <= hi:
            return name
    if ch.isascii() and ch.isalpha():
        return "Latin"
    return None


def script_counts(text: str) -> Counter:
    c: Counter = Counter()
    for ch in text:
        s = script_of(ch)
        if s and (ch.isalpha() or unicodedata.category(ch).startswith("M")):
            c[s] += 1
    return c


_MR = {"आहे", "आणि", "आहेत", "नाही", "काय", "होते", "केले", "च्या", "मध्ये", "तो", "ती", "आम्ही", "तुम्ही"}
_HI = {"है", "हैं", "और", "का", "की", "के", "में", "नहीं", "था", "थी", "को", "से", "यह", "पर", "कि", "तो"}
_NE = {"छ", "छन्", "हुन्छ", "र", "लाई", "मा", "को", "गर्न", "भएको", "हो", "गरेको"}


def guess_language(text: str) -> dict:
    """Best-effort language guess from script (+ a few function-word hints). Honest about ambiguity."""
    counts = script_counts(text)
    total = sum(counts.values())
    if total == 0:
        return {"script": None, "language": None, "candidates": [], "confidence": "none",
                "note": "No letters found."}
    script, n = counts.most_common(1)[0]
    cands = SCRIPT_LANGS.get(script, [])
    lang, conf, note = cands[0] if cands else None, "high", ""
    if len(cands) > 1:
        conf, note = "low", f"{script} script is shared by {', '.join(cands)}."
        words = set(re.findall(r"\S+", text))
        if script == "Devanagari":
            scores = {"mr": len(words & _MR), "hi": len(words & _HI), "ne": len(words & _NE)}
            best = max(scores, key=lambda k: scores[k])
            if scores[best] >= 2 and sorted(scores.values())[-1] > sorted(scores.values())[-2]:
                lang, conf, note = best, "medium", "Chosen from common function words; verify if it matters."
        elif script == "Bengali":
            if "ৰ" in text or "ৱ" in text:
                lang, conf, note = "as", "medium", "Assamese-specific letters found."
            else:
                lang, conf = "bn", "medium"
                note = "Bengali script; Assamese is also possible but no Assamese-only letters seen."
        elif script == "Arabic":
            lang, note = "ur", "Arabic script is shared by Urdu, Kashmiri and Sindhi; Urdu assumed."
    if script != "Latin" and counts.get("Latin", 0) > 0.5 * n:
        note += " Lots of Latin text too (code-mixed?)."
    return {"script": script, "language": lang, "candidates": cands, "confidence": conf, "note": note.strip(),
            "script_share": round(n / total, 3)}


# Prompt names used by the chat-completions transport ("Translate the following text into <name>:").
PROMPT_NAMES = {
    "en": "English", "as": "Assamese", "bn": "Bengali", "brx": "Bodo", "doi": "Dogri", "kok": "Konkani",
    "gu": "Gujarati", "hi": "Hindi", "kn": "Kannada", "ks": "Kashmiri (Perso-Arabic script)",
    "ks-Deva": "Kashmiri (Devanagari script)", "mai": "Maithili", "ml": "Malayalam", "mr": "Marathi",
    "mni": "Manipuri (Meitei script)", "mni-Beng": "Manipuri (Bengali script)", "ne": "Nepali", "or": "Odia",
    "pa": "Punjabi", "sa": "Sanskrit", "sat": "Santali", "sd": "Sindhi (Devanagari script)",
    "sd-Arab": "Sindhi (Perso-Arabic script)", "ta": "Tamil", "te": "Telugu", "ur": "Urdu",
}


def translate_prompt(text: str, target: str, script: str = "native") -> str:
    name = PROMPT_NAMES[target]
    if script == "roman":
        name += " (Roman)"
    elif script == "codemix":
        name += " (code-mixed)"
    return f"Translate the following text into {name}:\n\n{text}"
