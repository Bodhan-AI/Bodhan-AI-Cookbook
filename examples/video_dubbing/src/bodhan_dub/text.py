"""Source transcript assembly and deterministic checks for model decisions."""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, replace

_SENTENCE_BREAK = re.compile(r'(?<=[.!?।])\s+(?=[“"\'A-Z0-9])')

_SOFT_SENTENCE_BREAK = re.compile(
    r"(?<=\w)\s+(?=(?:A|An|The|This|That|These|Those|From|If|Let|Next|Now|"
    r"So|Then|Today|We|When|You)\s+[a-z])"
)
_NUMBER_WORDS = re.compile(
    r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|"
    r"forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion|"
    r"trillion|half|quarter|first|second|third|fourth|fifth|sixth|seventh|eighth|"
    r"ninth|tenth|dozen)\b",
    re.I,
)
_CLAUSE_BREAK = re.compile(r'(?<=[,;:—])\s+|\s+(?=(?:but|because|although|whereas|then)\b)', re.I)
_NUMBERS = re.compile(r"(?<!\w)[+−-]?\d+(?:[.,]\d+)*(?:\s?[%°])?")
_ACRONYMS = re.compile(r"\b[A-Z]{2,}[A-Z0-9]*\b")
_RELATIONS = re.compile(
    r"\b(?:no|not|never|cannot|less than|greater than|at least|at most|equal(?:s| to)?|"
    r"parallel|perpendicular|acute|obtuse|right angle|complementary|supplementary|"
    r"degree[s]?|centimetre[s]?|centimeter[s]?|kilometre[s]?|kilometer[s]?|"
    r"metre[s]?|meter[s]?|second[s]?|kilogram[s]?|percent)\b|[<>≤≥=×÷]",
    re.I,
)


@dataclass(frozen=True)
class Segment:
    id: int
    start: float
    end: float
    text: str
    timing: str
    windows: tuple[int, ...]
    speaker: str | None = None


def collapse(text: str) -> str:
    return " ".join(text.split())


def _token_key(text: str) -> str:
    return re.sub(r"[^\w]", "", text.casefold())


def _drop_overlap(previous: str, current: str) -> str:
    left, right = previous.split(), current.split()
    for count in range(min(len(left), len(right), 18), 2, -1):
        if [_token_key(word) for word in left[-count:]] == [_token_key(word) for word in right[:count]]:
            return " ".join(right[count:])
    return current


def window_segments(window, reply: dict, index: int) -> list[Segment]:
    text = collapse(reply.get("text", ""))
    candidates = []
    for part in reply.get("segments", []):
        part_text = collapse(part.get("text", ""))
        start, end = part.get("start"), part.get("end")
        if (
            part_text and isinstance(start, (float, int)) and isinstance(end, (float, int))
            and math.isfinite(start) and math.isfinite(end)
            and 0 <= start < end <= window.end - window.start + 0.075
        ):
            speaker = part.get("speaker")
            candidates.append(
                Segment(0, window.start + start, min(window.end, window.start + end),
                        part_text, "provider", (index,), speaker if isinstance(speaker, str) else None)
            )
    if candidates and (not text or _token_key(" ".join(item.text for item in candidates)) == _token_key(text)):
        return candidates
    if not text:
        return []
    speech = window.speech or ((window.start, window.end),)
    parts = source_parts(text)
    if len(parts) == 1:
        return [Segment(0, speech[0][0], speech[-1][1], text, "approximate", (index,))]
    active = sum(end - start for start, end in speech)
    weights = [max(1, len(p)) for p in parts]

    def point(fraction, *, entering):
        remaining = active * fraction
        for start, end in speech:
            if remaining < end - start or (remaining == end - start and not entering):
                return start + remaining
            remaining -= end - start
        return speech[-1][1]

    consumed = 0
    total = sum(weights)
    for part, weight in zip(parts, weights, strict=True):
        start = point(consumed / total, entering=True)
        consumed += weight
        end = point(consumed / total, entering=False)
        candidates.append(Segment(0, start, end, part, "approximate", (index,)))
    return candidates


def source_parts(text: str) -> list[str]:
    result = []
    for sentence in _SENTENCE_BREAK.split(text):
        if len(sentence) <= 210:
            result.append(sentence)
            continue
        soft_parts = _SOFT_SENTENCE_BREAK.split(sentence)
        if len(soft_parts) > 1:
            current = ""
            for part in soft_parts:
                if current and len(current) + len(part) + 1 > 205:
                    result.extend(_wrap_fragment(current))
                    current = ""
                current = f"{current} {part}".strip()
            if result and len(current) < 42 and len(result[-1]) + len(current) <= 240:
                result[-1] = f"{result[-1]} {current}"
            elif current:
                result.extend(_wrap_fragment(current))
        else:
            result.extend(_wrap_fragment(sentence))
    return result


def _wrap_fragment(text: str) -> list[str]:
    if len(text) <= 215:
        return [text]
    result = []
    words = text.split()
    # Keep the final unit substantial when a provider omitted punctuation.
    limit = max(100, math.ceil(len(text) / math.ceil(len(text) / 205)) + 12)
    current = ""
    for word in words:
        if current and len(current) + 1 + len(word) > limit:
            boundary = max(current.rfind(","), current.rfind(";"), current.rfind(":"))
            if boundary >= 85:
                result.append(current[:boundary + 1])
                current = current[boundary + 1:].strip()
            else:
                result.append(current)
                current = ""
        current = f"{current} {word}".strip()
    if result and len(current) < 40 and len(result[-1]) + len(current) <= 240:
        result[-1] = f"{result[-1]} {current}"
    elif current:
        result.append(current)
    return result


def assemble(parts: list[Segment], llm_merges: set[int] | None = None) -> list[Segment]:
    result: list[Segment] = []
    for index, current in enumerate(sorted(parts, key=lambda item: (item.start, item.end))):
        if result:
            prior = result[-1]
            different_window = prior.windows[-1] != current.windows[0]
            if different_window and current.start < prior.end + 0.06:
                remaining = _drop_overlap(prior.text, current.text)
                if not remaining:
                    result[-1] = replace(prior, end=max(prior.end, current.end), windows=prior.windows + current.windows)
                    continue
                current = replace(current, text=remaining)
            if current.start < prior.end:
                if current.end <= prior.end:
                    # Provider segments can overlap or nest. Preserve both texts
                    # in a single slot and drop the unreliable speaker labels.
                    result[-1] = replace(
                        prior, text=f"{prior.text} {current.text}", speaker=None,
                        timing="approximate", windows=tuple(dict.fromkeys(prior.windows + current.windows)),
                    )
                    continue
                boundary = max(prior.start, (prior.end + current.start) / 2)
                result[-1] = prior = replace(prior, end=boundary, timing="approximate", speaker=None)
                current = replace(current, start=boundary, timing="approximate", speaker=None)
            gap = current.start - prior.end
            unfinished = not re.search(r'[.!?।]["”\')\]]?$', prior.text)
            if (
                different_window and gap <= 0.45
                and (unfinished or index in (llm_merges or set()))
                and current.end - prior.start <= 26
                and len(prior.text) + len(current.text) <= 225
                and prior.speaker == current.speaker
            ):
                result[-1] = replace(
                    prior, text=f"{prior.text} {current.text}", end=current.end,
                    windows=tuple(dict.fromkeys(prior.windows + current.windows)),
                    timing="provider" if prior.timing == current.timing == "provider" else "approximate",
                )
                continue
        result.append(current)
    return [replace(item, id=index) for index, item in enumerate(result, 1)]


def normalize(text: str) -> str:
    result = []
    for char in unicodedata.normalize("NFKC", text).casefold():
        if char.isdecimal():
            result.append(str(unicodedata.decimal(char)))
        elif unicodedata.category(char)[0] in "LMN":
            result.append(char)
    return "".join(result)


def cer(reference: str, observed: str) -> float:
    previous = list(range(len(observed) + 1))
    for row, expected in enumerate(reference, 1):
        current = [row]
        for col, actual in enumerate(observed, 1):
            current.append(min(current[-1] + 1, previous[col] + 1, previous[col - 1] + (expected != actual)))
        previous = current
    return previous[-1] / max(len(reference), 1)


def quality(reference: str, observed: str) -> dict:
    expected, actual = normalize(reference), normalize(observed)
    error = cer(expected, actual)
    words = [token for word in observed.split() if (token := normalize(word))]
    ref_words = [token for word in reference.split() if (token := normalize(word))]
    repeats = []
    for width in range(1, min(6, len(words) // 2) + 1):
        for start in range(len(words) - 2 * width + 1):
            phrase = words[start:start + width]
            if phrase == words[start + width:start + 2 * width] and not any(
                ref_words[pos:pos + 2 * width] == phrase * 2 for pos in range(len(ref_words))
            ):
                repeats.append(" ".join(phrase))
    ratio = len(actual) / max(len(expected), 1)
    flags = []
    if not actual:
        flags.append("empty_verification")
    if repeats and (error > 0.12 or ratio > 1.1):
        flags.append("unexpected_repetition")
    if ratio < 0.7 and error > 0.24:
        flags.append("possible_omission")
    if error > 0.38:
        flags.append("high_cer")
    return {
        "passed": not flags, "cer": round(error, 4), "flags": flags,
        "reference": reference, "back_transcript": observed, "repeated_phrases": list(dict.fromkeys(repeats)),
        "normalized_length_ratio": round(ratio, 4),
    }


def source_term(term: str, source: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", source, re.I))


def numbers(text: str) -> Counter:
    result = Counter()
    for match in _NUMBERS.finditer(text):
        number = "".join(
            str(unicodedata.decimal(char)) if char.isdecimal() else char.replace("−", "-")
            for char in match.group()
        )
        result["".join(number.split()).replace(",", "")] += 1
    return result


def translation_flags(source: str, target: str, glossary: dict[str, str]) -> list[str]:
    flags = []
    if numbers(source) != numbers(target):
        flags.append("numeric_forms_differ")
    if any(not source_term(acronym, target) for acronym in _ACRONYMS.findall(source)):
        flags.append("acronym_form_differs")
    flags.extend(f"glossary_rendering_differs:{term}" for term, rendering in glossary.items()
                 if normalize(rendering) not in normalize(target))
    return flags


def safe_condensation(original: str, revised: str, terms: list[str]) -> bool:
    if not revised or len(revised) >= len(original) * 0.9:
        return False
    if numbers(original) != numbers(revised) or _ACRONYMS.findall(original) != _ACRONYMS.findall(revised):
        return False
    if _NUMBER_WORDS.findall(original.casefold()) != _NUMBER_WORDS.findall(revised.casefold()):
        return False
    if Counter(m.casefold() for m in _RELATIONS.findall(original)) != Counter(m.casefold() for m in _RELATIONS.findall(revised)):
        return False
    names = re.findall(r"\b[A-Z](?:[a-z]+|[A-Z0-9]*)\b", original)
    protected = terms + [name for name in names if name not in {"A", "I", "The", "This", "We", "Let", "Now"}]
    return all(source_term(term, revised) for term in protected if source_term(term, original))


def clause_split(source: str) -> list[str]:
    chunks = [chunk.strip() for chunk in _CLAUSE_BREAK.split(source) if chunk.strip()]
    if len(chunks) < 2:
        return []
    index = min(range(1, len(chunks)), key=lambda i: abs(len(" ".join(chunks[:i])) - len(source) / 2))
    return [" ".join(chunks[:index]), " ".join(chunks[index:])]
