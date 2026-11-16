"""Plain-code text handling: script detection, language ID by character set, chunking, similarity.

Nothing here calls a model. Language identification uses only the Unicode script of the text plus a
few script-specific letters and very common function words, which the assignment allows
("language-identification by script or character set").
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher

# ---------------------------------------------------------------------------
# scripts
# ---------------------------------------------------------------------------

_SCRIPT_RANGES: list[tuple[int, int, str]] = [
    (0x0900, 0x097F, "Devanagari"), (0xA8E0, 0xA8FF, "Devanagari"),
    (0x0980, 0x09FF, "Bengali"), (0x0A00, 0x0A7F, "Gurmukhi"), (0x0A80, 0x0AFF, "Gujarati"),
    (0x0B00, 0x0B7F, "Oriya"), (0x0B80, 0x0BFF, "Tamil"), (0x0C00, 0x0C7F, "Telugu"),
    (0x0C80, 0x0CFF, "Kannada"), (0x0D00, 0x0D7F, "Malayalam"), (0x0D80, 0x0DFF, "Sinhala"),
    (0x0600, 0x06FF, "Arabic"), (0x0750, 0x077F, "Arabic"), (0x08A0, 0x08FF, "Arabic"),
    (0xFB50, 0xFDFF, "Arabic"), (0xFE70, 0xFEFF, "Arabic"),
    (0x1C50, 0x1C7F, "Ol Chiki"), (0xABC0, 0xABFF, "Meetei Mayek"), (0xAAE0, 0xAAFF, "Meetei Mayek"),
    (0x0E00, 0x0E7F, "Thai"), (0x0400, 0x04FF, "Cyrillic"), (0x0370, 0x03FF, "Greek"),
    (0x4E00, 0x9FFF, "Han"), (0x3040, 0x30FF, "Kana"), (0xAC00, 0xD7AF, "Hangul"),
]
INDIC_SCRIPTS = frozenset({
    "Devanagari", "Bengali", "Gurmukhi", "Gujarati", "Oriya", "Tamil", "Telugu", "Kannada", "Malayalam",
    "Arabic", "Ol Chiki", "Meetei Mayek",
})


def char_script(ch: str) -> str | None:
    cp = ord(ch)
    if ch.isascii():
        return "Latin" if ch.isalpha() else None
    for lo, hi, name in _SCRIPT_RANGES:
        if lo <= cp <= hi:
            # Count letters and combining marks, not digits or punctuation inside the block.
            cat = unicodedata.category(ch)
            if cat[0] in ("L", "M"):
                return name
            return None
    if unicodedata.category(ch)[0] == "L" and "LATIN" in unicodedata.name(ch, ""):
        return "Latin"
    return "Other" if unicodedata.category(ch)[0] == "L" else None


def script_profile(text: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for ch in text:
        s = char_script(ch)
        if s:
            counts[s] += 1
    return dict(counts.most_common())


def dominant_script(text: str) -> tuple[str | None, float]:
    prof = script_profile(text)
    total = sum(prof.values())
    if not total:
        return None, 0.0
    name, n = next(iter(prof.items()))
    return name, n / total


def indic_letter_count(text: str) -> int:
    return sum(n for s, n in script_profile(text).items() if s in INDIC_SCRIPTS)


def has_indic(text: str) -> bool:
    return any(char_script(ch) in INDIC_SCRIPTS for ch in text if not ch.isascii())


# ---------------------------------------------------------------------------
# language identification by script / character set
# ---------------------------------------------------------------------------

_UNIQUE_SCRIPT_LANG = {
    "Tamil": "ta", "Telugu": "te", "Kannada": "kn", "Malayalam": "ml", "Gujarati": "gu",
    "Gurmukhi": "pa", "Oriya": "or", "Ol Chiki": "sat", "Meetei Mayek": "mni", "Latin": "en",
}

# Very common function words; enough to separate the main Devanagari-script languages.
_DEVANAGARI_MARKERS: dict[str, set[str]] = {
    "hi": {"है", "हैं", "का", "की", "के", "और", "में", "से", "को", "यह", "था", "थी", "थे", "नहीं", "पर", "भी", "एक", "लिए", "हम", "आप"},
    "mr": {"आहे", "आहेत", "आणि", "नाही", "होते", "होता", "आम्ही", "तुम्ही", "मध्ये", "याचा", "त्या", "हे", "ते", "व", "करा", "आपण", "केले", "म्हणून"},
    "ne": {"छ", "छन्", "हो", "र", "पनि", "गर्न", "भएको", "हुन्छ", "थियो", "मा", "लाई", "को", "गरेको", "यो", "तपाईं", "हुने"},
    "sa": {"अस्ति", "च", "तत्", "सः", "एव", "इति", "अपि", "भवति", "तस्य", "वा", "न", "यत्", "किम्"},
    "mai": {"अछि", "छथि", "एहि", "ओ", "सँ", "मे", "कें", "छल", "हमर", "अहाँ"},
    "kok": {"आसा", "आनी", "हांव", "तुमी", "जाल्यार", "म्हळ्ळें", "कितें", "आसात"},
}
_ASSAMESE_LETTERS = set("ৰৱ")
_SINDHI_ARABIC = set("ڄڃڇٻڀٺٽڏڊڌڍڙڪڳڱ")
_KASHMIRI_ARABIC = set("ٲٳۄۆێ")


@dataclass
class LanguageGuess:
    language: str | None
    confidence: float
    script: str | None
    candidates: list[str]
    method: str = "script"

    def as_dict(self) -> dict:
        return {
            "language": self.language,
            "confidence": round(self.confidence, 2),
            "script": self.script,
            "candidates": self.candidates,
            "method": self.method,
        }




def _latin_language(text: str, share: float) -> LanguageGuess:
    tokens = re.findall(r"[a-zà-öø-ÿœ']+", text.lower())
    scores = {lang: sum(1 for t in tokens if t in words) for lang, words in _LATIN_MARKERS.items()}
    best = max(scores, key=lambda k: scores[k])
    total = sum(scores.values())
    if total == 0 or best == "en" or scores[best] <= scores["en"]:
        return LanguageGuess("en", share * 0.9, "Latin", ["en"], "script+function-words")
    conf = share * scores[best] / total
    return LanguageGuess(best, conf, "Latin", sorted(scores, key=lambda k: -scores[k])[:3], "script+function-words")


_LATIN_MARKERS: dict[str, set[str]] = {
    "en": {"the", "and", "is", "of", "to", "in", "that", "it", "was", "for", "with", "this", "are", "you", "be", "on", "have", "not"},
    "fr": {"le", "la", "les", "et", "est", "des", "une", "un", "du", "dans", "que", "qui", "pour", "pas", "sur", "avec", "nous", "vous", "je", "c'est"},
    "es": {"el", "los", "las", "y", "es", "del", "una", "que", "por", "para", "con", "no", "en", "se", "su", "como", "pero", "muy"},
    "de": {"der", "die", "das", "und", "ist", "nicht", "ein", "eine", "mit", "für", "auf", "ich", "sie", "wir", "zu", "den", "dem"},
    "pt": {"o", "os", "as", "e", "é", "não", "uma", "um", "que", "do", "da", "em", "para", "com", "você", "isso"},
    "it": {"il", "lo", "gli", "e", "è", "non", "una", "che", "di", "per", "con", "sono", "della", "questo"},
}


def detect_language(text: str) -> LanguageGuess:
    script, share = dominant_script(text)
    if script is None:
        return LanguageGuess(None, 0.0, None, [], "script")
    if script == "Latin":
        return _latin_language(text, share)
    if script in _UNIQUE_SCRIPT_LANG:
        lang = _UNIQUE_SCRIPT_LANG[script]
        cands = [lang] if script != "Latin" else ["en"]
        conf = share if script != "Latin" else share * 0.9  # romanised Indic text also looks Latin
        return LanguageGuess(lang, conf, script, cands)
    if script == "Bengali":
        if any(ch in _ASSAMESE_LETTERS for ch in text):
            return LanguageGuess("as", share * 0.95, script, ["as", "bn"], "script+letters")
        return LanguageGuess("bn", share * 0.85, script, ["bn", "as", "mni-Beng"], "script+letters")
    if script == "Arabic":
        if any(ch in _SINDHI_ARABIC for ch in text):
            return LanguageGuess("sd-Arab", share * 0.85, script, ["sd-Arab", "ur", "ks"], "script+letters")
        if any(ch in _KASHMIRI_ARABIC for ch in text):
            return LanguageGuess("ks", share * 0.85, script, ["ks", "ur"], "script+letters")
        return LanguageGuess("ur", share * 0.8, script, ["ur", "ks", "sd-Arab"], "script+letters")
    if script == "Devanagari":
        tokens = re.findall(r"[\u0900-\u097F]+", text)
        scores = {lang: sum(1 for t in tokens if t in marks) for lang, marks in _DEVANAGARI_MARKERS.items()}
        best = max(scores, key=lambda k: scores[k])
        ranked = sorted(scores, key=lambda k: -scores[k])
        total = sum(scores.values())
        if total == 0:
            return LanguageGuess("hi", share * 0.5, script, ["hi", "mr", "ne", "mai", "sa", "kok", "doi", "brx"], "script+function-words")
        conf = share * (scores[best] / total) * min(1.0, 0.5 + total / 10)
        return LanguageGuess(best, conf, script, ranked[:4], "script+function-words")
    return LanguageGuess(None, 0.0, script, [], "script")


def script_matches_language(text: str, language: str, script_of: dict[str, str]) -> tuple[bool, str | None, float]:
    """Is the dominant script of ``text`` the native script of ``language``?"""
    script, share = dominant_script(text)
    expected = script_of.get(language)
    if script is None or expected is None:
        return True, script, share
    return script == expected, script, share


# ---------------------------------------------------------------------------
# sentence splitting and chunking
# ---------------------------------------------------------------------------

_SENTENCE_END = re.compile(r"(?<=[.!?।॥؟۔])[\"'”’)\]]*\s+|\n+")
_CLAUSE_BREAK = re.compile(r"(?<=[,;:،])\s+")


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENTENCE_END.split(text.strip())]
    return [p for p in parts if p]


def _hard_wrap(piece: str, max_chars: int) -> list[str]:
    words = piece.split()
    out, cur = [], ""
    for w in words:
        while len(w) > max_chars:  # a single unbroken run longer than the limit
            if cur:
                out.append(cur)
                cur = ""
            out.append(w[:max_chars])
            w = w[max_chars:]
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= max_chars:
            cur += " " + w
        else:
            out.append(cur)
            cur = w
    if cur:
        out.append(cur)
    return out


def _split_long_sentence(sentence: str, max_chars: int) -> list[str]:
    if len(sentence) <= max_chars:
        return [sentence]
    out: list[str] = []
    cur = ""
    for clause in _CLAUSE_BREAK.split(sentence):
        for piece in ([clause] if len(clause) <= max_chars else _hard_wrap(clause, max_chars)):
            if not cur:
                cur = piece
            elif len(cur) + 1 + len(piece) <= max_chars:
                cur += " " + piece
            else:
                out.append(cur)
                cur = piece
    if cur:
        out.append(cur)
    return out


def chunk_sentences(text: str, max_chars: int) -> list[str]:
    """Pack whole sentences into chunks of at most ``max_chars``; split overlong sentences at clauses, then words."""
    chunks: list[str] = []
    cur = ""
    for sentence in split_sentences(text):
        for piece in _split_long_sentence(sentence, max_chars):
            if not cur:
                cur = piece
            elif len(cur) + 1 + len(piece) <= max_chars:
                cur += " " + piece
            else:
                chunks.append(cur)
                cur = piece
    if cur:
        chunks.append(cur)
    return chunks


def chunk_paragraphs(text: str, max_chars: int) -> list[str]:
    """Split on blank lines and pack paragraphs up to ``max_chars``; long paragraphs fall back to sentences."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    cur = ""
    for para in paragraphs:
        pieces = [para] if len(para) <= max_chars else chunk_sentences(para, max_chars)
        for piece in pieces:
            if not cur:
                cur = piece
            elif len(cur) + 2 + len(piece) <= max_chars:
                cur += "\n\n" + piece
            else:
                chunks.append(cur)
                cur = piece
    if cur:
        chunks.append(cur)
    return chunks


# ---------------------------------------------------------------------------
# what is worth translating
# ---------------------------------------------------------------------------

_NON_TRANSLATABLE = re.compile(r"^[\s\d\W_]*$")
_URL = re.compile(r"^(https?://|www\.)\S+$")


def needs_translation(text: str) -> bool:
    t = text.strip()
    if not t or _NON_TRANSLATABLE.match(t) or _URL.match(t):
        return False
    if t.startswith("$") and t.endswith("$"):
        return False
    return True


# ---------------------------------------------------------------------------
# similarity for verification
# ---------------------------------------------------------------------------

def normalize_for_compare(text: str) -> str:
    text = unicodedata.normalize("NFC", text).lower()
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] in ("L", "M", "N", "Z"))
    # Nukta and chandrabindu/anusvara variants are common ASR/TTS round-trip differences.
    text = text.replace("\u093c", "").replace("\u0901", "\u0902")
    return re.sub(r"\s+", " ", text).strip()


def similarity(expected: str, heard: str) -> float:
    a, b = normalize_for_compare(expected), normalize_for_compare(heard)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def length_ratio(expected: str, heard: str) -> float:
    a, b = normalize_for_compare(expected), normalize_for_compare(heard)
    return len(b) / max(1, len(a))


def repeated_ngram_ratio(text: str, n: int = 3) -> float:
    """Share of word n-grams that occur more than once: high values flag TTS/ASR repetition loops."""
    words = normalize_for_compare(text).split()
    grams = [tuple(words[i:i + n]) for i in range(max(0, len(words) - n + 1))]
    if len(grams) < 4:
        return 0.0
    counts = Counter(grams)
    return sum(c for c in counts.values() if c > 1) / len(grams)


# ---------------------------------------------------------------------------
# prompt-injection heuristics (for flagging only; content is always treated as data)
# ---------------------------------------------------------------------------

_INJECTION = re.compile(
    r"(ignore|disregard|forget)\s+(all\s+|any\s+|the\s+)?(previous|prior|above|earlier)\s+(instructions|prompts?|rules)"
    r"|you\s+are\s+now\s+|new\s+instructions?\s*:|system\s+prompt|developer\s+message"
    r"|\b(api[_ ]?key|password|secret)s?\b.*\b(print|reveal|send|show|output)"
    r"|(print|reveal|send|show|output)\b.*\b(api[_ ]?key|password|secret|environment variables)"
    r"|do\s+not\s+(translate|transcribe|summari[sz]e)|instead\s*,?\s+(write|say|output|translate)"
    r"|max[-_ ]cost|budget\s+(is|to)\s+|rm\s+-rf|delete\s+(all\s+)?files",
    re.IGNORECASE,
)


def injection_signals(text: str, limit: int = 3) -> list[str]:
    found = []
    for m in _INJECTION.finditer(text):
        start = max(0, m.start() - 30)
        found.append(text[start:m.end() + 30].replace("\n", " "))
        if len(found) >= limit:
            break
    return found
