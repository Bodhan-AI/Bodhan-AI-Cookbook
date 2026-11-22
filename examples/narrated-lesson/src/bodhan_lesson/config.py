"""Settings: API endpoints, thresholds and secrets loaded from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# ---- Ingest -----------------------------------------------------------------
OCR_DPI = 150

# ---- OCR client -------------------------------------------------------------
OCR_BASE_URL = "https://api.bodhan.ai/v1"
OCR_MODEL = "indic-ocr"
OCR_TABLE_FORMAT = "markdown"
# max_tokens applies per block; a 502 means some block exceeded it, so escalate.
OCR_MAX_TOKENS_LADDER = (2048, 4096, 8192)
OCR_TIMEOUT_S = 120.0
OCR_MAX_ATTEMPTS = 4
OCR_BACKOFF_BASE_S = 1.0
OCR_BACKOFF_MAX_S = 30.0
OCR_CONCURRENCY = 3

# ---- Cleanup thresholds -----------------------------------------------------
DOUBTFUL_CONF_THRESHOLD = 0.70
# Rule 5: text shorter than this many characters per bbox pixel of width is "nearly empty".
MIN_CHARS_PER_BBOX_PX = 0.05
# Rule 3: a repeated title in the top fraction of the page is a running header.
RUNNING_HEADER_BAND = 0.20
RUNNING_HEADER_MIN_PAGES = 2
# Rule 8: padding (px) added around a figure crop.
FIGURE_CROP_PADDING_PX = 8
# Rule 11: a block with more than this share of prose characters is not an equation.
PROSE_RATIO_THRESHOLD = 0.30

# Page furniture is dropped by OCR type or label (Rule 3).
FURNITURE_TYPES = frozenset({"PageHeader", "PageFooter", "PageNumber"})
FURNITURE_LABELS = frozenset({"Header", "Footer", "Page-number", "Folio", "Website-link"})
FIGURE_TYPES = frozenset({"Figure", "Picture"})
FIGURE_LABELS = frozenset({"Image", "Diagram", "Chart"})

# Accepted names for the OCR key: the spec's name first, then the name used in .env.
OCR_KEY_NAMES = ("BODHAN_OCR_KEY", "indic-ocr-api")


@dataclass(frozen=True)
class Settings:
    """Runtime secrets. Never logged or written to disk."""

    ocr_key: str


def read_dotenv(path: Path) -> dict[str, str]:
    """Parse a minimal KEY=VALUE .env file; missing file yields an empty dict."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def load_settings(env_path: Path = Path(".env")) -> Settings:
    """Load settings; real environment variables take precedence over .env."""
    file_values = read_dotenv(env_path)
    for name in OCR_KEY_NAMES:
        key = os.environ.get(name) or file_values.get(name)
        if key:
            return Settings(ocr_key=key)
    raise RuntimeError(
        f"No OCR key found. Set one of {', '.join(OCR_KEY_NAMES)} in the environment or {env_path}."
    )


# ---- LLM (OpenAI-compatible; endpoint and model come from the environment) --------
LLM_TIMEOUT_S = 180.0
LLM_MAX_ATTEMPTS = 5
LLM_BACKOFF_BASE_S = 2.0
LLM_BACKOFF_MAX_S = 60.0
LLM_TEMPERATURE = 0.3
LLM_MAX_TOKENS_NARRATION = 3000  # includes reasoning tokens; providers count it against per-request limits
LLM_MAX_TOKENS_FIGURE = 2000
LLM_CACHE_SUBDIR = "llm"
LLM_KEY_NAMES = ("LLM_API_KEY", "groq-api-key")

# ---- Figures ---------------------------------------------------------------------
FIGURE_CONTEXT_CHARS = 300  # surrounding text given to the vision model, each side
FIGURE_MIN_PIXELS = 40  # smaller crops are not worth describing
FIGURE_CONCURRENCY = 2
FIGURE_DESCRIPTION_CHARS = (20, 600)

# ---- Narration ----------------------------------------------------------------------
NARRATION_BATCH_CHARS = 1800  # source characters per LLM call
NARRATION_RETRIES = 2  # extra attempts after a failed validation
EXERCISE_MAX_WORDS = 62  # about 20-25 seconds of speech per exercise problem
EXAMPLE_MAX_WORDS = 120  # worked examples are explained briefly
UNIT_MAX_WORDS = 260


@dataclass(frozen=True)
class LlmSettings:
    """LLM endpoint settings. `vision_model` is optional; without it figures use captions only."""

    base_url: str
    model: str
    api_key: str
    vision_model: str | None = None
    reasoning_effort: str | None = None  # sent only if set ("low" | "medium" | "high")
    exercise_model: str | None = None  # optional model for practice-problem hints; defaults to `model`


def load_llm_settings(env_path: Path = Path(".env")) -> LlmSettings | None:
    """Read LLM settings from the environment or .env; None when not (fully) configured."""
    file_values = read_dotenv(env_path)

    def lookup(*names: str) -> str | None:
        for name in names:
            value = os.environ.get(name) or file_values.get(name)
            if value:
                return value
        return None

    base_url, model, api_key = lookup("LLM_BASE_URL"), lookup("LLM_MODEL"), lookup(*LLM_KEY_NAMES)
    if not (base_url and model and api_key):
        return None
    return LlmSettings(base_url.rstrip("/"), model, api_key, lookup("LLM_VISION_MODEL"), lookup("LLM_REASONING_EFFORT"),
                       lookup("LLM_EXERCISE_MODEL"))


# ---- Bodhan Translate / Speak ------------------------------------------------------
BODHAN_BASE_URL = "https://api.bodhan.ai/v1"
TRANSLATE_KEY_NAMES = ("BODHAN_TRANSLATE_KEY", "indic-translate-api")
SPEAK_KEY_NAMES = ("BODHAN_SPEAK_KEY", "indic-speak-api")
TRANSCRIBE_KEY_NAMES = ("BODHAN_TRANSCRIBE_KEY", "indic-transcribe-api")
BODHAN_TIMEOUT_S = 120.0
BODHAN_MAX_ATTEMPTS = 5
TRANSLATE_MAX_OUTPUT_TOKENS = 2048
TTS_MAX_CHARS = 250  # one chunk is about 25 seconds of speech; the TTS wants a sentence or two
TTS_WORKERS = 6  # per language; languages also run in parallel. 429s are retried with backoff.
TRANSLATE_WORKERS = 4
CER_RESPEAK_THRESHOLD = 0.45  # a chunk whose back-transcription is worse than this is re-spoken once
# Hosted prices (INR) from the spec, used for the cost estimate in the run report.
PRICE_TRANSLATE_PER_10K_TOKENS = 0.20
PRICE_SPEAK_PER_CHAR = 6 / 10000
PRICE_TRANSCRIBE_PER_MIN = 0.10
TTS_STYLE = "educational lecture"
GAP_CHUNK_S, GAP_UNIT_S, GAP_SECTION_S = 0.3, 0.7, 1.2  # silences when assembling audio
SPEAK_LANGUAGES = ("en", "hi", "bn", "mr", "te", "ta", "gu", "kn", "ml", "or", "pa", "as", "ur")
# primary / alternate voice per language (from the Indic-Speak API reference)
VOICES = {
    "en": ("Kavya", "Amit"), "hi": ("Kavya", "Amit"), "bn": ("Ishita", "Sourav"), "mr": ("Anagha", "Chinmay"),
    "te": ("Sravani", "Vamsi"), "ta": ("Anitha", "Arun"), "gu": ("Dhara", "Parth"), "kn": ("Deepika", "Adarsh"),
    "ml": ("Lakshmi", "Kiran"), "or": ("Itishree", "Akash"), "pa": ("Kaur", "Manpreet"),
    "as": ("Prastuti", "Ankur"), "ur": ("Saba", "Zaid"),
}


@dataclass(frozen=True)
class BodhanKeys:
    translate: str
    speak: str
    transcribe: str | None = None  # optional: without it the content-fidelity back-check is skipped


def load_bodhan_keys(env_path: Path = Path(".env")) -> BodhanKeys:
    """Translate and Speak keys; real environment variables take precedence over .env."""
    file_values = read_dotenv(env_path)

    def find(names: tuple[str, ...]) -> str:
        for name in names:
            if value := os.environ.get(name) or file_values.get(name):
                return value
        raise RuntimeError(f"No key found. Set one of {', '.join(names)} in the environment or {env_path}.")

    transcribe = next((v for n in TRANSCRIBE_KEY_NAMES if (v := os.environ.get(n) or file_values.get(n))), None)
    return BodhanKeys(find(TRANSLATE_KEY_NAMES), find(SPEAK_KEY_NAMES), transcribe)
