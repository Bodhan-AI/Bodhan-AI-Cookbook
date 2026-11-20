"""Text helpers shared by the stages: numbers, digits, sentence chunks, and comparing speech with text."""

import re
import unicodedata

NUMBER_WORDS = {w: str(i) for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve".split())}

# Zero digit of each language's script. Indic-Speak reads Latin digits as English words
# ("180" -> "one hundred and eighty"), but reads native digits in the language itself.
DIGIT_ZERO = {
    "hi": "०", "mr": "०", "bn": "০", "as": "০", "ta": "௦", "te": "౦", "kn": "೦",
    "ml": "൦", "gu": "૦", "pa": "੦", "or": "୦", "ur": "۰",
}


def numbers(text: str) -> list[int]:
    """Every number in the text, sorted. Python's \\d matches any script's digits (२५, ২৫, 25)."""
    return sorted(int(n) for n in re.findall(r"\d+", text))


def digits(text: str) -> str:
    """Small English number words as digits, so "one pair" and "1 pair" compare equal."""
    return re.sub(r"\b[A-Za-z]+\b", lambda m: NUMBER_WORDS.get(m.group(0).lower(), m.group(0)), text)


def native_digits(text: str, lang: str) -> str:
    zero = DIGIT_ZERO.get(lang)
    if not zero:
        return text
    return "".join(chr(ord(zero) + int(c)) if "0" <= c <= "9" else c for c in text)


def sentences(text: str) -> list[str]:
    """Split at sentence ends: . ? ! and the Indic danda । ॥"""
    return [s.strip() for s in re.split(r"(?<=[.?!।॥])\s+", text.strip()) if s.strip()]


def chunks(text: str, max_chars: int) -> list[str]:
    """Whole sentences, grouped into pieces of at most about max_chars characters."""
    out = []
    for sentence in sentences(text):
        if out and len(out[-1]) + len(sentence) < max_chars:
            out[-1] += " " + sentence
        else:
            out.append(sentence)
    return out or [text]


def compare(reference: str, heard: str, latin: bool = False) -> dict:
    """Compare the text sent to TTS with what Indic-Transcribe heard.

    Only native letters and vowel signs are compared: the speech says "180" and "AOC" as words,
    which would otherwise count as errors. For English (latin=True), Latin letters, case-folded.
    Returns:
      cer     - standard character error rate (reported)
      missing - share of the text not heard; extra heard characters are free (catches omissions)
      extra   - heard length over expected length, allowing for spoken numbers and labels (catches repeats)
    """
    def norm(t: str) -> str:
        t = unicodedata.normalize("NFC", t)
        if latin:
            return "".join(c for c in t.lower() if c.isascii() and c.isalpha())
        return "".join(c for c in t if unicodedata.category(c)[0] in "LM" and not c.isascii())

    a, b = norm(reference), norm(heard)
    if not a:
        return {"cer": 0.0, "missing": 0.0, "extra": 1.0}
    labels = 0 if latin else 2 * len(re.findall(r"[A-Za-z]", reference))  # Latin labels are spoken as words
    expected = len(a) + labels + 5 * len(re.findall(r"\d", reference))
    return {
        "cer": round(_edits(a, b, insert_cost=1) / len(a), 3),
        "missing": round(_edits(a, b, insert_cost=0) / len(a), 3),
        "extra": round(len(b) / expected, 3),
    }


def _edits(a: str, b: str, insert_cost: int) -> int:
    """Levenshtein distance from a to b, with a configurable cost for characters only in b."""
    prev = [j * insert_cost for j in range(len(b) + 1)]
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + insert_cost, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]
