"""Spell checked numerals in the narration language without rewriting surrounding text."""

import re
import unicodedata
from functools import lru_cache

from indic_normalizer.lexicon import get_lexicon
from indic_normalizer.numbers import cardinal, cardinal_variations, split_digits

from ..models import SpokenNumber
from ..ocr.extraction import MATH_SPAN
from ..translation.segments import UNITS
from .tamil_numbers import spell_tamil_number

# Accept both Western and Indian grouping and keep every digit in dotted
# references. Match opaque content first so identifiers and LaTeX stay intact.
GROUPING = r"[,٬]"
INTEGER = rf"(?:\d{{1,3}}(?:{GROUPING}\d{{3}})+|\d{{1,2}}(?:{GROUPING}\d{{2}})*{GROUPING}\d{{3}}|\d+)"
NUMERAL = rf"[+\-−]?(?:{INTEGER}(?:[.٫]\d+)*|[.٫]\d+)"
NUMBER_IN_TEXT = re.compile(
    rf"(?s:{MATH_SPAN.pattern})|`[^`\n]+`|(?i:https?://|www\.)\S+"
    rf"|\b[A-Za-z][A-Za-z0-9_]*-\d+[\w.-]*"
    rf"|(?<![A-Za-z_\d.٫])(?P<number>{NUMERAL})"
    rf"(?:(?P<unit>{UNITS}|°[CFcf]?|[%‰])(?!\w)|(?![A-Za-z_\d]|[.٫]\d))"
)
NUMERAL_MARKS = str.maketrans({"−": "-", "٫": ".", ",": None, "٬": None})

# The upstream decimal separator falls back to English for these locales.
# Use the mathematical loanwords in the same script as the cardinal words.
DECIMAL_POINTS = {"brx": "दशमलव", "mni": "ꯄꯣꯏꯟꯠ", "sat": "ᱯᱚᱭᱮᱱᱴ"}
SIGN_WORDS = {
    "as": ("ধনাত্মক", "ঋণাত্মক"),
    "bn": ("ধনাত্মক", "ঋণাত্মক"),
    "brx": ("प्लस", "माइनास"),
    "doi": ("प्लस", "माइनस"),
    "en": ("plus", "minus"),
    "gu": ("ધન", "ઋણ"),
    "hi": ("धनात्मक", "ऋणात्मक"),
    "kn": ("ಧನಾತ್ಮಕ", "ಋಣಾತ್ಮಕ"),
    "kok": ("प्लस", "मायनस"),
    "ks": ("جمع", "منفی"),
    "mai": ("धनात्मक", "ऋणात्मक"),
    "ml": ("ധന", "ഋണ"),
    "mni": ("ꯄ꯭ꯂꯁ", "ꯃꯥꯏꯅꯁ"),
    "mr": ("धन", "ऋण"),
    "ne": ("धनात्मक", "ऋणात्मक"),
    "or": ("ଧନାତ୍ମକ", "ଋଣାତ୍ମକ"),
    "pa": ("ਧਨਾਤਮਕ", "ਰਿਣਾਤਮਕ"),
    "sa": ("धन", "ऋण"),
    "sat": ("ᱯᱞᱟᱥ", "ᱢᱟᱭᱱᱟᱥ"),
    "sd": ("प्लस", "माइनस"),
    "te": ("ధనాత్మక", "ఋణాత్మక"),
    "ur": ("مثبت", "منفی"),
}


def _number_literal(match: re.Match[str]) -> str | None:
    if match["number"] is not None:
        return match["number"]
    math = next((part.strip() for part in match.groups()[:MATH_SPAN.groups] if part is not None), "")
    return math if re.fullmatch(NUMERAL, math) else None


def has_speech_numerals(text: str) -> bool:
    """Detect raw numeric values in a script written by an earlier policy."""
    return any(_number_literal(match) is not None for match in NUMBER_IN_TEXT.finditer(text))


def _canonical_numeral(text: str) -> str:
    digits = "".join(str(unicodedata.decimal(char)) if char.isdecimal() else char for char in text)
    return re.sub(r"^([+-]?)\.", r"\g<1>0.", digits.translate(NUMERAL_MARKS))


def _replace_number_span(match: re.Match[str], text: str) -> str:
    """Separate a number from compact units or native suffixes such as Bengali টি."""
    unit = match["unit"]
    if unit:
        text = f"{text} {unit}"
    before = match.string[max(0, match.start() - 1):match.start()]
    after = match.string[match.end():match.end() + 1]
    if before and unicodedata.category(before)[0] in "LM":
        text = f" {text}"
    if after and unicodedata.category(after)[0] in "LM":
        text = f"{text} "
    return text


@lru_cache(maxsize=2048)
def _number_words(value: str, language: str) -> str:
    if language == "ta":
        return spell_tamil_number(value)

    sign = value[0] if value[0] in "+-" else ""
    whole, *fractions = (value[1:] if sign else value).split(".")
    reading = split_digits(whole, language) if len(whole) > 1 and whole[0] == "0" else cardinal(whole, language)
    point = DECIMAL_POINTS.get(language, get_lexicon(language).decimal_point)
    words = [reading]
    for fraction in fractions:
        words.extend((point, split_digits(fraction, language)))
    if sign:
        words.insert(0, SIGN_WORDS[language][0 if sign == "+" else 1])
    return unicodedata.normalize("NFC", " ".join(" ".join(words).split()))


def spell_numbers(text: str, language: str) -> tuple[str, tuple[SpokenNumber, ...]]:
    """Return the exact speech text and its numeric values for back-transcription QA."""
    readings = []

    def speak(match: re.Match[str]) -> str:
        literal = _number_literal(match)
        if literal is None:
            return match[0]
        value = _canonical_numeral(literal)
        words = _number_words(value, language)
        if not words or any(char.isdecimal() for char in words):
            raise ValueError(f"Could not spell the number {value} for {language} narration.")
        readings.append(SpokenNumber(value, words))
        return _replace_number_span(match, words)

    return NUMBER_IN_TEXT.sub(speak, text), tuple(dict.fromkeys(readings))


def normalize_digit_text(text: str) -> str:
    """Canonicalize written numerals for QA, including a compact measure or degree sign."""
    def normalize(match: re.Match[str]) -> str:
        if match["number"] is None:
            return match[0]
        return _replace_number_span(match, _canonical_numeral(match["number"]))

    return NUMBER_IN_TEXT.sub(normalize, text)


@lru_cache(maxsize=2048)
def alternate_number_words(value: str, language: str) -> tuple[str, ...]:
    """Return equivalent readings the back-transcript may use."""
    variants = []
    if value.isdigit() and (len(value) == 1 or not value.startswith("0")):
        variants.extend(cardinal_variations(value, language))
    if language == "hi":
        reading = _number_words(value, language)
        if "छः" in reading:
            variants.append(reading.replace("छः", "छह"))
    return tuple(variants)
