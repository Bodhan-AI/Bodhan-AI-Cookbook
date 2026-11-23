"""Language codes and script-based language identification (no models).

Script tells us the writing system, not always the language: Devanagari covers
Hindi, Marathi, Nepali, Maithili, Sanskrit... so `detect_script_language`
returns the script plus every Bodhan language that uses it, and a best guess.
"""

from __future__ import annotations

import re
import unicodedata

from . import config
from .errors import UNSUPPORTED_LANGUAGE, ToolError

# Unicode block ranges -> (script name, candidate language codes, default guess)
SCRIPTS: list[tuple[str, int, int, list[str], str]] = [
    ("Devanagari", 0x0900, 0x097F, ["hi", "mr", "ne", "mai", "sa", "kok", "doi", "brx", "bho", "ks-Deva", "sd"], "hi"),
    ("Bengali", 0x0980, 0x09FF, ["bn", "as", "mni-Beng"], "bn"),
    ("Gurmukhi", 0x0A00, 0x0A7F, ["pa"], "pa"),
    ("Gujarati", 0x0A80, 0x0AFF, ["gu"], "gu"),
    ("Odia", 0x0B00, 0x0B7F, ["or"], "or"),
    ("Tamil", 0x0B80, 0x0BFF, ["ta"], "ta"),
    ("Telugu", 0x0C00, 0x0C7F, ["te"], "te"),
    ("Kannada", 0x0C80, 0x0CFF, ["kn"], "kn"),
    ("Malayalam", 0x0D00, 0x0D7F, ["ml"], "ml"),
    ("Meetei Mayek", 0xABC0, 0xABFF, ["mni"], "mni"),
    ("Ol Chiki", 0x1C50, 0x1C7F, ["sat"], "sat"),
    ("Arabic", 0x0600, 0x06FF, ["ur", "ks", "sd-Arab"], "ur"),
]
INDIC_RANGES = [(entry[1], entry[2]) for entry in SCRIPTS]

# Common names a planner might use instead of codes.
NAME_TO_CODE = {v.lower().split(" (")[0]: k for k, v in config.rules()["language_names"].items()}
NAME_TO_CODE.update({"oriya": "or", "bangla": "bn", "manipuri": "mni", "kashmiri": "ks", "sindhi": "sd"})


def script_profile(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for ch in text:
        cp = ord(ch)
        if ch.isascii():
            if ch.isalpha():
                counts["Latin"] = counts.get("Latin", 0) + 1
            continue
        for name, lo, hi, _, _ in SCRIPTS:
            if lo <= cp <= hi:
                counts[name] = counts.get(name, 0) + 1
                break
    return counts


def indic_fraction(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    indic = sum(1 for c in letters if any(lo <= ord(c) <= hi for lo, hi in INDIC_RANGES))
    return indic / len(letters)


def detect_script_language(text: str) -> dict:
    prof = script_profile(text)
    if not prof:
        return {"script": None, "language": None, "candidates": [], "confidence": 0.0}
    total = sum(prof.values())
    script, n = max(prof.items(), key=lambda kv: kv[1])
    if script == "Latin":
        return {"script": "Latin", "language": "en", "candidates": ["en"], "confidence": round(n / total, 3),
                "note": "Latin script: English, or romanised Indic text"}
    entry = next(s for s in SCRIPTS if s[0] == script)
    cands = entry[3]
    guess = entry[4]
    # Cheap disambiguation hints inside Devanagari / Bengali scripts.
    if script == "Devanagari" and re.search(r"(आहे|आहेत|झाले|च्या|ला\b)", text):
        guess = "mr"
    elif script == "Devanagari" and re.search(r"(छ\b|छन्|हुन्छ|गर्न)", text):
        guess = "ne"
    elif script == "Bengali" and re.search(r"[ৰৱ]", text):
        guess = "as"
    return {"script": script, "language": guess, "candidates": cands, "confidence": round(n / total, 3)}


def normalize_code(lang: str | None, model: str) -> str | None:
    """Accept 'hi', 'Hindi', 'HINDI'; reject anything the model does not support."""
    if lang is None or str(lang).strip() == "":
        return None
    raw = str(lang).strip()
    code = raw if raw in config.languages(model) else NAME_TO_CODE.get(raw.lower(), raw.lower())
    if code.endswith("-in"):
        code = code[:-3]
    supported = config.languages(model)
    if code not in supported:
        raise ToolError(
            UNSUPPORTED_LANGUAGE,
            f"Language {raw!r} is not supported by {model}.",
            hint="Do not substitute another model or invent output. Tell the user it is unsupported. "
                 f"Supported codes: {', '.join(supported)}",
            language=raw, model=model,
        )
    return code


_DIGITS = str.maketrans({chr(base + i): str(i) for base in (0x0966, 0x09E6, 0x0A66, 0x0AE6, 0x0B66, 0x0BE6,
                                                            0x0C66, 0x0CE6, 0x0D66, 0x0660, 0x06F0) for i in range(10)})


def ascii_digits(text: str) -> str:
    """'२५' -> '25' for every Indic digit set."""
    return text.translate(_DIGITS)


def normalize_for_compare(text: str) -> str:
    """Normalisation used before CER / coverage comparisons."""
    text = ascii_digits(unicodedata.normalize("NFC", text)).lower()
    text = re.sub(r"[​-‍﻿]", "", text)
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def cer(reference: str, hypothesis: str) -> float:
    """Character error rate (Levenshtein / len(ref)) on normalised text."""
    r, h = normalize_for_compare(reference).replace(" ", ""), normalize_for_compare(hypothesis).replace(" ", "")
    if not r:
        return 0.0 if not h else 1.0
    prev = list(range(len(h) + 1))
    for i, rc in enumerate(r, 1):
        cur = [i] + [0] * len(h)
        for j, hc in enumerate(h, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rc != hc))
        prev = cur
    return round(prev[-1] / len(r), 4)


NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")


def numbers_in(text: str) -> list[str]:
    """Numbers as ASCII strings, with thousands separators removed ('1,200' -> '1200')."""
    return [n.replace(",", "") for n in NUM_RE.findall(ascii_digits(text))]
