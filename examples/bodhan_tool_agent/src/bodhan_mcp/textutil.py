"""Pure text helpers: script-based language ID, sentence splitting/chunking, normalisation, CER, number checks."""
from __future__ import annotations

import re
import unicodedata

# Unicode block -> candidate languages. Script identifies the script, not always the language.
_SCRIPTS: list[tuple[str, int, int, list[str]]] = [
    ("Devanagari", 0x0900, 0x097F, ["hi", "mr", "ne", "sa", "kok", "mai", "doi", "brx", "sd", "ks"]),
    ("Bengali", 0x0980, 0x09FF, ["bn", "as", "mni"]),
    ("Gurmukhi", 0x0A00, 0x0A7F, ["pa"]),
    ("Gujarati", 0x0A80, 0x0AFF, ["gu"]),
    ("Odia", 0x0B00, 0x0B7F, ["or"]),
    ("Tamil", 0x0B80, 0x0BFF, ["ta"]),
    ("Telugu", 0x0C00, 0x0C7F, ["te"]),
    ("Kannada", 0x0C80, 0x0CFF, ["kn"]),
    ("Malayalam", 0x0D00, 0x0D7F, ["ml"]),
    ("Ol Chiki", 0x1C50, 0x1C7F, ["sat"]),
    ("Arabic", 0x0600, 0x06FF, ["ur", "ks", "sd"]),
]
SCRIPT_TO_LANG_DEFAULT = {"Devanagari": "hi", "Bengali": "bn", "Gurmukhi": "pa", "Gujarati": "gu", "Odia": "or",
                          "Tamil": "ta", "Telugu": "te", "Kannada": "kn", "Malayalam": "ml", "Ol Chiki": "sat",
                          "Arabic": "ur", "Latin": "en"}

# language -> expected script (used to check that a translation came back in the right script)
LANG_SCRIPT = {"hi": "Devanagari", "mr": "Devanagari", "ne": "Devanagari", "sa": "Devanagari", "kok": "Devanagari",
               "mai": "Devanagari", "doi": "Devanagari", "brx": "Devanagari", "sd": "Devanagari",
               "ks-Deva": "Devanagari", "bn": "Bengali", "as": "Bengali", "mni-Beng": "Bengali", "pa": "Gurmukhi",
               "gu": "Gujarati", "or": "Odia", "ta": "Tamil", "te": "Telugu", "kn": "Kannada", "ml": "Malayalam",
               "sat": "Ol Chiki", "ur": "Arabic", "ks": "Arabic", "sd-Arab": "Arabic", "en": "Latin",
               "mni": "Meetei"}


def script_histogram(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for ch in text:
        cp = ord(ch)
        name = None
        for sname, lo, hi, _ in _SCRIPTS:
            if lo <= cp <= hi:
                name = sname
                break
        if name is None and ch.isalpha() and cp < 0x250:
            name = "Latin"
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts


def detect_script(text: str) -> tuple[str, float]:
    """Dominant script and its share of all letters. ('Unknown', 0) when there are no letters."""
    h = script_histogram(text)
    total = sum(h.values())
    if not total:
        return "Unknown", 0.0
    name, n = max(h.items(), key=lambda kv: kv[1])
    return name, n / total


def has_indic_script(text: str) -> bool:
    return any(k != "Latin" for k in script_histogram(text))


def guess_language(text: str) -> dict:
    """Script-based language identification (allowed by the brief). Several languages share Devanagari/Bengali, so
    `candidates` lists them and `confidence` is lower for those; callers should treat Devanagari as 'hi?' unless told."""
    script, share = detect_script(text)
    if script == "Unknown":
        return {"language": None, "script": script, "confidence": 0.0, "candidates": []}
    cands = next((c for n, _, _, c in _SCRIPTS if n == script), ["en"] if script == "Latin" else [])
    lang = SCRIPT_TO_LANG_DEFAULT.get(script)
    conf = share * (1.0 if len(cands) <= 1 else 0.6)
    mixed = share < 0.8
    return {"language": lang, "script": script, "confidence": round(conf, 2), "candidates": cands,
            "mixed_scripts": mixed}


# ---- splitting ---------------------------------------------------------------------------------------------------

_SENT_END = re.compile(r"(?<=[.!?।॥?!])\s+")


def split_sentences(text: str) -> list[str]:
    out: list[str] = []
    for para in re.split(r"\n\s*\n", text.strip()):
        para = " ".join(para.split())
        if not para:
            continue
        out.extend(s.strip() for s in _SENT_END.split(para) if s.strip())
    return out


def _hard_split(s: str, max_chars: int) -> list[str]:
    """Last resort for a sentence longer than max_chars: split at commas/semicolons, then at spaces."""
    parts = re.split(r"(?<=[,;:،，、])\s+", s)
    out: list[str] = []
    cur = ""
    for p in parts:
        while len(p) > max_chars:
            cut = p.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            if cur:
                out.append(cur)
                cur = ""
            out.append(p[:cut].strip())
            p = p[cut:].strip()
        if cur and len(cur) + 1 + len(p) > max_chars:
            out.append(cur)
            cur = p
        else:
            cur = f"{cur} {p}".strip()
    if cur:
        out.append(cur)
    return out


def chunk_text(text: str, max_chars: int) -> list[str]:
    """Pack whole sentences into chunks of <= max_chars. Never splits mid-sentence unless one sentence alone is longer
    than max_chars. Lossless: ' '.join(chunks) preserves every word."""
    chunks: list[str] = []
    cur = ""
    for s in split_sentences(text):
        pieces = [s] if len(s) <= max_chars else _hard_split(s, max_chars)
        for piece in pieces:
            if cur and len(cur) + 1 + len(piece) > max_chars:
                chunks.append(cur)
                cur = piece
            else:
                cur = f"{cur} {piece}".strip()
    if cur:
        chunks.append(cur)
    return chunks


def chunk_paragraphs(text: str, max_chars: int) -> list[str]:
    """Like chunk_text but keeps line structure (Markdown, tables, lists) intact by splitting only at blank lines,
    falling back to sentence packing for a single oversized paragraph. Tables are never split."""
    blocks = [b for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]
    chunks: list[str] = []
    cur = ""
    for b in blocks:
        if len(b) > max_chars and not _is_structured(b):
            for piece in chunk_text(b, max_chars):
                if cur and len(cur) + 2 + len(piece) > max_chars:
                    chunks.append(cur)
                    cur = piece
                else:
                    cur = f"{cur}\n\n{piece}" if cur else piece
            continue
        if cur and len(cur) + 2 + len(b) > max_chars:
            chunks.append(cur)
            cur = b
        else:
            cur = f"{cur}\n\n{b}" if cur else b
    if cur:
        chunks.append(cur)
    return chunks


def _is_structured(block: str) -> bool:
    return block.lstrip().startswith(("|", "<table", "$$", "```")) or "<table" in block


# ---- normalisation / metrics ------------------------------------------------------------------------------------

_DIGIT_BASES = [0x0966, 0x09E6, 0x0A66, 0x0AE6, 0x0B66, 0x0BE6, 0x0C66, 0x0CE6, 0x0D66, 0x06F0, 0x0660, 0x1C40]


def to_ascii_digits(text: str) -> str:
    out = []
    for ch in text:
        cp = ord(ch)
        for base in _DIGIT_BASES:
            if base <= cp <= base + 9:
                out.append(str(cp - base))
                break
        else:
            out.append(ch)
    return "".join(out)


def normalize(text: str) -> str:
    """For CER/coverage comparisons: NFC, ascii digits, drop punctuation/zero-width chars, lowercase, squash spaces."""
    text = unicodedata.normalize("NFC", to_ascii_digits(text))
    text = re.sub(r"[​‌‍﻿]", "", text)
    text = "".join(ch if (ch.isalnum() or ch.isspace() or unicodedata.category(ch).startswith("M")) else " "
                   for ch in text)
    return " ".join(text.lower().split())


def edit_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(reference: str, hypothesis: str) -> float:
    ref, hyp = normalize(reference), normalize(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    return edit_distance(ref, hyp) / len(ref)


_NUM = re.compile(r"\d+(?:[.,]\d+)*")


def extract_numbers(text: str) -> list[str]:
    return [n.replace(",", "") for n in _NUM.findall(to_ascii_digits(text))]


_LATEX = re.compile(r"\$\$.+?\$\$|\$[^$\n]+\$", re.S)


def extract_latex(text: str) -> list[str]:
    return [m.strip() for m in _LATEX.findall(text)]


def structure_counts(text: str) -> dict[str, int]:
    lines = text.splitlines()
    return {
        "headings": sum(1 for l in lines if l.lstrip().startswith("#")),
        "table_rows": sum(1 for l in lines if l.strip().startswith("|")),
        "html_tables": text.count("<table"),
        "latex": len(extract_latex(text)),
        "list_items": sum(1 for l in lines if re.match(r"\s*([-*]|\d+[.)])\s", l)),
    }
