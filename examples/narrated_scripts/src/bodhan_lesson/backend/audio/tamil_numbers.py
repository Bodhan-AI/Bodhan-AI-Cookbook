"""Verbalize checked numerals for Tamil narration and compare their spoken values."""

import re
from collections.abc import Mapping

DIGITS = (
    "பூஜ்ஜியம்", "ஒன்று", "இரண்டு", "மூன்று", "நான்கு", "ஐந்து", "ஆறு", "ஏழு", "எட்டு", "ஒன்பது",
)
TEENS = (
    "பத்து", "பதினொன்று", "பன்னிரண்டு", "பதின்மூன்று", "பதினான்கு", "பதினைந்து", "பதினாறு",
    "பதினேழு", "பதினெட்டு", "பத்தொன்பது",
)
TENS = ("இருபது", "முப்பது", "நாற்பது", "ஐம்பது", "அறுபது", "எழுபது", "எண்பது", "தொண்ணூறு")
TENS_PREFIXES = ("இருபத்", "முப்பத்", "நாற்பத்", "ஐம்பத்", "அறுபத்", "எழுபத்", "எண்பத்", "தொண்ணூற்")
TENS_ENDINGS = ("", "ொன்று", "ிரண்டு", "ுமூன்று", "ுநான்கு", "ைந்து", "ாறு", "ேழு", "ெட்டு", "ொன்பது")
HUNDREDS = ("நூறு", "இருநூறு", "முந்நூறு", "நானூறு", "ஐந்நூறு", "அறுநூறு", "எழுநூறு", "எண்ணூறு", "தொள்ளாயிரம்")
HUNDRED_PREFIXES = tuple(f"{word[:-2]}ற்று" for word in HUNDREDS[:-1]) + ("தொள்ளாயிரத்து",)
THOUSANDS = (
    "ஓராயிரம்", "இரண்டாயிரம்", "மூவாயிரம்", "நான்காயிரம்", "ஐயாயிரம்", "ஆறாயிரம்", "ஏழாயிரம்",
    "எட்டாயிரம்", "ஒன்பதாயிரம்",
)
SCALES = ((10_000_000, "கோடி", "கோடியே"), (100_000, "லட்சம்", "லட்சத்து"), (1000, "ஆயிரம்", "ஆயிரத்து"))
SIGNS = {"-": "எதிர்மறை", "−": "எதிர்மறை", "+": "நேர்மறை"}
POINT = " புள்ளி "

def _below_hundred(value: int) -> str:
    if value < 20:
        return (*DIGITS, *TEENS)[value]
    tens, ones = divmod(value, 10)
    if not ones:
        return TENS[tens - 2]
    consonant = "ற" if tens == 9 else "த"
    return f"{TENS_PREFIXES[tens - 2]}{consonant}{TENS_ENDINGS[ones]}"


def _cardinal(value: int) -> str:
    for magnitude, word, prefix in SCALES:
        if value >= magnitude:
            count, rest = divmod(value, magnitude)
            count_word = "ஒரு" if count == 1 else _cardinal(count)
            scale = f"{count_word} {prefix if rest else word}"
            if magnitude == 1000 and count < 10:
                scale = THOUSANDS[count - 1]
                if rest:
                    scale = f"{scale[:-2]}த்து"
            return f"{scale} {_cardinal(rest)}" if rest else scale
    hundreds, rest = divmod(value, 100)
    if not hundreds:
        return _below_hundred(rest)
    if not rest:
        return HUNDREDS[hundreds - 1]
    return f"{HUNDRED_PREFIXES[hundreds - 1]} {_below_hundred(rest)}"


def spell_tamil_number(value: str) -> str:
    """Read canonical ASCII digits using the Tamil pronunciation policy."""
    sign = SIGNS.get(value[0])
    integer, *fractions = (value[1:] if sign else value).split(".")
    if len(integer) > 1 and integer.startswith("0"):
        whole = " ".join(DIGITS[int(digit)] for digit in integer)
    else:
        whole = _cardinal(int(integer))
    parts = [whole, *(" ".join(DIGITS[int(digit)] for digit in part) for part in fractions)]
    spoken = POINT.join(parts)
    return f"{sign} {spoken}" if sign else spoken


def _alternatives(words: Mapping[str, int]) -> str:
    return "(?:" + "|".join(map(re.escape, sorted(words, key=len, reverse=True))) + ")"


DIGIT_VALUES = {word: value for value, word in enumerate(DIGITS)}
DIGIT_VALUES.update({"பூச்சியம்": 0, "ஒன்னு": 1, "ஒண்ணு": 1, "ரெண்டு": 2})
SMALL_VALUES = {_below_hundred(value): value for value in range(100)} | DIGIT_VALUES
for tens, prefix in enumerate(TENS_PREFIXES, 2):
    connector = f"{prefix}{'று' if tens == 9 else 'து'}"
    SMALL_VALUES.update({f"{connector} {word}": 10 * tens + ones for ones, word in enumerate(DIGITS) if ones})
HUNDRED_VALUES = {word: value * 100 for value, word in enumerate(HUNDREDS, 1)}
HUNDRED_CONNECTORS = {word: value * 100 for value, word in enumerate(HUNDRED_PREFIXES, 1)}
THOUSAND_VALUES = {word: value * 1000 for value, word in enumerate(THOUSANDS, 1)}
THOUSAND_CONNECTORS = {f"{word[:-2]}த்து": value for word, value in THOUSAND_VALUES.items()}
SMALL_PATTERN = _alternatives(SMALL_VALUES)
CARDINAL_GROUP = (
    rf"(?:{_alternatives(HUNDRED_CONNECTORS)} {SMALL_PATTERN}|{_alternatives(HUNDRED_VALUES)}|{SMALL_PATTERN})"
)
SCALE_COUNT = rf"(?:ஒரு|{CARDINAL_GROUP})"
CARDINAL_PATTERN = CARDINAL_GROUP
for scale, terminal, connector in reversed(SCALES[1:]):
    whole = rf"{SCALE_COUNT} {terminal}"
    prefix = rf"{SCALE_COUNT} {connector}"
    if scale == 1000:
        whole = rf"(?:{whole}|{_alternatives(THOUSAND_VALUES)})"
        prefix = rf"(?:{prefix}|{_alternatives(THOUSAND_CONNECTORS)})"
    CARDINAL_PATTERN = rf"(?:(?:{prefix} )?{CARDINAL_PATTERN}|{whole})"
CRORE_COUNT = rf"(?:ஒரு|{CARDINAL_PATTERN})"
CRORE = rf"{CRORE_COUNT}(?: கோடி)* கோடி"
CARDINAL_PATTERN = rf"(?:(?:{CRORE_COUNT}(?: கோடி)* (?:கோடியே|கோடி) )*(?:{CRORE}|{CARDINAL_PATTERN}))"

DIGIT_PATTERN = _alternatives(DIGIT_VALUES)
TAMIL_NUMBER_WORDS = re.compile(
    rf"(?<!\S)(?:(?P<sign>எதிர்மறை|நேர்மறை) )?"
    rf"(?P<whole>(?:பூஜ்ஜியம்|பூச்சியம்)(?: {DIGIT_PATTERN})+|{CARDINAL_PATTERN})"
    rf"(?P<decimals>(?:{POINT}{DIGIT_PATTERN}(?: {DIGIT_PATTERN})*)*)(?!\S)"
)
SCALE_VALUES = {word: scale for scale, *words in SCALES for word in words}
ADDITIVE_VALUES = (
    SMALL_VALUES | HUNDRED_VALUES | HUNDRED_CONNECTORS | THOUSAND_VALUES | THOUSAND_CONNECTORS | {"ஒரு": 1}
)
NUMBER_PARTS = re.compile(_alternatives(ADDITIVE_VALUES | SCALE_VALUES))


def normalize_tamil_numbers(text: str) -> str:
    """Normalize Tamil number words for speech QA."""
    def normalize(match: re.Match[str]) -> str:
        whole = match["whole"]
        if whole.startswith(("பூஜ்ஜியம் ", "பூச்சியம் ")):
            integer = "".join(str(DIGIT_VALUES[word]) for word in whole.split())
        else:
            higher = total = group = 0
            for word in NUMBER_PARTS.findall(whole):
                if SCALE_VALUES.get(word) == SCALES[0][0]:
                    higher = (higher + total + group) * SCALES[0][0]
                    total = group = 0
                elif word in SCALE_VALUES:
                    total += group * SCALE_VALUES[word]
                    group = 0
                else:
                    group += ADDITIVE_VALUES[word]
            integer = str(higher + total + group)
        decimals = "".join(
            "." + "".join(str(DIGIT_VALUES[word]) for word in part.split())
            for part in match["decimals"].split(POINT)[1:]
        )
        sign = {"எதிர்மறை": "-", "நேர்மறை": "+"}.get(match["sign"], "")
        return f"{sign}{integer}{decimals}"

    return TAMIL_NUMBER_WORDS.sub(normalize, text)
