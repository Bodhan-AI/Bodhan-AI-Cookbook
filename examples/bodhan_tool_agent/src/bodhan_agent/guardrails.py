import re
import unicodedata
from pathlib import Path

SUPPORTED_NOTE = (
    "English and these Indian languages: Assamese, Bengali, Bodo, Dogri, Gujarati, Hindi, Kannada, "
    "Konkani, Kashmiri, Maithili, Malayalam, Manipuri, Marathi, Nepali, Odia, Punjabi, Sanskrit, "
    "Santali, Sindhi, Tamil, Telugu, Urdu"
)

UNSUPPORTED_LANGUAGE_NAMES = {
    "french", "spanish", "german", "italian", "portuguese", "dutch", "russian", "japanese",
    "korean", "chinese", "mandarin", "cantonese", "arabic", "turkish", "thai", "vietnamese",
    "indonesian", "malay", "swahili", "hebrew", "greek", "swedish", "norwegian", "danish",
    "finnish", "persian", "farsi", "ukrainian", "czech", "hungarian", "romanian", "filipino", "tagalog",
}

# Unicode blocks of Indian scripts (Arabic block is used by Urdu, Kashmiri and Sindhi)
INDIC_RANGES = ((0x0600, 0x06FF), (0x0900, 0x0DFF), (0x1C50, 0x1C7F), (0xABC0, 0xABFF))
PUNCT = " \t\r\n।॥.,;:!?\"'()[]{}<>-—–…*_`#|/\\"


# ---------- Guardrail: language gate (runs before any model call) ----------
def check_language(task: str):
    words = set(re.findall(r"[a-z]+", task.lower()))
    found = sorted(UNSUPPORTED_LANGUAGE_NAMES & words)
    if not found:
        return None
    names = ", ".join(w.capitalize() for w in found)
    return {
        "languages": found,
        "summary": (f"The task mentions {names}, which the Bodhan models do not support. "
                    f"They work with {SUPPORTED_NOTE}. No files were produced."),
    }


# ---------- Guardrail: path sandbox ----------
def path_allowed(raw, roots) -> bool:
    try:
        path = Path(str(raw).strip().strip('"').strip("'")).expanduser().resolve()
    except (OSError, ValueError):
        return False
    return any(path == root or root in path.parents for root in roots)


# ---------- Guardrail: the LLM never writes Indian-language text ----------
def has_indic(text: str) -> bool:
    return any(lo <= ord(ch) <= hi for ch in text for lo, hi in INDIC_RANGES)


def norm_word(word: str) -> str:
    return unicodedata.normalize("NFC", word).strip(PUNCT)


def collect_strings(value, found=None):
    if found is None:
        found = []
    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            collect_strings(item, found)
    elif isinstance(value, (list, tuple)):
        for item in value:
            collect_strings(item, found)
    return found


def add_to_pool(pool: set, result) -> None:
    """Remember every Indian-language word that appeared in a tool result (its provenance)."""
    for text in collect_strings(result):
        for word in text.split():
            if has_indic(word):
                pool.add(norm_word(word))


def unseen_indic_words(text: str, pool: set) -> list:
    """Indian-language words that no tool ever produced, so the LLM must have written them."""
    return [w for w in text.split() if has_indic(w) and norm_word(w) not in pool]


def strip_unseen_indic(text: str, pool: set):
    kept, removed = [], 0
    for piece in re.split(r"(\s+)", text):
        if piece.strip() and has_indic(piece) and norm_word(piece) not in pool:
            removed += 1
            continue
        kept.append(piece)
    return "".join(kept), removed


# ---------- Guardrail: prompt-injection scan on scanned content ----------
INJECTION_PATTERNS = [re.compile(p, re.IGNORECASE) for p in (
    r"ignore (all |any |the |your )?(previous|prior|above|earlier)",
    r"disregard (all |any |the |your )?(previous|prior|above|earlier|instructions)",
    r"(new|updated|real) instructions?",
    r"system prompt",
    r"you (are|must) now",
    r"reveal .{0,40}(key|password|secret|token)",
    r"\b(api|secret)[ _-]?key\b",
    r"delete (all |the )?files?",
    r"(instead|now),? (call|use|run|send|open) ",
)]


def scan_injection(result) -> list:
    hits = []
    for text in collect_strings(result):
        for pattern in INJECTION_PATTERNS:
            found = pattern.search(text)
            if found:
                hits.append(found.group(0)[:60])
    return hits


# ---------- Verification: compare what was spoken with what is heard ----------
def normalize_for_compare(text: str) -> str:
    """Same treatment for both sides: Unicode form, no punctuation, Indian digits become 0-9."""
    text = unicodedata.normalize("NFC", text).lower()
    out = []
    for ch in text:
        if ch in ("\u200c", "\u200d"):      # invisible joiners, never spoken
            continue
        category = unicodedata.category(ch)
        if category == "Nd":
            out.append(str(unicodedata.digit(ch)))
        elif category[0] in ("P", "S", "C"):
            out.append(" ")
        else:
            out.append(ch)
    return re.sub(r"\s+", " ", "".join(out)).strip()


def edit_distance(a: str, b: str) -> int:
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def speech_check(reference: str, heard: str, threshold: float = 0.30) -> dict:
    ref = normalize_for_compare(reference)
    got = normalize_for_compare(heard)
    if not ref:
        return {"cer": None, "passed": False, "note": "reference text is empty"}
    cer = round(edit_distance(ref, got) / len(ref), 3)
    return {"cer": cer, "passed": cer <= threshold, "threshold": threshold,
            "reference_chars": len(ref), "heard_chars": len(got)}