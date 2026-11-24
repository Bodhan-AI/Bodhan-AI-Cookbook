"""
Constants — Languages, voices, styles for Bodhan AI APIs
"""

# ── Languages ────────────────────────────────────────────────

LANGUAGES = {
    "as":       {"name": "Assamese",                "native": "অসমীয়া",        "tts": True,  "stt": True,  "translate": True,  "transliterate": True,  "codemix": True},
    "bn":       {"name": "Bengali",                 "native": "বাংলা",          "tts": True,  "stt": True,  "translate": True,  "transliterate": True,  "codemix": True},
    "brx":      {"name": "Bodo",                    "native": "बड़ो",            "tts": True,  "stt": False, "translate": True,  "transliterate": True,  "codemix": True},
    "doi":      {"name": "Dogri",                   "native": "डोगरी",           "tts": True,  "stt": True,  "translate": True,  "transliterate": True,  "codemix": True},
    "en":       {"name": "English",                 "native": "English",        "tts": True,  "stt": True,  "translate": True,  "transliterate": False, "codemix": False},
    "gu":       {"name": "Gujarati",                "native": "ગુજરાતી",         "tts": True,  "stt": True,  "translate": True,  "transliterate": True,  "codemix": True},
    "hi":       {"name": "Hindi",                   "native": "हिन्दी",           "tts": True,  "stt": True,  "translate": True,  "transliterate": True,  "codemix": True},
    "kn":       {"name": "Kannada",                 "native": "ಕನ್ನಡ",           "tts": True,  "stt": True,  "translate": True,  "transliterate": True,  "codemix": True},
    "kok":      {"name": "Konkani",                 "native": "कोंकणी",          "tts": True,  "stt": True,  "translate": True,  "transliterate": True,  "codemix": True},
    "ks":       {"name": "Kashmiri",                "native": "كٲشُر",          "tts": True,  "stt": True,  "translate": True,  "transliterate": True,  "codemix": True},
    "ks-Deva":  {"name": "Kashmiri (Devanagari)",   "native": "कॉशुर",          "tts": False, "stt": False, "translate": True,  "transliterate": True,  "codemix": False},
    "mai":      {"name": "Maithili",                "native": "मैथिली",          "tts": True,  "stt": True,  "translate": True,  "transliterate": True,  "codemix": True},
    "ml":       {"name": "Malayalam",               "native": "മലയാളം",         "tts": True,  "stt": True,  "translate": True,  "transliterate": True,  "codemix": True},
    "mni":      {"name": "Manipuri (Meitei)",       "native": "ꯃꯤꯇꯩꯂꯣꯟ",        "tts": True,  "stt": True,  "translate": True,  "transliterate": True,  "codemix": True},
    "mni-Beng": {"name": "Manipuri (Bengali)",      "native": "মণিপুরী",         "tts": False, "stt": False, "translate": True,  "transliterate": True,  "codemix": True},
    "mr":       {"name": "Marathi",                 "native": "मराठी",           "tts": True,  "stt": True,  "translate": True,  "transliterate": True,  "codemix": True},
    "ne":       {"name": "Nepali",                  "native": "नेपाली",          "tts": True,  "stt": True,  "translate": True,  "transliterate": True,  "codemix": True},
    "or":       {"name": "Odia",                    "native": "ଓଡ଼ିଆ",           "tts": True,  "stt": True,  "translate": True,  "transliterate": True,  "codemix": True},
    "pa":       {"name": "Punjabi",                 "native": "ਪੰਜਾਬੀ",          "tts": True,  "stt": True,  "translate": True,  "transliterate": True,  "codemix": True},
    "sa":       {"name": "Sanskrit",                "native": "संस्कृतम्",        "tts": True,  "stt": True,  "translate": True,  "transliterate": True,  "codemix": True},
    "sat":      {"name": "Santali",                 "native": "ᱥᱟᱱᱛᱟᱲᱤ",         "tts": True,  "stt": True,  "translate": True,  "transliterate": True,  "codemix": True},
    "sd":       {"name": "Sindhi (Devanagari)",     "native": "सिन्धी",          "tts": True,  "stt": True,  "translate": True,  "transliterate": True,  "codemix": False},
    "sd-Arab":  {"name": "Sindhi (Perso-Arabic)",   "native": "سنڌي",           "tts": False, "stt": False, "translate": True,  "transliterate": True,  "codemix": False},
    "ta":       {"name": "Tamil",                   "native": "தமிழ்",           "tts": True,  "stt": True,  "translate": True,  "transliterate": True,  "codemix": True},
    "te":       {"name": "Telugu",                  "native": "తెలుగు",          "tts": True,  "stt": True,  "translate": True,  "transliterate": True,  "codemix": True},
    "ur":       {"name": "Urdu",                    "native": "اُردُو",           "tts": True,  "stt": True,  "translate": True,  "transliterate": True,  "codemix": True},
}

# STT-only languages (not in translation/transliteration)
STT_ONLY_LANGUAGES = {
    "bhb": {"name": "Bhili",    "native": "भीली"},
    "bho": {"name": "Bhojpuri", "native": "भोजपुरी"},
}


def get_language_name(code: str) -> str:
    if code in LANGUAGES:
        return LANGUAGES[code]["name"]
    if code in STT_ONLY_LANGUAGES:
        return STT_ONLY_LANGUAGES[code]["name"]
    return code


def get_native_name(code: str) -> str:
    if code in LANGUAGES:
        return LANGUAGES[code]["native"]
    if code in STT_ONLY_LANGUAGES:
        return STT_ONLY_LANGUAGES[code]["native"]
    return code


def get_translation_languages() -> dict:
    """Languages available as translation targets."""
    return {k: v for k, v in LANGUAGES.items() if v["translate"]}


def get_tts_languages() -> dict:
    """Languages available for TTS."""
    return {k: v for k, v in LANGUAGES.items() if v["tts"]}


def get_stt_languages() -> dict:
    """All languages available for STT (includes bhb, bho)."""
    stt = {k: v for k, v in LANGUAGES.items() if v["stt"]}
    for k, v in STT_ONLY_LANGUAGES.items():
        stt[k] = {**v, "stt": True, "tts": False, "translate": False, "transliterate": False, "codemix": False}
    return stt


def language_display_options(lang_dict: dict) -> dict:
    """Return {display_string: code} for use in selectboxes."""
    return {f"{v['native']}  —  {v['name']}": k for k, v in lang_dict.items()}


# ── Voices ───────────────────────────────────────────────────

VOICES = {
    # Assamese
    "Prastuti":  {"gender": "female", "language": "Assamese",  "code": "as"},
    "Ankur":     {"gender": "male",   "language": "Assamese",  "code": "as"},
    # Bengali
    "Ishita":    {"gender": "female", "language": "Bengali",   "code": "bn"},
    "Sourav":    {"gender": "male",   "language": "Bengali",   "code": "bn"},
    # Bodo
    "Gwrbw":     {"gender": "female", "language": "Bodo",      "code": "brx"},
    "Sansuma":   {"gender": "male",   "language": "Bodo",      "code": "brx"},
    # Dogri
    "Preeti":    {"gender": "female", "language": "Dogri",     "code": "doi"},
    "Sham":      {"gender": "male",   "language": "Dogri",     "code": "doi"},
    # Gujarati
    "Dhara":     {"gender": "female", "language": "Gujarati",  "code": "gu"},
    "Parth":     {"gender": "male",   "language": "Gujarati",  "code": "gu"},
    # Hindi
    "Kavya":     {"gender": "female", "language": "Hindi",     "code": "hi"},
    "Suhani":    {"gender": "female", "language": "Hindi",     "code": "hi"},
    "Amit":      {"gender": "male",   "language": "Hindi",     "code": "hi"},
    # Kannada
    "Deepika":   {"gender": "female", "language": "Kannada",   "code": "kn"},
    "Adarsh":    {"gender": "male",   "language": "Kannada",   "code": "kn"},
    # Konkani
    "Anjali":    {"gender": "female", "language": "Konkani",   "code": "kok"},
    "Sandeep":   {"gender": "male",   "language": "Konkani",   "code": "kok"},
    # Kashmiri
    "Zoon":      {"gender": "female", "language": "Kashmiri",  "code": "ks"},
    "Ishfaq":    {"gender": "male",   "language": "Kashmiri",  "code": "ks"},
    # Maithili
    "Vaidehi":   {"gender": "female", "language": "Maithili",  "code": "mai"},
    "Madhukar":  {"gender": "male",   "language": "Maithili",  "code": "mai"},
    # Malayalam
    "Lakshmi":   {"gender": "female", "language": "Malayalam",  "code": "ml"},
    "Kiran":     {"gender": "male",   "language": "Malayalam",  "code": "ml"},
    # Manipuri
    "Thoibi":    {"gender": "female", "language": "Manipuri",  "code": "mni"},
    "Chaoba":    {"gender": "male",   "language": "Manipuri",  "code": "mni"},
    # Marathi
    "Anagha":    {"gender": "female", "language": "Marathi",   "code": "mr"},
    "Chinmay":   {"gender": "male",   "language": "Marathi",   "code": "mr"},
    # Nepali
    "Srijana":   {"gender": "female", "language": "Nepali",    "code": "ne"},
    "Sagar":     {"gender": "male",   "language": "Nepali",    "code": "ne"},
    # Odia
    "Itishree":  {"gender": "female", "language": "Odia",      "code": "or"},
    "Akash":     {"gender": "male",   "language": "Odia",      "code": "or"},
    # Punjabi
    "Kaur":      {"gender": "female", "language": "Punjabi",   "code": "pa"},
    "Manpreet":  {"gender": "male",   "language": "Punjabi",   "code": "pa"},
    # Sanskrit
    "Bharati":   {"gender": "female", "language": "Sanskrit",  "code": "sa"},
    "Aryaman":   {"gender": "male",   "language": "Sanskrit",  "code": "sa"},
    # Santali
    "Phulmani":  {"gender": "female", "language": "Santali",   "code": "sat"},
    "Sibu":      {"gender": "male",   "language": "Santali",   "code": "sat"},
    # Sindhi
    "Moomal":    {"gender": "female", "language": "Sindhi",    "code": "sd"},
    "Rano":      {"gender": "male",   "language": "Sindhi",    "code": "sd"},
    # Tamil
    "Anitha":    {"gender": "female", "language": "Tamil",     "code": "ta"},
    "Arun":      {"gender": "male",   "language": "Tamil",     "code": "ta"},
    # Telugu
    "Sravani":   {"gender": "female", "language": "Telugu",    "code": "te"},
    "Vamsi":     {"gender": "male",   "language": "Telugu",    "code": "te"},
    # Urdu
    "Saba":      {"gender": "female", "language": "Urdu",      "code": "ur"},
    "Zaid":      {"gender": "male",   "language": "Urdu",      "code": "ur"},
}


def get_default_voice(lang_code: str) -> str:
    """Get default female voice name for a language code."""
    for name, info in VOICES.items():
        if info["code"] == lang_code and info["gender"] == "female":
            return name
    return "Kavya"  # Fallback


def get_voices_for_language(lang_code: str) -> dict:
    """Get voices recorded in a specific language."""
    return {name: info for name, info in VOICES.items() if info["code"] == lang_code}


def voice_display_options() -> dict:
    """Return {display_string: voice_name} for selectboxes."""
    options = {}
    for name, info in VOICES.items():
        options[f"{name} ({info['language']} / {info['gender'].title()})"] = name
    return options


# ── TTS Styles ───────────────────────────────────────────────

TTS_STYLES = {
    "Neutral (Standard)":   None,
    "Audiobook Narration":  "single person narration audiobook",
    "Children's Story":     "children's stories",
    "Educational Lecture":  "educational lecture",
    "News Broadcast":       "news",
    "TV Style News":        "TV style news",
    "AIR Style News":       "AIR style news",
    "Customer Care":        "Customer Care",
    "Commercial":           "advertisements",
    "Happy / Upbeat":       "happy",
    "Sad / Melancholic":    "sad",
    "Angry / Assertive":    "anger",
    "Fear / Suspense":      "fear",
    "Surprise":             "surprise",
    "Disgust":              "disgust",
}

SCRIPT_MODES = {
    "Native Script": "native",
    "Roman (Latin letters)": "roman",
    "Code-Mix (Native + English)": "codemix",
}


# ── Environment & Per-Model API Key Helper ───────────────────

import os

def load_env_file(path: str = ".env") -> dict:
    """Load key-value pairs from .env into os.environ if not already set."""
    loaded = {}
    if not os.path.exists(path):
        return loaded
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip("'\"")
                    if val:
                        loaded[key] = val
                        os.environ[key] = val
    except Exception as e:
        print(f"Notice: Error loading .env: {e}")
    return loaded

# Load .env immediately when constants is imported
ENV_KEYS = load_env_file()


def get_service_key(session_state: dict, service: str) -> str:
    """
    Get the API key for a specific service from session_state or environment variables.
    Services: 'ocr', 'translate', 'tts', 'stt', 'transliterate'
    """
    key_mapping = {
        "ocr": ("api_key_ocr", "BODHAN_API_KEY_OCR"),
        "translate": ("api_key_translate", "BODHAN_API_KEY_TRANSLATE"),
        "tts": ("api_key_tts", "BODHAN_API_KEY_TTS"),
        "stt": ("api_key_stt", "BODHAN_API_KEY_STT"),
        "transliterate": ("api_key_transliterate", "BODHAN_API_KEY_TRANSLITERATE"),
    }

    state_key, env_var = key_mapping.get(service, ("", ""))

    # 1. Check session_state
    val = session_state.get(state_key, "").strip() if state_key else ""
    if val:
        return val

    # 2. Check environment variable (from .env)
    val = os.getenv(env_var, "").strip()
    if val:
        return val

    # 3. Transliteration falls back to translation
    if service == "transliterate":
        val = session_state.get("api_key_translate", "").strip() or os.getenv("BODHAN_API_KEY_TRANSLATE", "").strip()
        if val:
            return val

    # 4. Global fallback
    return session_state.get("api_key", "").strip() or os.getenv("BODHAN_API_KEY", "").strip()



