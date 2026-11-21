import re


_CATEGORY_ALIASES = {
    "number": "number",
    "unit": "number",
    "numberunit": "number",
    "term": "term",
    "entity": "term",
    "termentity": "term",
    "wronglanguage": "language",
    "script": "language",
    "wrongscript": "language",
    "untranslated": "language",
    "language": "language",
    "format": "format",
    "markdown": "format",
    "latex": "format",
    "omission": "omission",
    "addition": "addition",
    "mistranslation": "mistranslation",
    "speechomission": "speech_omission",
    "truncation": "speech_omission",
    "speechtruncation": "speech_omission",
    "omissionspeech": "speech_omission",
    "repetition": "repetition",
    "garbled": "garbled",
}


def _normalize_alias(raw: str) -> str:
    return re.sub(r"[^a-z0-9]", "", raw.lower().strip())


def canonical_category(raw: str, modality: str) -> str | None:
    normalized = _normalize_alias(raw)
    canonical = _CATEGORY_ALIASES.get(normalized)
    if canonical is not None:
        if modality == "text" and canonical.startswith("speech_"):
            return "unmapped"
        if modality == "speech" and canonical in {"omission", "addition", "mistranslation", "number", "term", "format", "language"}:
            return "unmapped"
        return canonical
    return "unmapped"


def canonical_severity(raw: str) -> str:
    normalized = _normalize_alias(raw)
    if normalized in {"minor", "low", "cosmetic"}:
        return "minor"
    if normalized in {"critical", "high", "severe"}:
        return "critical"
    return "major"
