"""config: Settings read from the environment (and an optional .env file).
languages: Language table: codes, scripts and default Indic-Speak voices.
taxonomy: Error taxonomy: the 10 categories, severities, label aliases and MQM weights.
thresholds: Decision thresholds.
logging_setup: Structured logging: human-readable console + JSON lines in <out>/logs/run.jsonl.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

# -------------------- config
DEFAULT_BODHAN_BASE_URL = "https://api.bodhan.ai"


TRANSLATE_PATH = "/v1/chat/completions"  # live API; the documented /translate returns 404


SPEECH_PATH = "/v1/audio/speech"


TRANSCRIBE_PATH = "/v1/audio/transcriptions"


MODEL_TRANSLATE = "indic-translate"


MODEL_SPEAK = "indic-speak"


MODEL_TRANSCRIBE = "indic-transcribe"


@dataclass(frozen=True)
class Prices:
    """Hosted prices in INR (from the assignment brief)."""

    translate_per_10k_output_tokens: float = 0.20
    speak_per_10k_input_chars: float = 6.0
    transcribe_per_audio_minute: float = 0.10


@dataclass
class Settings:
    bodhan_base_url: str = DEFAULT_BODHAN_BASE_URL
    translate_key: str | None = None
    speak_key: str | None = None
    transcribe_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    prices: Prices = field(default_factory=Prices)

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_model and self.llm_api_key)

    def redacted(self) -> dict:
        """Settings safe to log or write to run_meta.json (never the keys)."""
        return {
            "bodhan_base_url": self.bodhan_base_url,
            "translate_key_set": bool(self.translate_key),
            "speak_key_set": bool(self.speak_key),
            "transcribe_key_set": bool(self.transcribe_key),
            "llm_base_url": self.llm_base_url,
            "llm_model": self.llm_model,
            "llm_key_set": bool(self.llm_api_key),
        }


def _env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def _normalise_base_url(url: str) -> str:
    url = url.rstrip("/")
    # The brief quotes https://api.bodhan.ai/v1; endpoint paths already carry /v1 where needed.
    if url.endswith("/v1"):
        url = url[: -len("/v1")]
    return url


def load_settings(env_file: str | Path | None = None) -> Settings:
    if env_file:
        load_dotenv(env_file, override=False)
    else:
        found = find_dotenv(usecwd=True)
        if found:
            load_dotenv(found, override=False)
    shared = _env("BODHAN_API_KEY")  # Cookbook docs use one key; per-model keys win when set
    return Settings(
        bodhan_base_url=_normalise_base_url(_env("BODHAN_BASE_URL") or DEFAULT_BODHAN_BASE_URL),
        translate_key=_env("BODHAN_TRANSLATE_KEY") or shared,
        speak_key=_env("BODHAN_SPEAK_KEY") or shared,
        transcribe_key=_env("BODHAN_TRANSCRIBE_KEY") or shared,
        llm_base_url=_env("LLM_BASE_URL"),
        llm_model=_env("LLM_MODEL"),
        llm_api_key=_env("LLM_API_KEY"),
    )


# -------------------- languages
@dataclass(frozen=True)
class Language:
    code: str
    name: str
    script: str  # Unicode script name used by the script detector (Part 2)
    voices: tuple[str, ...]  # Indic-Speak voices, female first
    tier: str  # "production" | "preview"


_LANGS = [
    # --- Indic-Speak production tier (13) ---
    Language("en", "English", "Latin", ("Kavya", "Amit"), "production"),
    Language("hi", "Hindi", "Devanagari", ("Kavya", "Suhani", "Amit"), "production"),
    Language("bn", "Bengali", "Bengali", ("Ishita", "Sourav"), "production"),
    Language("mr", "Marathi", "Devanagari", ("Anagha", "Chinmay"), "production"),
    Language("te", "Telugu", "Telugu", ("Sravani", "Vamsi"), "production"),
    Language("ta", "Tamil", "Tamil", ("Anitha", "Arun"), "production"),
    Language("gu", "Gujarati", "Gujarati", ("Dhara", "Parth"), "production"),
    Language("kn", "Kannada", "Kannada", ("Deepika", "Adarsh"), "production"),
    Language("ml", "Malayalam", "Malayalam", ("Lakshmi", "Kiran"), "production"),
    Language("or", "Odia", "Oriya", ("Itishree", "Akash"), "production"),
    Language("pa", "Punjabi", "Gurmukhi", ("Kaur", "Manpreet"), "production"),
    Language("as", "Assamese", "Bengali", ("Prastuti", "Ankur"), "production"),
    Language("ur", "Urdu", "Arabic", ("Saba", "Zaid"), "production"),
    # --- Indic-Speak preview tier (10) ---
    Language("brx", "Bodo", "Devanagari", ("Gwrbw", "Sansuma"), "preview"),
    Language("doi", "Dogri", "Devanagari", ("Preeti", "Sham"), "preview"),
    Language("kok", "Konkani", "Devanagari", ("Anjali", "Sandeep"), "preview"),
    Language("ks", "Kashmiri", "Arabic", ("Zoon", "Ishfaq"), "preview"),
    Language("mai", "Maithili", "Devanagari", ("Vaidehi", "Madhukar"), "preview"),
    Language("ne", "Nepali", "Devanagari", ("Srijana", "Sagar"), "preview"),
    Language("mni", "Manipuri", "Meetei_Mayek", ("Thoibi", "Chaoba"), "preview"),
    Language("sa", "Sanskrit", "Devanagari", ("Bharati", "Aryaman"), "preview"),
    Language("sat", "Santali", "Ol_Chiki", ("Phulmani", "Sibu"), "preview"),
    Language("sd", "Sindhi", "Devanagari", ("Moomal", "Rano"), "preview"),
]


LANGUAGES: dict[str, Language] = {lang.code: lang for lang in _LANGS}


PRODUCTION_CODES: tuple[str, ...] = tuple(lang.code for lang in _LANGS if lang.tier == "production")


class UnknownLanguageError(ValueError):
    pass


def get_language(code: str) -> Language:
    try:
        return LANGUAGES[code.strip().lower()]
    except KeyError as exc:
        known = ", ".join(sorted(LANGUAGES))
        raise UnknownLanguageError(f"Unknown language code {code!r}. Known codes: {known}") from exc


def parse_langs(spec: str) -> tuple[list[str], list[str]]:
    """Parse a ``--langs`` value such as ``hi,ta,bn``.

    Returns (codes, warnings). Raises UnknownLanguageError for unknown codes.
    """
    codes: list[str] = []
    warnings: list[str] = []
    for raw in spec.split(","):
        raw = raw.strip()
        if not raw:
            continue
        lang = get_language(raw)
        if lang.code not in codes:
            codes.append(lang.code)
        if lang.tier != "production":
            warnings.append(f"{lang.code} ({lang.name}) is an Indic-Speak preview-tier language; speech quality may be lower.")
    if not codes:
        raise UnknownLanguageError("--langs must name at least one language code")
    return codes, warnings


def default_voice(code: str) -> str:
    return get_language(code).voices[0]


# -------------------- taxonomy
TEXT_CATEGORIES = (
    "omission",
    "addition",
    "mistranslation",
    "number_unit",
    "term_entity",
    "format",
    "wrong_language_script",
)


SPEECH_CATEGORIES = ("speech_omission_truncation", "speech_repetition", "speech_garbled")


CATEGORIES = TEXT_CATEGORIES + SPEECH_CATEGORIES


SEVERITIES = ("minor", "major", "critical")


MQM_WEIGHTS = {"minor": 1, "major": 5, "critical": 25}


_ALIASES = {
    "omission": ["omission", "omitted", "missing", "deletion", "missing_content", "translation_omission"],
    "addition": ["addition", "added", "insertion", "extra", "hallucination", "translation_addition"],
    "mistranslation": ["mistranslation", "mistranslated", "wrong_meaning", "accuracy", "meaning"],
    "number_unit": ["number", "numbers", "number_unit", "number_or_unit", "unit", "units", "numeric", "date", "quantity", "digit"],
    "term_entity": ["term", "entity", "term_entity", "term_or_entity", "terminology", "named_entity", "ner", "name"],
    "format": ["format", "formatting", "markdown", "latex", "table", "structure", "markup"],
    "wrong_language_script": [
        "wrong_language", "wrong_script", "language", "script", "untranslated", "wrong_language_or_script",
        "wrong_language_script", "language_script",
    ],
    "speech_omission_truncation": [
        "speech_omission", "truncation", "truncated", "speech_truncation", "audio_omission", "speech_omission_truncation",
        "speech_omission_or_truncation", "omission_or_truncation",
    ],
    "speech_repetition": ["repetition", "repeat", "repeated", "loop", "looping", "speech_repetition", "audio_repetition"],
    "speech_garbled": ["garbled", "garbled_audio", "noise", "silence", "unintelligible", "speech_garbled", "audio_garbled", "distortion"],
}


_ALIAS_MAP = {alias: cat for cat, aliases in _ALIASES.items() for alias in aliases}


_SEVERITY_ALIASES = {
    "minor": "minor", "low": "minor", "1": "minor", "small": "minor",
    "major": "major", "medium": "major", "moderate": "major", "2": "major",
    "critical": "critical", "high": "critical", "severe": "critical", "3": "critical",
}


def _slug(raw: str) -> str:
    return re.sub(r"[\s\-/]+", "_", str(raw).strip().lower())


def canonical_category(raw: str) -> str:
    """Map a label type to one of CATEGORIES, or ``unmapped:<raw>`` (never raises)."""
    slug = _slug(raw)
    if slug in _ALIAS_MAP:
        return _ALIAS_MAP[slug]
    # "speech omission" style labels where the area is a prefix
    for prefix in ("speech_", "audio_", "translation_", "text_"):
        if slug.startswith(prefix) and slug[len(prefix):] in _ALIAS_MAP:
            base = _ALIAS_MAP[slug[len(prefix):]]
            if prefix in ("speech_", "audio_") and base == "omission":
                return "speech_omission_truncation"
            if prefix in ("translation_", "text_") or base.startswith("speech_"):
                return base
    return f"unmapped:{slug}"


def canonical_severity(raw: str | None) -> str | None:
    if raw is None:
        return None
    return _SEVERITY_ALIASES.get(_slug(raw))


def is_speech(category: str) -> bool:
    return category in SPEECH_CATEGORIES


# -------------------- thresholds
DEFAULTS: dict[str, float] = {
    # --- statistics -------------------------------------------------------------
    "robust_z": 3.5,  # |robust z| above this = outlier vs the run's other items (same language)
    "min_stat_items": 8,  # minimum items of a language before its run statistics are trusted
    # --- script / language ------------------------------------------------------
    "script_min_letters": 5,  # fewer letters than this: too short to judge the script
    "script_ratio_min": 0.5,  # expected-script share of letters below this = wrong script
    "latin_run_major_words": 4,  # >= this many consecutive English words outside code/LaTeX/URLs
    "latin_run_minor_words": 3,
    "foreign_indic_run_words": 2,  # words in another Indic script
    # --- audio ------------------------------------------------------------------
    "silence_rel_db": 35.0,  # frame is "silent" if this many dB below the clip's speech level
    "silence_gap_s": 1.2,  # internal pause longer than this = suspicious
    "edge_silence_s": 2.0,  # leading/trailing silence longer than this
    "clip_level": 0.999,  # |sample| at or above this counts as clipped
    "clip_run_samples": 3,  # consecutive clipped samples that make a clipping event
    "noise_flatness": 0.4,  # spectral flatness: white noise ~0.56 (e^-gamma), real Indic-Speak voiced frames <= 0.23 (measured)
    "noise_run_s": 0.35,  # noise-like stretch longer than this (fricatives are shorter)
    "noise_floor_abs_db": -55.0,  # floor must also be above this to call a floor outlier "noise"
    # --- round trips (Block 5): rule parameters, not data statistics ------------------
    # The data-driven noise floors themselves are computed per run from clean items and
    # written to <out>/thresholds.generated.json (roundtrip.py); these only shape the rules.
    "floor_low_pct": 5.0,  # back-translation precision/recall: below the 5th percentile of clean items = beyond floor
    "floor_high_pct": 95.0,  # ASR residual WER/CER: above the 95th percentile of clean items = beyond floor
    "floor_min_items": 15,  # per-language floor needs this many clean items; otherwise pooled across languages
    "floor_min_pooled": 5,  # fewer clean items than this even pooled: no floor, no decision
    "asr_spelling_similarity": 0.75,  # mark-stripped character similarity at/above this = spelling variant (noise)
    "repeat_ngram_min": 2,  # an inserted n-gram of at least this many words that repeats the words before it
    "repeat_strong_words": 3,  # a repeat this long is confirmed without the duration check
    "duration_confirm_z": 2.0,  # speaking-time-per-char robust z beyond +/- this confirms truncation / repetition
    "truncation_min_words": 1,  # unexplained words missing at the end of the transcript
    # --- LLM judge / fusion (Block 6) ----------------------------------------------
    # Judge errors below this confidence are not used for flags (still recorded). Calibration
    # may tune it globally, or per category as "judge_min_confidence.<category>".
    "judge_min_confidence": 0.5,
}


PER_CATEGORY_PREFIX = "judge_min_confidence."


def load_thresholds(path: str | Path | None = None) -> dict[str, float]:
    th = dict(DEFAULTS)
    if path and Path(path).exists():
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        th.update({k: float(v) for k, v in data.get("thresholds", data).items() if k in DEFAULTS or k.startswith(PER_CATEGORY_PREFIX)})
    return th


# -------------------- logging_setup
class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": round(record.created, 3),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if fields:
            payload.update(fields)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = getattr(record, "fields", None) or {}
        extra = " ".join(f"{k}={v}" for k, v in fields.items())
        return f"{record.levelname:<7} {record.name.removeprefix('bodhan_eval.')}: {record.getMessage()} {extra}".rstrip()


def make_console_safe() -> None:
    """Never crash on characters the console can't encode (e.g. the rupee sign on Windows cp1252)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def setup_logging(out_dir: Path | None, level: str = "INFO") -> Path | None:
    make_console_safe()
    root = logging.getLogger("bodhan_eval")
    root.setLevel(logging.DEBUG)
    root.propagate = False
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(getattr(logging, level.upper(), logging.INFO))
    console.setFormatter(ConsoleFormatter())
    root.addHandler(console)
    log_path = None
    if out_dir is not None:
        log_dir = Path(out_dir) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "run.jsonl"
        fh = logging.FileHandler(log_path, mode="w", encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(JsonLineFormatter())
        root.addHandler(fh)
    # keep third-party noise out of the console
    for noisy in ("httpx", "httpcore", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return log_path


def close_logging() -> None:
    root = logging.getLogger("bodhan_eval")
    for h in list(root.handlers):
        h.flush()
        h.close()
        root.removeHandler(h)
