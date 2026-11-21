"""Fixed language / voice / style library (from the Bodhan API docs) and language-name resolution."""

from __future__ import annotations

import json
from pathlib import Path

from bodhan_core.errors import ToolError

_LIB = json.loads((Path(__file__).parent.parent / "bodhan_core" / "library.json").read_text(encoding="utf-8"))

NAMES: dict[str, str] = _LIB["language_names"]
STT_LANGUAGES: list[str] = _LIB["stt_languages"]
TRANSLATE_LANGUAGES: list[str] = _LIB["translate_languages"]
TTS_LANGUAGES: list[str] = _LIB["tts_languages"]
VOICES: dict[str, dict[str, list[str]]] = _LIB["voices"]
STYLES: list[str] = _LIB["styles"]

_ALIASES = {
    "odia": "or", "oriya": "or", "kashmiri": "ks", "manipuri": "mni", "meitei": "mni", "sindhi": "sd",
    "bangla": "bn", "panjabi": "pa", "punjabi": "pa", "bodo": "brx", "english": "en",
}
_BY_NAME: dict[str, str] = {n.split(" (")[0].lower(): c for c, n in NAMES.items()}
_BY_NAME.update(_ALIASES)


def normalize_language(value: str) -> str | None:
    """Accept an ISO code ('hi', 'ks-Deva') or an English name ('Hindi'); None if it is not an Indic-model language."""
    v = (value or "").strip()
    if v in NAMES:
        return v
    low = v.lower()
    for code in NAMES:
        if code.lower() == low:
            return code
    return _BY_NAME.get(low)


def check_language(value: str, supported: list[str], capability: str) -> str:
    code = normalize_language(value)
    if code is None or code not in supported:
        raise ToolError(
            "unsupported_language",
            f"'{value}' is not supported for {capability}.",
            "Supported codes: " + ", ".join(supported) + ". If the user asked for an unsupported language, "
            "the task is impossible: explain that instead of producing output.",
            supported=supported,
        )
    return code
