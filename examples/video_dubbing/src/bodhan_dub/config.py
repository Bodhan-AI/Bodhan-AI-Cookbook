"""Settings for a clean checkout and for deployment to another LLM endpoint."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

# Bodhan's published production tiers and hosted voice catalog. English uses
# an available fixed voice with the English language instruction.
VOICES = {
    "as": "Prastuti", "bn": "Ishita", "en": "Kavya", "gu": "Dhara",
    "hi": "Kavya", "kn": "Deepika", "ml": "Lakshmi", "mr": "Anagha",
    "or": "Itishree", "pa": "Kaur", "ta": "Anitha", "te": "Sravani", "ur": "Saba",
}
CATALOG = set(("""
Prastuti Ankur Ishita Sourav Gwrbw Sansuma Preeti Sham Dhara Parth Kavya Suhani
Amit Deepika Adarsh Anjali Sandeep Zoon Ishfaq Vaidehi Madhukar Lakshmi Kiran
Thoibi Chaoba Anagha Chinmay Srijana Sagar Itishree Akash Kaur Manpreet Bharati
Aryaman Phulmani Sibu Moomal Rano Anitha Arun Sravani Vamsi Saba Zaid
""").split())


def _mapping(path: Path | None) -> dict:
    if path is None:
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"could not load {path.name}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must contain a mapping")
    return data


def read_voices(path: Path | None) -> dict[str, str]:
    result = dict(VOICES)
    for lang, voice in _mapping(path).items():
        if lang not in VOICES or not isinstance(voice, str) or voice not in CATALOG:
            raise ValueError(f"unsupported language or Indic-Speak voice in voices mapping: {lang!r}")
        result[lang] = voice
    return result


def read_glossary(path: Path | None) -> dict[str, dict[str, str]]:
    result = {}
    for term, languages in _mapping(path).items():
        if not isinstance(term, str) or not term.strip() or not isinstance(languages, dict):
            raise ValueError("glossary entries must map an English term to language: rendering")
        if not all(
            lang in VOICES and isinstance(text, str) and text.strip()
            for lang, text in languages.items()
        ):
            raise ValueError(f"invalid language or rendering for glossary term {term!r}")
        result[term.strip()] = {lang: text.strip() for lang, text in languages.items()}
    return result


def settings() -> dict:
    rates = {
        key: float(value)
        for key, variable in {
            "llm_in_usd_per_mtok": "LLM_INPUT_USD_PER_MILLION",
            "llm_out_usd_per_mtok": "LLM_OUTPUT_USD_PER_MILLION",
        }.items()
        if (value := os.getenv(variable))
    }
    return {
        **rates,
        "transcribe_key": os.getenv("BODHAN_TRANSCRIBE_KEY", ""),
        "translate_key": os.getenv("BODHAN_TRANSLATE_KEY", ""),
        "speak_key": os.getenv("BODHAN_SPEAK_KEY", ""),
        "bodhan_url": os.getenv("BODHAN_BASE_URL", "https://api.bodhan.ai"),
        "llm_base_url": os.getenv("LLM_BASE_URL", ""),
        "llm_model": os.getenv("LLM_MODEL", ""),
        "llm_api_key": os.getenv("LLM_API_KEY", ""),
    }
