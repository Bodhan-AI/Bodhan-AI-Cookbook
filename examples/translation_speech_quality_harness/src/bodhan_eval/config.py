import os
from pathlib import Path
from typing import TypedDict

from dotenv import load_dotenv

load_dotenv()

CONFIG_DIR = Path(__file__).resolve().parent


class CheckThreshold(TypedDict):
    value: float
    origin: str


CHECK_THRESHOLDS: dict[str, CheckThreshold] = {
    "speech_silence_ratio": {
        "value": 0.8,
        "origin": "Default cutoff for the deterministic silence check.",
    },
    "clipping_peak": {
        "value": 0.999,
        "origin": "Near-full-scale peak cutoff for PCM clipping.",
    },
    "text_length_mad_multiplier": {
        "value": 3.0,
        "origin": "Robust per-language median plus/minus MAD default.",
    },
    "text_length_min_ratio": {
        "value": 0.05,
        "origin": "Lower bound preventing a near-zero text-length floor.",
    },
    "text_length_default_mad": {
        "value": 0.1,
        "origin": "Fallback MAD when a language has no run-derived length statistics.",
    },
    "text_length_min_items": {
        "value": 8,
        "origin": "Documented default: minimum items of one language before its run-derived length statistics are trusted.",
    },
    "speech_max_duration_s": {
        "value": 30.0,
        "origin": "Single-request speech duration limit used by the speech check.",
    },
    "voiced_peak": {
        "value": 0.02,
        "origin": "Peak amplitude cutoff used to identify voiced audio frames.",
    },
    "speech_chunk_max_duration_s": {
        "value": 28.0,
        "origin": "Chunk target kept below the speech endpoint duration limit.",
    },
    "speech_min_active_duration_s": {
        "value": 0.25,
        "origin": "Below this voiced duration, a long ASR transcript is treated as suspect.",
    },
    "speech_max_words_per_second": {
        "value": 5.0,
        "origin": "Conservative spoken-word rate cap for corroborating ASR repetition.",
    },
}

CHECK_DEFAULTS: dict[str, dict[str, float]] = {
    "confidence": {
        "text_numbers": 0.95,
        "text_language_latin": 0.95,
        "text_language_ambiguous": 0.75,
        "text_format": 0.9,
        "text_length": 0.55,
        "roundtrip": 0.55,
        "speech_silence": 0.8,
        "speech_duration": 0.5,
    },
}

COSTS: dict[str, float] = {
    "translate": 0.20 / 10_000,
    "speak": 6.00 / 10_000,
    "transcribe": 0.10 / 60,
}

DEFAULT_MAX_COST_INR = 50.0
DEFAULT_CALIBRATION_THRESHOLD = 0.5
JUDGE_MAX_FINDINGS_PER_ITEM = 5
JUDGE_DEFAULT_CONFIDENCE = 0.5
GENERATED_TRANSLATION_MAX_OUTPUT_TOKENS = 512
GENERATED_TRANSLATION_TRUNCATION_RETRIES = 1
MAX_JSONL_LINE_BYTES = 5 * 1024 * 1024
MAX_AUDIO_FILE_BYTES = 50 * 1024 * 1024

BODHAN_TRANSLATE_KEY = os.getenv("BODHAN_TRANSLATE_KEY")
BODHAN_SPEAK_KEY = os.getenv("BODHAN_SPEAK_KEY")
BODHAN_TRANSCRIBE_KEY = os.getenv("BODHAN_TRANSCRIBE_KEY")
LLM_BASE_URL = os.getenv("LLM_BASE_URL")
LLM_MODEL = os.getenv("LLM_MODEL")
LLM_API_KEY = os.getenv("LLM_API_KEY")
