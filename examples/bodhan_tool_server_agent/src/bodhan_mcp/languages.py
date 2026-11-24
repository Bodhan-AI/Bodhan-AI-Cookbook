"""Language tables (from Bodhan API docs, 2026-11) and script-based language ID."""
import re

ASR = set(["as", "bhb", "bho", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "mai", "ml", "mni", "mr", "ne", "or", "pa", "sa", "sat", "sd", "ta", "te", "ur"])
TRANSLATE = set(["as", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "ks-Deva", "mai", "ml", "mni", "mni-Beng", "mr", "ne", "or", "pa", "sa", "sat", "sd", "sd-Arab", "ta", "te", "ur"])
SPEAK = set(["as", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "mai", "ml", "mni", "mr", "ne", "or", "pa", "sa", "sat", "sd", "ta", "te", "ur"])
MODELS = {"transcribe": ASR, "translate": TRANSLATE, "speak": SPEAK}

NAMES = {  # alias -> code; matches by lowercase
    "assamese": "as", "bengali": "bn", "bangla": "bn", "bodo": "brx", "dogri": "doi", "english": "en",
    "gujarati": "gu", "hindi": "hi", "kannada": "kn", "konkani": "kok", "kashmiri": "ks", "maithili": "mai",
    "malayalam": "ml", "manipuri": "mni", "meitei": "mni", "marathi": "mr", "nepali": "ne", "odia": "or",
    "oriya": "or", "punjabi": "pa", "panjabi": "pa", "sanskrit": "sa", "santali": "sat", "sindhi": "sd",
    "tamil": "ta", "telugu": "te", "urdu": "ur", "bhojpuri": "bho", "bhili": "bhb",
    # script variants, spelled as the Bodhan docs name them
    "kashmiri (perso-arabic)": "ks", "kashmiri (devanagari)": "ks-Deva", "sindhi (devanagari)": "sd",
    "sindhi (perso-arabic)": "sd-Arab", "manipuri (meitei mayek)": "mni", "manipuri (bengali script)": "mni-Beng",
}

# Languages people ask for that no Bodhan model supports (so we can name them and refuse). Unknown names are never refused.
UNSUPPORTED_NAMES = set(["french", "spanish", "german", "italian", "portuguese", "russian", "chinese", "mandarin", "cantonese", "japanese", "korean", "arabic", "turkish", "dutch", "polish", "swedish", "norwegian", "danish", "finnish", "greek", "hebrew", "persian", "farsi", "thai", "vietnamese", "indonesian", "malay", "swahili", "ukrainian", "czech", "hungarian", "romanian", "tulu", "rajasthani", "marwari", "magahi", "awadhi", "haryanvi", "chhattisgarhi", "sinhala", "burmese", "khmer", "tagalog", "filipino", "hausa", "yoruba", "zulu", "afrikaans", "fr", "es", "de", "it", "pt", "ru", "zh", "ja", "ko", "ar", "tr", "nl", "pl", "sv"])
_ALL_CODES = {c.lower(): c for c in ASR | TRANSLATE | SPEAK}
NEEDS = {"asr": ASR, "translate": TRANSLATE, "speak": SPEAK}
_PRETTY = {}
for _alias, _code in NAMES.items():
    _PRETTY.setdefault(_code, _alias.title())


def resolve(name: str) -> str | None:
    """Language name or code -> supported code (any model), or None if unknown."""
    s = name.strip().lower()
    return NAMES.get(s) or _ALL_CODES.get(s)


def language_problems(names: list[str] | None, need: str) -> list[tuple[str, str]]:
    """Named languages that the model for `need` (asr | translate | speak) cannot handle. Unknown names are ignored."""
    out = []
    for n in names or []:
        code = resolve(n)
        if code is None:
            if n.strip().lower() in UNSUPPORTED_NAMES:
                out.append((n, f"{n} is not supported"))
        elif code not in NEEDS[need]:
            out.append((n, f"{n} ({code}) is not supported for {'transcription' if need == 'asr' else 'speech' if need == 'speak' else 'translation'}"))
    return out


def supported_text(need: str) -> str:
    return ", ".join(f"{_PRETTY.get(c, c)} ({c})" for c in sorted(NEEDS[need]) if c in _PRETTY)


# voices: language -> (female..., male)  — from the Bodhan docs table; any voice can read any language
VOICES = {
    "as": ("Prastuti", "Ankur"), "bn": ("Ishita", "Sourav"), "brx": ("Gwrbw", "Sansuma"),
    "doi": ("Preeti", "Sham"), "gu": ("Dhara", "Parth"), "hi": ("Kavya", "Amit"),
    "kn": ("Deepika", "Adarsh"), "kok": ("Anjali", "Sandeep"), "ks": ("Zoon", "Ishfaq"),
    "mai": ("Vaidehi", "Madhukar"), "ml": ("Lakshmi", "Kiran"), "mni": ("Thoibi", "Chaoba"),
    "mr": ("Anagha", "Chinmay"), "ne": ("Srijana", "Sagar"), "or": ("Itishree", "Akash"),
    "pa": ("Kaur", "Manpreet"), "sa": ("Bharati", "Aryaman"), "sat": ("Phulmani", "Sibu"),
    "sd": ("Moomal", "Rano"), "ta": ("Anitha", "Arun"), "te": ("Sravani", "Vamsi"), "ur": ("Saba", "Zaid"),
}
EXTRA_FEMALE = {"hi": ["Suhani"]}
ALL_VOICES = {v.lower(): v for pair in VOICES.values() for v in pair} | {"suhani": "Suhani"}


def norm(code_or_name: str) -> str:
    """Name or code in any letter case to the canonical code ('KS-DEVA' becomes 'ks-Deva'); unknown input is returned as typed."""
    s = code_or_name.strip()
    return NAMES.get(s.lower()) or _ALL_CODES.get(s.lower()) or s


def pick_voice(lang: str, gender: str | None) -> str:
    f, m = VOICES.get(lang, VOICES["hi"])  # English etc: Hindi voices read any language
    return m if (gender or "female").lower().startswith("m") else f


# ---------- script detection ----------
BLOCKS = {
    "Devanagari": (0x0900, 0x097F), "Bengali": (0x0980, 0x09FF), "Gurmukhi": (0x0A00, 0x0A7F),
    "Gujarati": (0x0A80, 0x0AFF), "Odia": (0x0B00, 0x0B7F), "Tamil": (0x0B80, 0x0BFF),
    "Telugu": (0x0C00, 0x0C7F), "Kannada": (0x0C80, 0x0CFF), "Malayalam": (0x0D00, 0x0D7F),
    "Arabic": (0x0600, 0x06FF), "OlChiki": (0x1C50, 0x1C7F), "MeeteiMayek": (0xABC0, 0xABFF), "Latin": (0x0041, 0x024F),
}
SCRIPT_LANG = {"Bengali": "bn", "Gurmukhi": "pa", "Gujarati": "gu", "Odia": "or", "Tamil": "ta",
               "Telugu": "te", "Kannada": "kn", "Malayalam": "ml", "OlChiki": "sat", "MeeteiMayek": "mni", "Latin": "en"}


def script_counts(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for ch in text:
        o = ord(ch)
        if not ch.isalpha():
            continue
        for name, (a, b) in BLOCKS.items():
            if a <= o <= b:
                out[name] = out.get(name, 0) + 1
                break
    return out


def has_indic(text: str) -> bool:
    return any(k != "Latin" for k in script_counts(text))


def detect_language(text: str) -> dict:
    """Script + character/word heuristics. Returns language, confidence, alternatives, method."""
    c = script_counts(text)
    if not c:
        return {"script": None, "language": None, "confidence": 0.0, "alternatives": [], "method": "none"}
    script = max(c, key=c.get)
    share = c[script] / sum(c.values())
    if script in SCRIPT_LANG:
        lang, conf, alts = SCRIPT_LANG[script], 0.9 * share, []
        if script == "Bengali" and re.search("[ৰৱ]", text):
            lang, alts = "as", ["bn"]
        elif script == "Bengali":
            alts = ["as"]
        return {"script": script, "language": lang, "confidence": round(conf if not alts else conf * 0.85, 2),
                "alternatives": alts, "method": "script"}
    if script == "Arabic":
        if re.search("[ڄٺڙڪڳڻ]", text):
            return {"script": script, "language": "sd", "confidence": 0.7, "alternatives": ["ur"], "method": "chars"}
        if re.search("[ۍێۆ]", text):
            return {"script": script, "language": "ks", "confidence": 0.7, "alternatives": ["ur"], "method": "chars"}
        return {"script": script, "language": "ur", "confidence": 0.8 * share, "alternatives": ["sd", "ks"], "method": "script"}
    # Devanagari is shared by hi/mr/ne/sa/mai/...: score whole-word markers and distinctive substrings
    words = set(re.findall(r"[\u0900-\u097F]+", text))
    word_marks = {
        "hi": {"है", "और", "का", "की", "के", "में", "नहीं", "हैं", "को", "से", "यह", "था", "पर", "कि", "भी", "एक", "इस", "जो", "थे", "रहा", "गया", "लिए"},
        "mr": {"आहे", "आणि", "नाही", "होते", "होता", "होती", "आहेत", "तुम्ही", "मी", "काय", "या", "हे", "तर", "पण", "म्हणून", "असे", "यांनी"},
        "ne": {"छ", "छन्", "छैन", "हुन्छ", "थियो", "र", "हो", "तपाईं", "मलाई", "पनि", "भने", "यो", "तथा", "मा", "ले", "लाई"},
        "mai": {"अछि", "छथि", "आ", "हमर", "अहाँ", "नहि"},
    }
    sub_marks = {  # substring evidence, each counted once
        "mr": ["ळ", "ांनी", "झाला", "झाली", "झाले", "केले", "केली", "सांगितले", "ऊन ", "ाची ", "ाचे ", "ाना "],
        "ne": ["हरू", "हरु", "सँग", "भएको", "गर्न", "हुने", "हुनु", "पछि", "थिएन", "सम्बन्ध", "यसको", "उनको"],
        "hi": ["ाओं", "ियों", "करने", "रहे", "किया", "गई", "दिया", "सकता", "सकती"],
    }
    score = {k: len(words & v) for k, v in word_marks.items()}
    for k, subs in sub_marks.items():
        score[k] += sum(1 for x in subs if x in text)
    best = max(score, key=score.get)
    tot = sum(score.values())
    if tot == 0:
        return {"script": script, "language": "hi", "confidence": 0.4,
                "alternatives": ["mr", "ne", "sa", "mai"], "method": "script-default"}
    conf = min(0.95, 0.5 + 0.1 * score[best]) * (score[best] / tot) ** 0.5
    alts = [k for k, v in sorted(score.items(), key=lambda kv: -kv[1]) if v and k != best]
    return {"script": script, "language": best, "confidence": round(conf, 2), "alternatives": alts, "method": "words"}


def mixed_script_words(text: str) -> list[str]:
    """Words with letters or vowel signs from two different Indic scripts: a typical OCR misread."""
    import unicodedata
    out = []
    for w in text.split():
        found = set()
        for ch in w:
            if unicodedata.category(ch)[0] in "LM":
                o = ord(ch)
                for name, (a, b) in BLOCKS.items():
                    if a <= o <= b and name != "Latin":
                        found.add(name)
        if len(found) > 1:
            out.append(w)
    return out
