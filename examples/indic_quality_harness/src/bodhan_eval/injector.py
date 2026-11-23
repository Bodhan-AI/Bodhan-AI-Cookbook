
import random
import re
import unicodedata


def find_token_spans(text: str) -> list[tuple[int, int]]:
    """Find Unicode-aware word spans, including Indic combining marks."""
    spans = []
    start = None

    for index, char in enumerate(text):
        category = unicodedata.category(char)
        is_word_char = (
            category.startswith(("L", "N"))
            or (category.startswith("M") and start is not None)
        )

        if is_word_char and start is None:
            start = index
        elif not is_word_char and start is not None:
            spans.append((start, index))
            start = None

    if start is not None:
        spans.append((start, len(text)))

    return spans


def inject_number_error(text: str) -> dict | None:
    """Replace one numeric value with a different value."""
    matches = list(re.finditer(r"\d+(?:[.,]\d+)?", text))

    if not matches:
        return None

    match = matches[0]
    original = match.group()
    replacement = "999" if original != "999" else "888"

    mutated = text[:match.start()] + replacement + text[match.end():]

    return {
        "text": mutated,
        "category": "number_mismatch",
        "span": [match.start(), match.start() + len(replacement)],
        "original": original,
        "replacement": replacement,
    }


def inject_omission(text: str, seed: int = 0) -> dict | None:
    """Remove one complete token without rewriting the remaining text."""
    spans = find_token_spans(text)

    if not spans:
        return None

    start, end = random.Random(seed).choice(spans)
    removed = text[start:end]
    mutated = text[:start] + text[end:]

    return {
        "text": mutated,
        "category": "omission",
        "span": [start, start],
        "original": removed,
        "replacement": "",
    }


def inject_repetition(text: str, seed: int = 0) -> dict | None:
    """Duplicate one token programmatically."""
    spans = find_token_spans(text)

    if not spans:
        return None

    start, end = random.Random(seed).choice(spans)
    token = text[start:end]
    mutated = text[:end] + " " + token + text[end:]

    return {
        "text": mutated,
        "category": "repetition",
        "span": [end + 1, end + 1 + len(token)],
        "original": "",
        "replacement": token,
    }


def inject_formatting_error(text: str) -> dict | None:
    """Remove a pair of Markdown/LaTeX formatting markers."""
    markers = ["**", "__", "```", "$$", "`", "$"]

    for marker in markers:
        if text.count(marker) >= 2:
            first = text.find(marker)
            second = text.find(marker, first + len(marker))

            mutated = (
                text[:first]
                + text[first + len(marker):second]
                + text[second + len(marker):]
            )

            return {
                "text": mutated,
                "category": "formatting_mismatch",
                "span": [first, first],
                "original": marker,
                "replacement": "",
            }

    return None


def inject_error(
    text: str,
    category: str,
    seed: int = 0,
) -> dict | None:
    """Inject one labelled error into a translated text."""
    injectors = {
        "number_mismatch": inject_number_error,
        "omission": lambda value: inject_omission(value, seed),
        "repetition": lambda value: inject_repetition(value, seed),
        "formatting_mismatch": inject_formatting_error,
    }

    injector = injectors.get(category)

    if injector is None:
        raise ValueError(f"Unsupported error category: {category}")

    result = injector(text)

    if result is None:
        return None

    return {
        "original_text": text,
        "mutated_text": result["text"],
        "injected_category": result["category"],
        "span": result["span"],
        "original": result["original"],
        "replacement": result["replacement"],
        "seed": seed,
    }


# ---------------------------------------------------------------------------
# Additional labelled error types. All injections are deterministic for a
# given (text, language, seed) and change exactly one span.
# ---------------------------------------------------------------------------

from bodhan_eval.lexicons import PLACES, UNITS, find_concept_spans  # noqa: E402

# A meaning-bearing word to insert ("maybe"): an addition, not a repetition.
ADDITION_WORDS = {"hi": "शायद", "bn": "হয়তো", "ta": "ஒருவேளை", "en": "maybe"}

# Antonym pairs used for mistranslation; checked in both directions.
ANTONYMS = {
    "hi": [("पहले", "बाद"), ("अधिक", "कम"), ("ज़्यादा", "कम"), ("बंद", "खुला"),
           ("शुरू", "खत्म"), ("सुबह", "शाम"), ("नया", "पुराना"), ("नई", "पुरानी")],
    "bn": [("আগে", "পরে"), ("বেশি", "কম"), ("বন্ধ", "খোলা"), ("শুরু", "শেষ"),
           ("সকাল", "সন্ধ্যা"), ("নতুন", "পুরানো")],
    "ta": [("முன்", "பின்"), ("அதிக", "குறைந்த"), ("மூட", "திற"),
           ("காலை", "மாலை"), ("புதிய", "பழைய")],
    "en": [("before", "after"), ("more", "less"), ("open", "close"),
           ("start", "end"), ("new", "old")],
}
# Fallback replacement word ("tree") when no antonym applies.
DISTRACTOR_WORDS = {"hi": "पेड़", "bn": "গাছ", "ta": "மரம்", "en": "tree"}

LATIN_ENTITY_SWAPS = {
    "Android": "Windows", "iOS": "Linux", "PDF": "DOCX", "Python": "Java",
    "UPI": "NEFT", "Bluetooth": "Wi-Fi", "PostgreSQL": "MySQL", "Excel": "Word",
}


def _labelled(text, mutated, category, start, replacement, original):
    return {
        "text": mutated,
        "category": category,
        "span": [start, start + len(replacement)],
        "original": original,
        "replacement": replacement,
    }


def inject_addition(text: str, language: str = "hi", seed: int = 0) -> dict | None:
    """Insert one meaning-bearing word before a random token."""
    spans = find_token_spans(text)
    if not spans:
        return None

    word = ADDITION_WORDS.get(language, ADDITION_WORDS["en"])
    start, _ = random.Random(seed).choice(spans)
    mutated = text[:start] + word + " " + text[start:]
    return _labelled(text, mutated, "addition", start, word, "")


def inject_mistranslation(text: str, language: str = "hi", seed: int = 0) -> dict | None:
    """Swap a word for its antonym, or a content word for an unrelated word."""
    for left, right in ANTONYMS.get(language, []):
        for old, new in ((left, right), (right, left)):
            index = text.find(old)
            if index != -1:
                mutated = text[:index] + new + text[index + len(old):]
                return _labelled(text, mutated, "mistranslation", index, new, old)

    # Units and places belong to other categories, so never replace them.
    protected = [
        (s, e) for s, e, _ in
        find_concept_spans(text, UNITS, language) + find_concept_spans(text, PLACES, language)
    ]
    candidates = [
        (s, e) for s, e in find_token_spans(text)
        if not text[s:e].isdigit() and e - s >= 3
        and not re.search(r"[A-Za-z]", text[s:e])
        and not any(s < pe and ps < e for ps, pe in protected)
    ]
    if not candidates:
        return None

    start, end = random.Random(seed).choice(candidates)
    word = DISTRACTOR_WORDS.get(language, DISTRACTOR_WORDS["en"])
    mutated = text[:start] + word + text[end:]
    return _labelled(text, mutated, "mistranslation", start, word, text[start:end])


def _swap_concept(text, concepts, language, category):
    for start, end, concept_id in find_concept_spans(text, concepts, language):
        group = concepts[concept_id]["group"]
        surface = text[start:end]
        # Prefer an alias in the same script as the matched surface form.
        lang = "en" if re.search(r"[A-Za-z]", surface) else language
        for other_id, spec in concepts.items():
            if other_id == concept_id or spec["group"] != group or not spec.get(lang):
                continue
            replacement = spec[lang][0]
            mutated = text[:start] + replacement + text[end:]
            return _labelled(text, mutated, category, start, replacement, surface)
    return None


def inject_unit_error(text: str, language: str = "hi", seed: int = 0) -> dict | None:
    """Replace a unit with another unit of the same dimension (km -> metre)."""
    return _swap_concept(text, UNITS, language, "unit_mismatch")


def inject_entity_error(text: str, language: str = "hi", seed: int = 0) -> dict | None:
    """Change an e-mail domain, a Latin-script name or a known place."""
    email = re.search(r"([\w.+-]+@)([\w-]+)((?:\.[\w-]+)+)", text)
    if email:
        replacement = email.group(1) + "examp1e" + email.group(3)
        mutated = text[:email.start()] + replacement + text[email.end():]
        return _labelled(text, mutated, "entity_mismatch", email.start(), replacement, email.group())

    for name, swap in LATIN_ENTITY_SWAPS.items():
        match = re.search(rf"(?<![A-Za-z]){re.escape(name)}(?![A-Za-z])", text)
        if match:
            mutated = text[:match.start()] + swap + text[match.end():]
            return _labelled(text, mutated, "entity_mismatch", match.start(), swap, name)

    return _swap_concept(text, PLACES, language, "entity_mismatch")


def inject_link_error(text: str) -> dict | None:
    """Drop a Markdown link target: "[label](url)" -> "label"."""
    match = re.search(r"\[([^\]]+)\]\(([^)]+)\)", text)
    if not match:
        return None
    label = match.group(1)
    mutated = text[:match.start()] + label + text[match.end():]
    return _labelled(text, mutated, "formatting_mismatch", match.start(), label, match.group())


SUPPORTED_CATEGORIES = [
    "number_mismatch",
    "unit_mismatch",
    "omission",
    "addition",
    "repetition",
    "mistranslation",
    "entity_mismatch",
    "formatting_mismatch",
]


def inject_labelled_error(
    text: str,
    category: str,
    language: str = "hi",
    seed: int = 0,
) -> dict | None:
    """Like :func:`inject_error`, for every supported category."""
    language_aware = {
        "addition": inject_addition,
        "mistranslation": inject_mistranslation,
        "unit_mismatch": inject_unit_error,
        "entity_mismatch": inject_entity_error,
    }

    if category in language_aware:
        result = language_aware[category](text, language, seed)
    elif category == "formatting_mismatch":
        result = inject_formatting_error(text) or inject_link_error(text)
    else:
        return inject_error(text, category, seed)

    if result is None:
        return None

    return {
        "original_text": text,
        "mutated_text": result["text"],
        "injected_category": result["category"],
        "span": result["span"],
        "original": result["original"],
        "replacement": result["replacement"],
        "seed": seed,
    }
