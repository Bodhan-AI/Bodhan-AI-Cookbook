"""Supported languages and voices per Bodhan model, plus script-based language detection.

Data comes from ``data/bodhan_models.json`` (a copy of the cookbook's scripts/bodhan_api_rules.json).
Script detection is allowed by the assignment rules: it inspects Unicode blocks only, no ML model.
"""

from __future__ import annotations

import json
import unicodedata
from collections import Counter
from functools import lru_cache
from importlib import resources

from .errors import ErrorCode, ToolError


@lru_cache(maxsize=1)
def model_data() -> dict:
    with resources.files("bodhan_core").joinpath("data/bodhan_models.json").open(encoding="utf-8") as fh:
        return json.load(fh)


def language_names() -> dict[str, str]:
    return dict(model_data()["language_names"])


def supported_languages(model: str) -> list[str]:
    return list(model_data()["models"][model].get("languages", []))


def voices(language: str | None = None, gender: str | None = None) -> list[dict]:
    table = model_data()["models"]["indic-speak"].get("voices", {})
    out = []
    for lang, by_gender in table.items():
        if language and lang != language:
            continue
        for g, names in by_gender.items():
            if gender and g != gender:
                continue
            out.extend({"voice": n, "language": lang, "gender": g} for n in names)
    return out


def normalize_language(value: str) -> str:
    """Map 'Tamil', 'ta', 'TA', 'tamil ' -> 'ta'. Raises UNSUPPORTED_LANGUAGE for unknown names."""
    v = value.strip()
    names = language_names()
    if v in names:
        return v
    lowered = v.lower()
    for code, name in names.items():
        if lowered == code.lower() or lowered == name.lower() or lowered == name.split(" (")[0].lower():
            return code
    raise ToolError(
        ErrorCode.UNSUPPORTED_LANGUAGE,
        f"Language {value!r} is not supported by any Bodhan model.",
        hint="Supported: " + ", ".join(f"{c} ({n})" for c, n in sorted(names.items())),
    )


def require_supported(model: str, language: str) -> str:
    code = normalize_language(language)
    if code not in supported_languages(model):
        names = language_names()
        raise ToolError(
            ErrorCode.UNSUPPORTED_LANGUAGE,
            f"{model} does not support {names.get(code, code)} ({code}).",
            hint=f"{model} supports: " + ", ".join(supported_languages(model)),
        )
    return code


# Unicode script name (first word of the character name) -> candidate language codes.
SCRIPT_LANGS: dict[str, list[str]] = {
    "DEVANAGARI": ["hi", "mr", "ne", "sa", "mai", "kok", "doi", "brx", "bho"],
    "BENGALI": ["bn", "as", "mni-Beng"],
    "GURMUKHI": ["pa"],
    "GUJARATI": ["gu"],
    "ORIYA": ["or"],
    "TAMIL": ["ta"],
    "TELUGU": ["te"],
    "KANNADA": ["kn"],
    "MALAYALAM": ["ml"],
    "ARABIC": ["ur", "ks", "sd-Arab"],
    "OL": ["sat"],  # OL CHIKI
    "MEETEI": ["mni"],  # MEETEI MAYEK
    "LATIN": ["en"],
}


def detect_script(text: str) -> dict:
    """Return the dominant script and candidate languages. Ambiguous scripts list several candidates."""
    counts: Counter[str] = Counter()
    for ch in text:
        if not ch.isalpha():
            continue
        try:
            script = unicodedata.name(ch).split(" ")[0]
        except ValueError:
            continue
        counts[script] += 1
    total = sum(counts.values())
    if not total:
        return {"script": None, "candidates": [], "share": 0.0, "ambiguous": True}
    script, n = counts.most_common(1)[0]
    candidates = SCRIPT_LANGS.get(script, [])
    return {
        "script": script.title(),
        "candidates": candidates,
        "share": round(n / total, 3),
        "ambiguous": len(candidates) != 1,
    }
