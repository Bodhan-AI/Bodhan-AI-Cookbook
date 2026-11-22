"""Configuration, language tables, pricing and sandboxed path handling."""
from __future__ import annotations

import os
from pathlib import Path

from .errors import ToolError

API_BASE = os.environ.get("BODHAN_API_BASE", "https://api.bodhan.ai").rstrip("/")

KEY_ENV = {
    "transcribe": "BODHAN_TRANSCRIBE_KEY",
    "translate": "BODHAN_TRANSLATE_KEY",
    "speak": "BODHAN_SPEAK_KEY",
    "ocr": "BODHAN_OCR_KEY",
}

# Hosted prices (INR), from the assignment brief.
PRICE_TRANSCRIBE_PER_MIN = 0.10
PRICE_TRANSLATE_PER_10K_TOKENS = 0.20
PRICE_SPEAK_PER_10K_CHARS = 6.0
PRICE_OCR_PER_IMAGE = 0.20

# Model limits
MAX_TRANSCRIBE_WINDOW_S = 30.0
SAFE_TRANSCRIBE_WINDOW_S = 28.0
MAX_TTS_CHARS = 280  # "a sentence or two, about 30 seconds of speech"
MAX_TRANSLATE_CHARS = 6000  # conservative per-request size; we split on paragraph/sentence boundaries

# Language tables (codes from the Bodhan API reference)
STT_LANGS = {
    "as": "Assamese", "bhb": "Bhili", "bho": "Bhojpuri", "bn": "Bengali", "brx": "Bodo", "doi": "Dogri",
    "en": "English", "gu": "Gujarati", "hi": "Hindi", "kn": "Kannada", "kok": "Konkani", "ks": "Kashmiri",
    "mai": "Maithili", "ml": "Malayalam", "mni": "Manipuri", "mr": "Marathi", "ne": "Nepali", "or": "Odia",
    "pa": "Punjabi", "sa": "Sanskrit", "sat": "Santali", "sd": "Sindhi", "ta": "Tamil", "te": "Telugu",
    "ur": "Urdu",
}
TRANSLATE_LANGS = {
    "as": "Assamese", "bn": "Bengali", "brx": "Bodo", "doi": "Dogri", "en": "English", "gu": "Gujarati",
    "hi": "Hindi", "kn": "Kannada", "kok": "Konkani", "ks": "Kashmiri", "ks-Deva": "Kashmiri (Devanagari)",
    "mai": "Maithili", "ml": "Malayalam", "mni": "Manipuri", "mni-Beng": "Manipuri (Bengali)", "mr": "Marathi",
    "ne": "Nepali", "or": "Odia", "pa": "Punjabi", "sa": "Sanskrit", "sat": "Santali", "sd": "Sindhi",
    "sd-Arab": "Sindhi (Perso-Arabic)", "ta": "Tamil", "te": "Telugu", "ur": "Urdu",
}
TTS_LANGS = {
    "as", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "mai", "ml", "mni", "mr", "ne", "or",
    "pa", "sa", "sat", "sd", "ta", "te", "ur",
}
UNSUPPORTED_TRANSLATE = {"bhb", "bho"}

# name/alias -> code, so "Tamil", "tamil", "ta" all work
_ALIASES: dict[str, str] = {}
for _c, _n in {**STT_LANGS, **TRANSLATE_LANGS}.items():
    _ALIASES[_c.lower()] = _c
    _ALIASES[_n.lower()] = _c
_ALIASES.update({"oriya": "or", "panjabi": "pa", "marathi": "mr", "kashmiri": "ks", "sindhi": "sd", "manipuri": "mni"})


def normalize_lang(value: str) -> str:
    key = (value or "").strip().lower()
    if key in _ALIASES:
        return _ALIASES[key]
    raise ToolError(
        "unsupported_language",
        f"'{value}' is not a language Bodhan supports.",
        "Supported: " + ", ".join(f"{c} ({n})" for c, n in sorted(TRANSLATE_LANGS.items())) + ". "
        "If the user asked for an unsupported language, tell them so instead of producing output.",
    )


# Voice library (from the Bodhan API reference): lang -> {"female": [...], "male": [...]}
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
ALL_VOICES = {v for d in VOICES.values() for vs in d.values() for v in vs}
TTS_STYLES = [
    "AIR style news", "Customer Care", "TV style news", "advertisements", "anger", "children's stories",
    "disgust", "educational lecture", "fear", "happy", "news", "sad", "single person narration audiobook",
    "surprise",
]


def pick_voice(lang: str, gender: str = "female") -> str:
    d = VOICES.get(lang) or VOICES["hi"]
    return (d.get(gender) or d["female"])[0]


# ---- sandboxed paths -------------------------------------------------------------------------------------------

def workdir() -> Path:
    return Path(os.environ.get("BODHAN_WORKDIR", ".")).resolve()


def allowed_roots() -> list[Path]:
    roots = [workdir()]
    for p in os.environ.get("BODHAN_EXTRA_ROOTS", "").split(os.pathsep):
        if p.strip():
            roots.append(Path(p).resolve())
    return roots


def safe_path(path: str, must_exist: bool = True) -> Path:
    """Resolve `path` and make sure it lives under an allowed root. Tools only accept file references, and only
    inside the sandbox: prevents a prompt-injected path like /etc/passwd from being read or overwritten."""
    if not path or not isinstance(path, str):
        raise ToolError("invalid_path", "A file path is required.", "Pass a path to a file inside the working directory.")
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = workdir() / p
    p = p.resolve()
    if not any(p == r or r in p.parents for r in allowed_roots()):
        raise ToolError(
            "path_outside_workspace",
            f"'{path}' is outside the allowed workspace.",
            "Only files under the input/output folders can be used. Allowed roots: "
            + ", ".join(str(r) for r in allowed_roots()),
        )
    if must_exist and not p.exists():
        raise ToolError("file_not_found", f"File not found: {path}", "Check the path with list_files or inspect_media.")
    return p


def api_key(kind: str) -> str:
    env = KEY_ENV[kind]
    key = os.environ.get(env, "").strip() or os.environ.get("BODHAN_API_KEY", "").strip()  # one shared key also works
    if not key:
        raise ToolError("missing_api_key", f"{env} is not set.", f"Set {env} (or BODHAN_API_KEY) in the environment; see .env.example.")
    return key
