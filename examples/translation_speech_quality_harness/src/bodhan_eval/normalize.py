from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import re
import unicodedata


_DIGIT_BLOCKS = (
    0x0966,
    0x09E6,
    0x0A66,
    0x0AE6,
    0x0B66,
    0x0BE6,
    0x0C66,
    0x0CE6,
    0x0D66,
)
_ARABIC_DIGITS = (0x0660, 0x06F0)
_REMOVE_COMPARISON = {
    "\u200b",
    "\u200c",
    "\u200d",
    "\u2060",
    "\u00ad",
    "\ufeff",
    "\u061c",
    "\u200e",
    "\u200f",
}
_REMOVE_COMPARISON.update(chr(code) for code in range(0x202A, 0x202F))
_REMOVE_COMPARISON.update(chr(code) for code in range(0x2066, 0x206A))
_MALAYALAM_CHILLU = {
    "\u0d23": "\u0d7a",
    "\u0d28": "\u0d7b",
    "\u0d30": "\u0d7c",
    "\u0d32": "\u0d7d",
    "\u0d33": "\u0d7e",
    "\u0d15": "\u0d7f",
}
_DEVANAGARI_NUKTA = {
    ("\u0928", "\u093c"): "\u0929",
    ("\u0930", "\u093c"): "\u0931",
    ("\u0933", "\u093c"): "\u0934",
}
_NATIVE_DIGITS = {chr(start + offset): str(offset) for start in (*_DIGIT_BLOCKS, *_ARABIC_DIGITS) for offset in range(10)}


@dataclass(frozen=True)
class Normalized:
    text: str
    map: list[int]
    spans: list[tuple[int, int]] = field(default_factory=list)
    original_length: int = 0


@dataclass(frozen=True)
class NumberTok:
    value: Decimal
    span: tuple[int, int]
    raw: str


def _native_digit(char: str) -> str | None:
    return _NATIVE_DIGITS.get(char)


def _merge_span(left: tuple[str, int, int], right: tuple[str, int, int], char: str) -> tuple[str, int, int]:
    return (char, min(left[1], right[1]), max(left[2], right[2]))


def _prepare(text: str, lang: str) -> list[tuple[str, int, int]]:
    prepared: list[tuple[str, int, int]] = []
    index = 0
    while index < len(text):
        char = text[index]

        if lang == "ml" and index + 2 < len(text):
            chillu = _MALAYALAM_CHILLU.get(char)
            if chillu and text[index + 1:index + 3] == "\u0d4d\u200d":
                prepared.append((chillu, index, index + 3))
                index += 3
                continue

        if char in _REMOVE_COMPARISON:
            index += 1
            continue
        if char == "," and index > 0 and index + 1 < len(text):
            previous = text[index - 1]
            following = text[index + 1]
            if (previous.isdigit() or _native_digit(previous)) and (
                following.isdigit() or _native_digit(following)
            ):
                index += 1
                continue
        if lang == "ur" and (
            char == "\u0640"
            or "\u064b" <= char <= "\u065f"
            or char == "\u0670"
        ):
            index += 1
            continue

        if char == "\u00a0" or char == "\u2009":
            char = " "
        elif lang == "ur" and char == "\u064a":
            char = "\u06cc"
        elif lang == "ur" and char == "\u0643":
            char = "\u06a9"
        else:
            char = _native_digit(char) or char

        replacements = {
            "\u0964": ".",
            "\u0965": ".",
            "\u06d4": ".",
            "\u061f": "?",
            "\u060c": ",",
            "\u066b": ".",
            "\u066c": "",
            "\u2018": "'",
            "\u2019": "'",
            "\u201c": '"',
            "\u201d": '"',
            "\u2013": "-",
            "\u2014": "-",
        }
        replacement = replacements.get(char, char)
        if replacement:
            if lang in {"hi", "mr"} and char in {"\u0929", "\u0931", "\u0934"}:
                decomposed = {
                    "\u0929": "\u0928\u093c",
                    "\u0931": "\u0930\u093c",
                    "\u0934": "\u0933\u093c",
                }[char]
                prepared.extend((part, index, index + 1) for part in decomposed)
            else:
                prepared.extend(
                    (part, index, index + 1)
                    for part in unicodedata.normalize("NFD", replacement)
                )
        index += 1
    return prepared


def _canonical_order(chars: list[tuple[str, int, int]]) -> list[tuple[str, int, int]]:
    ordered: list[tuple[str, int, int]] = []
    segment: list[tuple[str, int, int]] = []
    for token in chars:
        if unicodedata.combining(token[0]) == 0 and segment:
            starter = segment[:1]
            marks = segment[1:]
            ordered.extend(starter + sorted(marks, key=lambda mark: unicodedata.combining(mark[0])))
            segment = []
        segment.append(token)
    if segment:
        starter = segment[:1] if unicodedata.combining(segment[0][0]) == 0 else []
        marks = segment[1:] if starter else segment
        ordered.extend(starter + sorted(marks, key=lambda mark: unicodedata.combining(mark[0])))
    return ordered


def _compose(chars: list[tuple[str, int, int]]) -> list[tuple[str, int, int]]:
    ordered = _canonical_order(chars)
    custom: list[tuple[str, int, int]] = []
    index = 0
    while index < len(ordered):
        if index + 1 < len(ordered):
            combined = _DEVANAGARI_NUKTA.get((ordered[index][0], ordered[index + 1][0]))
            if combined:
                custom.append(_merge_span(ordered[index], ordered[index + 1], combined))
                index += 2
                continue
        custom.append(ordered[index])
        index += 1

    composed: list[tuple[str, int, int]] = []
    starter_index: int | None = None
    last_combining = 0
    for token in custom:
        char = token[0]
        combining = unicodedata.combining(char)
        can_compose = starter_index is not None and (
            last_combining < combining or last_combining == 0
        )
        if can_compose:
            starter = composed[starter_index]
            candidate = unicodedata.normalize("NFC", starter[0] + char)
            if len(candidate) == 1:
                composed[starter_index] = _merge_span(starter, token, candidate)
                continue
        if combining == 0:
            starter_index = len(composed)
        composed.append(token)
        last_combining = combining
    return composed


def normalize(text: str, lang: str) -> Normalized:
    prepared = _compose(_prepare(text, lang))

    expanded: list[tuple[str, int, int]] = []
    for char, start, end in prepared:
        if char.isspace():
            char = " "
        elif "LATIN" in unicodedata.name(char, "") and "FULLWIDTH" not in unicodedata.name(char, ""):
            folded = char.casefold()
            expanded.extend((part, start, end) for part in folded)
            continue
        expanded.append((char, start, end))

    collapsed: list[tuple[str, int, int]] = []
    for token in expanded:
        if token[0] == " ":
            if not collapsed or collapsed[-1][0] == " ":
                if collapsed:
                    prior = collapsed[-1]
                    collapsed[-1] = (" ", prior[1], token[2])
                continue
        collapsed.append(token)
    if collapsed and collapsed[-1][0] == " ":
        collapsed.pop()

    return Normalized(
        text="".join(token[0] for token in collapsed),
        map=[token[1] for token in collapsed],
        spans=[(token[1], token[2]) for token in collapsed],
        original_length=len(text),
    )


_NUMBER_RE = re.compile(r"(?<![\w])(?:\d+(?:[,.]\d+)*(?:[.,]\d+)?)(?![\w])")


def extract_numbers(text: str, lang: str) -> list[NumberTok]:
    norm = normalize(text, lang)
    numbers: list[NumberTok] = []
    for match in _NUMBER_RE.finditer(norm.text):
        raw = match.group(0)
        compact = raw.replace(",", "")
        try:
            value = Decimal(compact)
        except InvalidOperation:
            continue
        start = norm.spans[match.start()][0] if norm.spans else norm.map[match.start()]
        end = norm.spans[match.end() - 1][1] if norm.spans else norm.map[match.end() - 1] + 1
        numbers.append(NumberTok(value=value, span=(start, end), raw=raw))
    return numbers
