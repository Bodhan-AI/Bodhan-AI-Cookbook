"""Text normalisation and comparison.

Everything here is deterministic and independent of any model. It exists so a
character error rate means something: Indic digits are folded to ASCII, Unicode
is NFKC-normalised, punctuation and whitespace are removed, and case is
ignored before two strings are compared.

Also houses the repetition guard, because an ASR or TTS model that loops is a
known failure mode and needs a cheap detector rather than a model call.
"""

from __future__ import annotations

import re
import unicodedata

_WS = re.compile(r"\s+")
_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)
_PUNCT_ONLY = re.compile(r"^[\W_]*$", re.UNICODE)
_TERMINAL = frozenset(".!?।॥؟")


def normalize_ws(text: str) -> str:
    return _WS.sub(" ", text.replace("\x00", " ")).strip()


def is_degenerate(text: str) -> bool:
    """True for empty or punctuation-only output, which some ASR runs return."""
    stripped = normalize_ws(text)
    return not stripped or bool(_PUNCT_ONLY.match(stripped))


def ends_sentence(text: str) -> bool:
    stripped = normalize_ws(text)
    return bool(stripped) and stripped[-1] in _TERMINAL


def fold_digits(text: str) -> str:
    """Map any Unicode decimal digit to its ASCII equivalent (२५ → 25)."""
    out = []
    for char in text:
        try:
            value = unicodedata.digit(char)
        except (TypeError, ValueError):
            out.append(char)
        else:
            out.append(str(value))
    return unicodedata.normalize("NFKC", "".join(out))


def normalize_for_compare(text: str) -> str:
    """Canonical form used by every error-rate comparison."""
    text = fold_digits(unicodedata.normalize("NFKC", text)).casefold()
    text = _NON_WORD.sub("", text)
    return text


def char_error_rate(reference: str, hypothesis: str) -> float:
    """Levenshtein distance over normalised characters, divided by reference length."""
    ref = normalize_for_compare(reference)
    hyp = normalize_for_compare(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    return levenshtein(ref, hyp) / len(ref)


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current = [i]
        for j, char_b in enumerate(b, start=1):
            insert = current[j - 1] + 1
            delete = previous[j] + 1
            substitute = previous[j - 1] + (char_a != char_b)
            current.append(min(insert, delete, substitute))
        previous = current
    return previous[-1]


def collapse_repeats(text: str, *, min_words: int = 3, max_repeats: int = 2) -> tuple[str, bool]:
    """Collapse a phrase repeated back-to-back more than ``max_repeats`` times.

    Returns the cleaned text and whether anything changed. Deliberately
    conservative: it needs at least ``min_words`` identical consecutive tokens,
    so ordinary emphasis ("very very good") is untouched.
    """
    original = text
    for _ in range(6):
        changed = False
        for size in (12, 10, 8, 6, 5, 4, 3):
            if size < min_words:
                continue
            pattern = re.compile(
                r"((?:\S+\s+){" + str(size - 1) + r"}\S+)(?:\s+\1){" + str(max_repeats) + r",}"
            )
            new_text, count = pattern.subn(r"\1", text)
            if count:
                text = new_text
                changed = True
                break
        if not changed or text == original:
            break
    text = normalize_ws(text)
    return text, text != original


def word_count(text: str) -> int:
    return len(normalize_ws(text).split())


def truncation_ratio(expected: str, heard: str) -> float:
    """How much of the expected text was heard, by normalised character count."""
    ref = normalize_for_compare(expected)
    hyp = normalize_for_compare(heard)
    if not ref:
        return 1.0
    return min(1.0, len(hyp) / len(ref))
