"""Settings, limits, and the lists of languages, voices and styles."""

import os
from pathlib import Path

from dotenv import load_dotenv

from .errors import MissingKeyError

load_dotenv()  # reads the .env file, if there is one

BASE_URL = os.environ.get("BODHAN_BASE_URL", "https://api.bodhan.ai").rstrip("/")


MAX_INPUT_CHARS = 2000
CHUNK_LIMIT = 250

# Pauses between audio clips, in milliseconds.
SENTENCE_PAUSE_MS = 300
PARAGRAPH_PAUSE_MS = 700
SPEAK_WORKERS = 3
DB_PATH = Path(os.environ.get("NARRATOR_DB", "narrator.db"))
HISTORY_LIMIT = 20

LANGUAGES = {
    "hi": "Hindi (हिन्दी)",
    "bn": "Bengali (বাংলা)",
    "mr": "Marathi (मराठी)",
    "ta": "Tamil (தமிழ்)",
    "te": "Telugu (తెలుగు)",
    "gu": "Gujarati (ગુજરાતી)",
    "kn": "Kannada (ಕನ್ನಡ)",
    "ml": "Malayalam (മലയാളം)",
    "pa": "Punjabi (ਪੰਜਾਬੀ)",
    "or": "Odia (ଓଡ଼ିଆ)",
    "as": "Assamese (অসমীয়া)",
    "ur": "Urdu (اردو)",
    "ne": "Nepali (नेपाली)",
    "sa": "Sanskrit (संस्कृतम्)",
    "kok": "Konkani (कोंकणी)",
    "mai": "Maithili (मैथिली)",
    "doi": "Dogri (डोगरी)",
    "brx": "Bodo (बड़ो)",
    "sat": "Santali (ᱥᱟᱱᱛᱟᱲᱤ)",
    "mni": "Manipuri (ꯃꯤꯇꯩꯂꯣꯟ)",
    "ks": "Kashmiri (کٲشُر)",
    "sd": "Sindhi (सिन्धी)",
    "en": "English",
}

VOICES = {
    "hi": ["Kavya", "Suhani", "Amit"],
    "bn": ["Ishita", "Sourav"],
    "mr": ["Anagha", "Chinmay"],
    "ta": ["Anitha", "Arun"],
    "te": ["Sravani", "Vamsi"],
    "gu": ["Dhara", "Parth"],
    "kn": ["Deepika", "Adarsh"],
    "ml": ["Lakshmi", "Kiran"],
    "pa": ["Kaur", "Manpreet"],
    "or": ["Itishree", "Akash"],
    "as": ["Prastuti", "Ankur"],
    "ur": ["Saba", "Zaid"],
    "ne": ["Srijana", "Sagar"],
    "sa": ["Bharati", "Aryaman"],
    "kok": ["Anjali", "Sandeep"],
    "mai": ["Vaidehi", "Madhukar"],
    "doi": ["Preeti", "Sham"],
    "brx": ["Gwrbw", "Sansuma"],
    "sat": ["Phulmani", "Sibu"],
    "mni": ["Thoibi", "Chaoba"],
    "ks": ["Zoon", "Ishfaq"],
    "sd": ["Moomal", "Rano"],
    "en": ["Kavya", "Amit"],
}
DEFAULT_VOICE = {code: names[0] for code, names in VOICES.items()}

# Label shown to the user -> value sent to Bodhan (None means: leave the style out).
STYLES = {
    "Neutral": None,
    "Audiobook narration": "single person narration audiobook",
    "News": "news",
}

# Where saved history lives. It is git-ignored.
DATA_DIR = Path(
    os.environ.get("NARRATOR_DATA_DIR")
    or Path(__file__).resolve().parent.parent / "data"
)

_PLACEHOLDERS = {"", "paste_your_real_key_here", "your_api_key_here"}


def get_api_key(kind):
    """Return the key for 'translate' or 'speak'.

    A separate key is used if one is set (BODHAN_TRANSLATE_API_KEY or
    BODHAN_SPEAK_API_KEY); otherwise the shared BODHAN_API_KEY.
    """
    specific = {
        "translate": "BODHAN_TRANSLATE_API_KEY",
        "speak": "BODHAN_SPEAK_API_KEY",
    }[kind]
    key = os.environ.get(specific) or os.environ.get("BODHAN_API_KEY") or ""
    if key.strip() in _PLACEHOLDERS:
        raise MissingKeyError(
            "No Bodhan API key found. Copy .env.example to .env and put your key in it."
        )
    return key.strip()

# Languages whose voice reads numbers in-language when given native digits.
NATIVE_DIGITS = {"hi": "०१२३४५६७८९"}