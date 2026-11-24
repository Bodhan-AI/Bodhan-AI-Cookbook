"""Helpers for text meant to be read aloud: symbol checks, numbers, length limits and a
rule-based LaTeX-to-speech converter (the fallback when the LLM's narration is unusable)."""

from __future__ import annotations

import re

# Anything a listener would hear as "open bracket", "dollar", "caret"... or that a TTS voice
# cannot pronounce sensibly. Spoken narration must contain none of these.
FORBIDDEN_SPOKEN = re.compile(r"[$\\{}()\[\]^_=+*/<>|#~`°∠⊥∥≠≤≥×÷√π∴∵±→]")


def forbidden_symbols(text: str) -> list[str]:
    return sorted(set(FORBIDDEN_SPOKEN.findall(text)))


def word_count(text: str) -> int:
    return len(text.split())


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.?!])\s+", text.strip()) if s]


def trim_to_words(text: str, max_words: int) -> str:
    """Keep whole sentences while they fit; a single over-long sentence is cut at the limit."""
    if word_count(text) <= max_words:
        return text
    kept: list[str] = []
    for sentence in sentences(text):
        if word_count(" ".join([*kept, sentence])) > max_words:
            break
        kept.append(sentence)
    if kept:
        return " ".join(kept)
    return " ".join(text.split()[:max_words]).rstrip(",;:") + "."


# ---- numbers ----------------------------------------------------------------------
_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
         "sixteen seventeen eighteen nineteen").split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def number_to_words(n: int) -> str:
    """English words for 0 <= n < 1,000,000 (digits are returned unchanged beyond that)."""
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + (f" {_ONES[n % 10]}" if n % 10 else "")
    if n < 1000:
        rest = f" and {number_to_words(n % 100)}" if n % 100 else ""
        return f"{_ONES[n // 100]} hundred{rest}"
    if n < 1_000_000:
        rest = f" {number_to_words(n % 1000)}" if n % 1000 else ""
        return f"{number_to_words(n // 1000)} thousand{rest}"
    return str(n)


def _spoken_forms(token: str) -> set[str]:
    """Digit and word spellings that count as having said `token`."""
    if "." in token:
        whole, _, fraction = token.partition(".")
        digits = " ".join(_ONES[int(d)] for d in fraction)
        return {token, f"{number_to_words(int(whole))} point {digits}"}
    n = int(token)
    words = number_to_words(n)
    return {token, words, words.replace(" and ", " ")}


def significant_numbers(source: str) -> list[str]:
    """Numbers a narration must not lose: decimals and integers of 10 or more.

    Single digits are skipped because they are routinely spoken as "half", "twice",
    "squared" or "first".
    """
    tokens = _NUMBER.findall(re.sub(r"\\[A-Za-z]+", " ", source))
    return list(dict.fromkeys(t for t in tokens if "." in t or int(t) >= 10))


def missing_numbers(source: str, spoken: str) -> list[str]:
    haystack = " ".join(spoken.lower().replace("-", " ").split())
    missing = []
    for token in significant_numbers(source):
        forms = _spoken_forms(token)
        if not any(re.search(rf"(?<![\w.]){re.escape(form)}(?![\w]|\.\d)", haystack) for form in forms):
            missing.append(token)
    return missing


# ---- LaTeX -> speech (rule based) ------------------------------------------------------
_COMMANDS = {
    "angle": "angle", "triangle": "triangle", "parallel": "is parallel to", "perp": "is perpendicular to",
    "times": "times", "cdot": "times", "div": "divided by", "pm": "plus or minus", "neq": "is not equal to",
    "ne": "is not equal to", "leq": "is less than or equal to", "le": "is less than or equal to",
    "geq": "is greater than or equal to", "ge": "is greater than or equal to", "therefore": "therefore",
    "because": "because", "cong": "is congruent to", "sim": "is similar to", "pi": "pi", "alpha": "alpha",
    "beta": "beta", "gamma": "gamma", "theta": "theta", "circ": "degrees", "ldots": "and so on",
    "cdots": "and so on", "infty": "infinity", "rightarrow": "goes to", "Rightarrow": "implies",
    "degree": "degrees", "prime": "prime",
}
_PREFIXED = {"overline": "line segment", "overrightarrow": "ray", "overleftrightarrow": "line",
             "widehat": "angle", "hat": "angle", "vec": "vector"}
_SIMPLE_SYMBOLS = (("=", " equals "), ("+", " plus "), ("<", " is less than "), (">", " is greater than "),
                   ("%", " percent "), ("/", " by "), (":", " to "), ("*", " times "), ("×", " times "),
                   ("°", " degrees "), ("∠", " angle "), ("⊥", " is perpendicular to "), ("∥", " is parallel to "),
                   ("−", " minus "), ("-", " minus "))
_SPELL_CAPS = re.compile(r"\b[A-Z]{2,4}\b")
_ARG = r"\{([^{}]*)\}"


def _spell(word: str) -> str:
    """Spaced capital letters are read as letter names: ABC -> A B C."""
    return " ".join(word) if re.fullmatch(r"[A-Z]{2,4}", word.strip()) else word


def latex_to_speech(latex: str) -> tuple[str, int]:
    """Spoken English for a LaTeX fragment, plus the number of commands it could not translate."""
    text = latex.replace("$", " ")
    text = re.sub(r"\\(?:text|mathrm|mbox)" + _ARG, lambda m: " " + _spell(m.group(1)) + " ", text)
    for _ in range(4):  # innermost-first, so nested fractions resolve
        text = re.sub(r"\\d?frac" + _ARG + _ARG, r" \1 over \2 ", text)
    text = re.sub(r"\\sqrt" + _ARG, r" square root of \1 ", text)
    for command, words in _PREFIXED.items():
        text = re.sub(rf"\\{command}\s*{_ARG}", lambda m, w=words: f" {w} {_spell(m.group(1))} ", text)
    text = re.sub(r"\^\s*\{?\s*\\circ\s*\}?", " degrees ", text)
    text = re.sub(r"\^\s*\{?2\}?", " squared ", text)
    text = re.sub(r"\^\s*\{?3\}?", " cubed ", text)
    text = re.sub(r"\^\s*\{([^{}]*)\}", r" to the power \1 ", text)
    text = re.sub(r"\^\s*(\w)", r" to the power \1 ", text)
    text = re.sub(r"_\s*\{([^{}]*)\}", r" \1 ", text)
    text = text.replace("_", " ")
    text = re.sub(r"\\(?:left|right|big|Big|quad|qquad|,|;|!|:)\s*", " ", text)
    text = text.replace("\\\\", " ")
    unknown = 0

    def command(match: re.Match[str]) -> str:
        nonlocal unknown
        name = match.group(1)
        if name in _COMMANDS:
            return f" {_COMMANDS[name]} "
        unknown += 1
        return " "

    text = re.sub(r"\\([A-Za-z]+)", command, text)
    for symbol, words in _SIMPLE_SYMBOLS:
        text = text.replace(symbol, words)
    text = re.sub(r"[(){}\[\]|\\^~`]", " ", text)
    text = _SPELL_CAPS.sub(lambda m: _spell(m.group(0)), text)
    return re.sub(r"\s+", " ", text).strip(), unknown


# ---- Markdown -> plain speakable text ----------------------------------------------------
_MATH_ANY = re.compile(r"\$\$(.+?)\$\$|\$(.+?)\$", re.DOTALL)


def plain_speech(markdown: str) -> str:
    """Strip Markdown and speak the math: the deterministic narration of a lesson element."""
    text = re.sub(r"<!--.*?-->", " ", markdown, flags=re.DOTALL)
    text = re.sub(r"```[a-z]*\n?|```", " ", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = _MATH_ANY.sub(lambda m: f" {latex_to_speech(m.group(1) or m.group(2))[0]} ", text)
    text = re.sub(r"(?m)^\s*(?:#{1,6}|>)\s*", "", text)
    text = re.sub(r"\bFig\.\s*", "Figure ", text)
    text = re.sub(r"[*_`|]", " ", text)
    text = re.sub(r"\(\s*(\d+)\s*\)", r" equation \1 ", text)
    text = re.sub(r"[(){}\[\]]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def scrub_symbols(text: str) -> str:
    """Last-resort cleanup so no forbidden symbol reaches the TTS: speak or drop it."""
    for symbol, words in _SIMPLE_SYMBOLS:
        text = text.replace(symbol, words)
    return re.sub(r"\s+", " ", FORBIDDEN_SPOKEN.sub(" ", text)).strip()


# ---- spoken numbers -> values (the inverse of number_to_words) -------------------------
_WORD_VALUES = {word: value for value, word in enumerate(_ONES)}
_WORD_VALUES |= {word: 10 * value for value, word in enumerate(_TENS) if word != "_"}
_NUMBER_TOKEN = re.compile(r"[a-z]+|\d+(?:\.\d+)?")


def spoken_numbers(text: str) -> set[str]:
    """Numbers stated in `text`, as digit strings, whether written as digits or in words.

    "one hundred and eighty degrees" -> "180"; "six point one three" -> "6.13".
    """
    tokens = _NUMBER_TOKEN.findall(text.lower().replace("-", " "))
    found: set[str] = set()
    total = current = 0
    active = False  # inside a run of number words
    index = 0

    def flush() -> int | None:
        nonlocal total, current, active
        value = total + current if active else None
        total = current = 0
        active = False
        return value

    while index < len(tokens):
        token = tokens[index]
        if token in _WORD_VALUES:
            current += _WORD_VALUES[token]
            active = True
        elif token == "hundred" and active:
            current = (current or 1) * 100
        elif token == "thousand" and active:
            total, current = total + (current or 1) * 1000, 0
        elif (token == "and" and active and tokens[index - 1] in ("hundred", "thousand")
              and index + 1 < len(tokens) and tokens[index + 1] in _WORD_VALUES):
            pass  # only "one hundred and eighty" joins a number; "sixty four and thirty" is two
        elif token == "point" and active:
            whole = flush()
            fraction = ""
            while index + 1 < len(tokens) and tokens[index + 1] in _WORD_VALUES:
                index += 1
                fraction += str(_WORD_VALUES[tokens[index]])
            found.add(f"{whole}.{fraction}" if fraction else str(whole))
        else:
            if (value := flush()) is not None:
                found.add(str(value))
            if re.fullmatch(r"\d+(?:\.\d+)?", token):
                found.add(token)
        index += 1
    if (value := flush()) is not None:
        found.add(str(value))
    return found


def unexpected_numbers(source: str, spoken: str) -> list[str]:
    """Significant numbers (decimals, or integers of 10 or more) stated in `spoken` that do not
    appear in `source`: a narration must describe a method, not invent or compute results."""
    allowed = set(_NUMBER.findall(re.sub(r"\\[A-Za-z]+", " ", source))) | spoken_numbers(source)
    allowed |= {part for number in list(allowed) for part in number.split(".")}
    return sorted(n for n in spoken_numbers(spoken) if ("." in n or int(n) >= 10) and n not in allowed)


_THREE_CAPS = re.compile(r"\b[A-Z]{3}\b")


def spell_angle_labels(text: str) -> str:
    """Three capital letters in a row are an angle or triangle label: BOE -> B O E, so a TTS voice
    reads letter names instead of trying to pronounce a word."""
    return _THREE_CAPS.sub(lambda m: " ".join(m.group(0)), text)
