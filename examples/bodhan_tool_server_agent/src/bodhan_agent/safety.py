"""Untrusted-content wrapping, injection flags, and the rule that the LLM never authors Indic text."""
import re

from bodhan_mcp.languages import has_indic

UNTRUSTED_KEYS = {"preview", "translation", "text"}
INJECTION = re.compile(
    r"\bignore (all |any |the )?(previous|prior|above)\b|\bdisregard (the )?(task|instructions)\b|\bsystem prompt\b|"
    r"\byou (must|should) now\b|\bnew instructions\b|\bact as\b|\breveal .*(key|secret)|\bforget (everything|your)\b|"
    # Hindi / Marathi: "ignore (all) previous instructions", "API key", "system prompt"
    r"(पिछले|पिछली|मागील|सभी|सर्व|ऊपर के)\s*(सभी\s*|सर्व\s*)?(निर्देश|सूचना|आदेश)|"
    r"अनदेखा करें|दुर्लक्ष\S*\s*करा|(एपीआई|api)\s*(कुंजी|की)|सिस्टम\s*प्रॉम्प्ट", re.IGNORECASE)
_TAG = re.compile(r"<\s*/?\s*untrusted_content[^>]*>", re.IGNORECASE)
_PATHY = re.compile(r"(_ref|path|paths|pages)$")  # arguments that hold file names or page ranges, not prose
_INDIC_RUN = re.compile(r"[؀-ۿऀ-෿](?:[؀-ۿऀ-෿\s,.;:!?।॥-]*[؀-ۿऀ-෿])?")


def wrap(source: str, text: str) -> str:
    text = _TAG.sub("", re.sub(r"[\x00-\x08\x0b-\x1f]", "", str(text)))  # any case or spacing of an opening/closing tag
    return f'<untrusted_content source="{source}">{text}</untrusted_content>'


def injection_hits(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(0) for m in INJECTION.finditer(text or "")))


def _strings(x, key=""):
    if isinstance(x, str):
        if not _PATHY.search(key):
            yield x
    elif isinstance(x, dict):
        for k, v in x.items():
            yield from _strings(v, str(k))
    elif isinstance(x, (list, tuple)):
        for v in x:
            yield from _strings(v, key)


# Words that are Hindi/Urdu but not English, so a run of them means romanized (Latin-script) Hindi. Words that are also
# English ("main", "par", "kar", "hum", "so", "to") are left out on purpose.
_ROMAN_HI = frozenset({
    "hai", "hain", "nahi", "nahin", "kya", "aap", "mera", "meri",
    "kaise", "kyun", "bahut", "accha", "acha", "namaste", "dhanyavad", "shukriya",
    "mein", "tha", "thi", "raha", "rahi", "karo", "hota", "hoti",
    "wala", "wali", "bhi", "toh", "yeh", "woh", "kuch", "kaun",
    "kahan", "aur", "apna", "apni", "hamara", "hamari", "tumhara", "tumhari",
    "hoon",
})


def looks_romanized_indic(text: str) -> bool:
    """Heuristic for Hindi/Urdu typed in Latin letters: at least 3 marker words (4 in a longer text) and one word in
    five. It does not cover other romanized languages; the script check covers the rest."""
    words = re.findall(r"[a-z]+", text.lower())
    hits = sum(1 for w in words if w in _ROMAN_HI)
    return hits >= (3 if len(words) < 12 else 4) and hits / max(1, len(words)) >= 0.2


def is_indic_text(text: str) -> bool:
    return has_indic(text) or looks_romanized_indic(text)


def llm_wrote_indic(args) -> bool:
    """Any string the LLM passes to a tool (or in its plan or finish) must not be Indian-language text, in native
    script or romanized Hindi."""
    return any(is_indic_text(s) for s in _strings(args))


def strip_indic(text: str) -> str:
    """Last resort once the LLM keeps writing Indic text: remove it so it never reaches result.md."""
    if looks_romanized_indic(text):
        return "[removed: non-English text]"
    return _INDIC_RUN.sub("[removed: non-English text]", text) if has_indic(text) else text


def split_task_text(task: str) -> tuple[str, list[str]]:
    """Indic runs inside the user's task, replaced by numbered placeholders; returns (new task, the runs)."""
    runs: list[str] = []

    def rep(m: re.Match) -> str:
        runs.append(m.group(0))
        return f"<task text #{len(runs)}>"
    return _INDIC_RUN.sub(rep, task), runs
