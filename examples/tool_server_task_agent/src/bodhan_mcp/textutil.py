"""Pure text helpers: script-based language ID, sentence/Markdown chunking, SRT, digit normalisation, CER."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# (name, start, end) Unicode blocks -> script name
_RANGES: list[tuple[str, int, int]] = [
    ("Devanagari", 0x0900, 0x097F), ("Devanagari", 0xA8E0, 0xA8FF),
    ("Bengali", 0x0980, 0x09FF), ("Gurmukhi", 0x0A00, 0x0A7F), ("Gujarati", 0x0A80, 0x0AFF),
    ("Odia", 0x0B00, 0x0B7F), ("Tamil", 0x0B80, 0x0BFF), ("Telugu", 0x0C00, 0x0C7F),
    ("Kannada", 0x0C80, 0x0CFF), ("Malayalam", 0x0D00, 0x0D7F), ("Arabic", 0x0600, 0x06FF),
    ("Arabic", 0x0750, 0x077F), ("Arabic", 0xFB50, 0xFDFF), ("Arabic", 0xFE70, 0xFEFF),
    ("OlChiki", 0x1C50, 0x1C7F), ("MeeteiMayek", 0xABC0, 0xABFF), ("MeeteiMayek", 0xAAE0, 0xAAFF),
]

# script -> candidate language codes (most likely first). Script alone cannot separate every pair.
SCRIPT_LANGS: dict[str, list[str]] = {
    "Devanagari": ["hi", "mr", "ne", "sa", "kok", "mai", "doi", "brx", "sd", "ks-Deva"],
    "Bengali": ["bn", "as", "mni-Beng"], "Gurmukhi": ["pa"], "Gujarati": ["gu"], "Odia": ["or"], "Tamil": ["ta"],
    "Telugu": ["te"], "Kannada": ["kn"], "Malayalam": ["ml"], "Arabic": ["ur", "ks", "sd-Arab"],
    "OlChiki": ["sat"], "MeeteiMayek": ["mni"], "Latin": ["en"],
}
# Tiny marker-word lists to pick between look-alike Devanagari / Bengali-script languages.
_MARKERS: dict[str, tuple[str, ...]] = {
    "mr": ("आहे", "आहेत", "आणि", "नाही", "काय", "होते", "मी ", "तुम्ही", "आपण", "ला "),
    "hi": ("है", "हैं", "और", "नहीं", "का ", "की ", "के ", "में", "को ", "था", "रहेगा", "कृपया"),
    "ne": ("छ", "हुन्छ", "छन्", "गर्नु", "तपाईं", "हो।"),
    "as": ("আৰু", "নহয়", "কৰা", "মই", "আছে।"),
    "bn": ("এবং", "আমি", "না", "করে", "হয়", "আছে", "বন্ধ", "থাকবে"),
}

_INDIC_RE = re.compile("[\u0900-\u0dff\u0600-\u06ff\u0750-\u077f\ufb50-\ufdff\ufe70-\ufeff\u1c50-\u1c7f\uabc0-\uabff\uaae0-\uaaff]")


def script_of(ch: str) -> str | None:
    cp = ord(ch)
    for name, lo, hi in _RANGES:
        if lo <= cp <= hi:
            return name
    if ch.isalpha() and cp < 0x250:
        return "Latin"
    return None


def script_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for ch in text:
        s = script_of(ch)
        if s:
            counts[s] = counts.get(s, 0) + 1
    return counts


def has_indic_script(text: str) -> bool:
    return bool(_INDIC_RE.search(text))


def detect_language(text: str) -> dict:
    """Language ID by Unicode script only (no model). Returns script, ranked candidates and confidence."""
    counts = script_counts(text)
    total = sum(counts.values())
    if total == 0:
        return {"script": None, "language": None, "candidates": [], "confidence": "none", "script_counts": {}, "marker_score": None}
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    script = ranked[0][0]
    cands = list(SCRIPT_LANGS.get(script, []))
    share = counts[script] / total
    lang = cands[0] if cands else None
    confidence = "high" if len(cands) == 1 else "medium"
    marker_score: int | None = None
    if len(cands) > 1:
        scored = {c: sum(text.count(m) for m in _MARKERS.get(c, ())) for c in cands}
        best = max(scored, key=lambda c: scored[c])
        marker_score = scored[best]
        if scored[best] > 0:
            lang = best
            cands = [best] + [c for c in cands if c != best]
            confidence = "medium"
        else:
            confidence = "low"
    if script == "Latin":
        confidence = "medium"  # could also be romanised Indic text; script cannot tell
    if share < 0.6 and len(ranked) > 1:
        confidence = "low"
    return {"script": script, "language": lang, "candidates": cands, "confidence": confidence,
            "script_counts": dict(ranked), "marker_score": marker_score}


def already_in_language(text: str, target: str) -> bool:
    """True when the text is clearly already written in `target` (script-only evidence; never for Latin script, which may be romanised Indic)."""
    d = detect_language(text[:3000])
    if d["script"] in (None, "Latin") or d["language"] != target:
        return False
    if len(d["candidates"]) == 1:  # distinctive script (Tamil, Telugu, ...): the script alone settles it
        return d["script_counts"].get(d["script"], 0) / max(sum(d["script_counts"].values()), 1) >= 0.8
    return (d["marker_score"] or 0) >= 2  # shared script: need several marker words for the target language


def expected_script_for(language: str) -> str | None:
    for script, langs in SCRIPT_LANGS.items():
        if language in langs:
            return script
    return None


# ---- digits ----------------------------------------------------------------------------------
def normalize_digits(text: str) -> str:
    out = []
    for ch in text:
        if ch.isdigit() and not ch.isascii():
            try:
                out.append(str(unicodedata.digit(ch)))
                continue
            except ValueError:
                pass
        out.append(ch)
    return "".join(out)


def numbers_in(text: str) -> list[str]:
    return re.findall(r"\d+(?:[.,]\d+)*", normalize_digits(text))


# ---- sentences -------------------------------------------------------------------------------
_SENT_SPLIT = re.compile(r"(?<=[।॥.!?؟۔])[\"'”’)\]]*\s+|\n+")


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text.strip()) if s and s.strip()]


def _hard_split(sentence: str, max_chars: int) -> list[str]:
    """Split an over-long sentence at commas, then spaces, then raw characters."""
    if len(sentence) <= max_chars:
        return [sentence]
    parts: list[str] = []
    cur = ""
    for piece in re.split(r"(?<=[,;:،、])\s+|\s+", sentence):
        if len(piece) > max_chars:  # no spaces at all (e.g. unspaced script): cut
            if cur:
                parts.append(cur)
                cur = ""
            parts += [piece[i:i + max_chars] for i in range(0, len(piece), max_chars)]
            continue
        if cur and len(cur) + 1 + len(piece) > max_chars:
            parts.append(cur)
            cur = piece
        else:
            cur = f"{cur} {piece}".strip()
    if cur:
        parts.append(cur)
    return parts


def pack_sentences(text: str, max_chars: int) -> list[str]:
    """Split text into chunks of whole sentences, each <= max_chars (long sentences are split at commas/spaces)."""
    units: list[str] = []
    for s in split_sentences(text):
        units += _hard_split(s, max_chars)
    chunks: list[str] = []
    cur = ""
    for u in units:
        if cur and len(cur) + 1 + len(u) > max_chars:
            chunks.append(cur)
            cur = u
        else:
            cur = f"{cur} {u}".strip()
    if cur:
        chunks.append(cur)
    return chunks


def speakable(text: str) -> str:
    """Strip Markdown syntax so it is not read aloud."""
    t = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    t = re.sub(r"```.*?```", " ", t, flags=re.S)
    t = re.sub(r"^\s*\|?[\s:|-]{3,}\|?\s*$", " ", t, flags=re.M)  # table separator rows
    t = t.replace("|", ". ")
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.M)
    t = re.sub(r"^\s*[-*+]\s+", "", t, flags=re.M)
    t = re.sub(r"[*_`~]{1,3}", "", t)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"[ \t]+", " ", t)
    return t.strip()


# ---- markdown chunking for translation -------------------------------------------------------
@dataclass
class Chunk:
    text: str
    sep: str = "\n\n"           # separator inserted BEFORE this chunk when joining
    table_continuation: bool = False  # continuation of a split table: strip its repeated header on join


_TABLE_LINE = re.compile(r"^\s*\|.*\|\s*$")


def _blocks(text: str) -> list[str]:
    return [b for b in re.split(r"\n\s*\n", text.replace("\r\n", "\n")) if b.strip()]


def _is_table(block: str) -> bool:
    lines = [ln for ln in block.splitlines() if ln.strip()]
    return len(lines) >= 2 and all(_TABLE_LINE.match(ln) for ln in lines)


def chunk_markdown(text: str, max_chars: int) -> list[Chunk]:
    """Split Markdown at paragraph boundaries into chunks <= max_chars.

    Tables stay whole when they fit; an over-large table is split by rows with the header repeated
    (the repeat is stripped again on join). Oversized paragraphs are split at sentence boundaries.
    """
    units: list[Chunk] = []
    for block in _blocks(text):
        if len(block) <= max_chars:
            units.append(Chunk(block))
        elif _is_table(block):
            lines = [ln for ln in block.splitlines() if ln.strip()]
            header, rows = lines[:2], lines[2:]
            cur: list[str] = []
            first = True
            for row in rows:
                if cur and len("\n".join(header + cur + [row])) > max_chars:
                    units.append(Chunk("\n".join(header + cur), sep="\n\n" if first else "\n", table_continuation=not first))
                    first = False
                    cur = []
                cur.append(row)
            if cur:
                units.append(Chunk("\n".join(header + cur), sep="\n\n" if first else "\n", table_continuation=not first))
        else:
            for i, piece in enumerate(pack_sentences(block, max_chars)):
                units.append(Chunk(piece, sep="\n\n" if i == 0 else " "))
    # pack small neighbours together (only plain, non-table, paragraph-boundary units)
    packed: list[Chunk] = []
    for u in units:
        mergeable = (packed and u.sep == "\n\n" and not u.table_continuation and not _is_table(u.text)
                     and not _is_table(packed[-1].text) and len(packed[-1].text) + 2 + len(u.text) <= max_chars)
        if mergeable:
            packed[-1] = Chunk(packed[-1].text + "\n\n" + u.text, packed[-1].sep, packed[-1].table_continuation)
        else:
            packed.append(u)
    return packed


def join_chunks(chunks: list[Chunk], translated: list[str]) -> str:
    out = ""
    for i, (chunk, tr) in enumerate(zip(chunks, translated, strict=True)):
        if chunk.table_continuation:
            tr = "\n".join(tr.splitlines()[2:])  # drop repeated header + separator rows
        out += (chunk.sep if i else "") + tr
    return out.strip() + "\n"


# ---- LaTeX protection ------------------------------------------------------------------------
_MATH = re.compile(r"\$\$.+?\$\$|\$[^$\n]+?\$|\\\(.+?\\\)|\\\[.+?\\\]", re.S)


def protect_math(text: str) -> tuple[str, list[str]]:
    spans: list[str] = []

    def _sub(m: re.Match) -> str:
        spans.append(m.group(0))
        return f"[[M{len(spans) - 1}]]"

    return _MATH.sub(_sub, text), spans


def restore_math(text: str, spans: list[str]) -> tuple[str, int]:
    """Put LaTeX back. Returns (text, number of placeholders that were lost by the translator)."""
    lost = 0
    for i, span in enumerate(spans):
        token = re.compile(r"\[\[\s*M\s*%d\s*\]\]" % i)
        if token.search(text):
            text = token.sub(lambda _m, s=span: s, text)
        else:
            lost += 1
    return text, lost


# ---- SRT -------------------------------------------------------------------------------------
@dataclass
class Cue:
    index: int
    start: float
    end: float
    text: str


def fmt_ts(sec: float) -> str:
    ms = int(round(max(sec, 0) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _parse_ts(s: str) -> float:
    h, m, rest = s.strip().replace(".", ",").split(":")
    sec, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000


def build_srt(cues: list[Cue]) -> str:
    return "\n".join(f"{i}\n{fmt_ts(c.start)} --> {fmt_ts(c.end)}\n{c.text}\n" for i, c in enumerate(cues, 1))


_SRT_TIME = re.compile(r"(\d+:\d\d:\d\d[,.]\d{1,3})\s*-->\s*(\d+:\d\d:\d\d[,.]\d{1,3})")


def parse_srt(text: str) -> list[Cue]:
    cues: list[Cue] = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()):
        lines = block.strip().splitlines()
        for i, ln in enumerate(lines):
            m = _SRT_TIME.search(ln)
            if m:
                cues.append(Cue(len(cues) + 1, _parse_ts(m.group(1)), _parse_ts(m.group(2)), "\n".join(lines[i + 1:]).strip()))
                break
    return cues


def split_cue_text(text: str, max_line: int = 42) -> str:
    """Wrap subtitle text onto lines of <= ~42 chars at spaces."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > max_line:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return "\n".join(lines)


# ---- error rate ------------------------------------------------------------------------------
def normalize_for_cer(text: str) -> str:
    t = unicodedata.normalize("NFC", text).lower()
    t = normalize_digits(t)
    t = re.sub(r"[\u200b-\u200d\ufeff]", "", t)
    t = "".join(ch for ch in t if not unicodedata.category(ch).startswith(("P", "S", "Z", "C")))
    return t


def cer(reference: str, hypothesis: str, *, ignore_digits: bool = False) -> float:
    """Character error rate = edit distance / reference length.

    ignore_digits=True is for references containing numerals: ASR writes "90" as the words for ninety, so digits are
    dropped from the reference and only deletions/substitutions count (spoken number words appear as harmless insertions).
    """
    from rapidfuzz.distance import Levenshtein

    ref, hyp = normalize_for_cer(reference), normalize_for_cer(hypothesis)
    if ignore_digits:
        ref = "".join(ch for ch in ref if not ch.isdigit())
        if not ref:
            return 0.0
        ops = Levenshtein.editops(ref, hyp)
        return sum(1 for op in ops if op.tag in ("delete", "replace")) / len(ref)
    if not ref:
        return 0.0 if not hyp else 1.0
    return Levenshtein.distance(ref, hyp) / len(ref)


def repeated_ngrams(text: str, n: int = 3, min_repeats: int = 3) -> list[str]:
    """Phrases of n words that occur back-to-back at least min_repeats times (TTS looping signature)."""
    cleaned = "".join(" " if unicodedata.category(ch)[0] in "PSC" else ch for ch in text.lower())  # keep combining marks (Indic matras)
    words = cleaned.split()
    found: list[str] = []
    for i in range(len(words) - n * min_repeats + 1):
        gram = words[i:i + n]
        if all(words[i + k * n:i + (k + 1) * n] == gram for k in range(min_repeats)):
            phrase = " ".join(gram)
            if phrase not in found:
                found.append(phrase)
    return found
