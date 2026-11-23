import unicodedata
from collections import Counter
from difflib import SequenceMatcher

from bodhan_eval.lexicons import NUMBER_WORDS
from bodhan_eval.normalize import normalize_text

# Missing-content thresholds for TTS -> STT round trips.
MISSING_RATIO_FAIL = 0.25
TRUNCATION_TOKENS_FAIL = 2


def tokenize(text: str) -> list[str]:
    text = normalize_text(text).lower()
    tokens = []
    current = []

    for char in text:
        category = unicodedata.category(char)

        if category.startswith(("L", "N")):
            current.append(char)
        elif category.startswith("M") and current:
            current.append(char)
        else:
            if current:
                tokens.append("".join(current))
                current = []

    if current:
        tokens.append("".join(current))

    return tokens


def normalize_spoken_numbers(tokens: list[str], language: str) -> list[str]:
    """Normalize a small, explicit set of supported spoken-number forms."""

    aliases = {
        "hi": {
            ("ट्वेंटी", "फाइव"): "25",
            ("पच्चीस",): "25",
        },
        "bn": {
            ("ছয়",): "6",
            ("ছয়",): "6",
        },
        "ta": {
            ("ஆறு",): "6",
        },
    }

    language_aliases = dict(aliases.get(language, {}))

    # Single-word cardinal numbers from the shared lexicon ("दस" -> "10").
    for word, value in NUMBER_WORDS.get(language, {}).items():
        language_aliases.setdefault((word,), value)
    normalized = []
    i = 0

    while i < len(tokens):
        matched = False

        # Try longer aliases first.
        for phrase, number in sorted(
            language_aliases.items(),
            key=lambda item: len(item[0]),
            reverse=True,
        ):
            size = len(phrase)

            if tuple(tokens[i:i + size]) == phrase:
                normalized.append(number)
                i += size
                matched = True
                break

        if not matched:
            normalized.append(tokens[i])
            i += 1

    return normalized

def detect_repetitions(tokens: list[str]) -> list[str]:
    """Adjacent repeated tokens ("a a") and repeated bigrams ("a b a b")."""
    repeated = [
        current
        for previous, current in zip(tokens, tokens[1:], strict=False)
        if previous == current
    ]

    for index in range(len(tokens) - 3):
        first = tokens[index:index + 2]
        if first[0] != first[1] and first == tokens[index + 2:index + 4]:
            repeated.append(" ".join(first))

    return repeated


def evaluate_speech(  intended_text: str,
    transcribed_text: str,
    language: str = "",) -> dict:
    intended_tokens = tokenize(intended_text)
    transcribed_tokens = tokenize(transcribed_text)
    normalized_intended = normalize_spoken_numbers(
        intended_tokens, language
    )
    normalized_transcribed = normalize_spoken_numbers(
        transcribed_tokens, language
    )


    intended_counts = Counter(normalized_intended)
    transcribed_counts = Counter(normalized_transcribed)

    missing = list((intended_counts - transcribed_counts).elements())
    added = list((transcribed_counts - intended_counts).elements())

    # Repetition already present in the intended text ("बहुत बहुत") is not
    # a speech error.
    repetitions = list((
        Counter(detect_repetitions(normalized_transcribed))
        - Counter(detect_repetitions(normalized_intended))
    ).elements())

    exact_match = normalized_intended == normalized_transcribed
    if repetitions:
        status = "fail"
        severity = "medium"
    elif exact_match:
        status = "pass"
        severity = "none"
    else:
        status = "review"
        severity = "review"

    return {
        "category": "speech_text_comparison",
        "status": status,
        "passed": True if status == "pass" else (
            False if status == "fail" else None
        ),
        "severity": severity,
       "evidence": {
    "intended_tokens": intended_tokens,
    "transcribed_tokens": transcribed_tokens,
    "normalized_intended_tokens": normalized_intended,
    "normalized_transcribed_tokens": normalized_transcribed,
    "missing_tokens": missing,
    "added_tokens": added,
    "repetitions": repetitions,
              },
        "requires_semantic_review": status == "review",
    }



def _speech_check(category: str, status: str, severity: str, evidence: dict) -> dict:
    return {
        "category": category,
        "status": status,
        "passed": {"pass": True, "fail": False, "review": None}[status],
        "severity": "none" if status == "pass" else severity,
        "evidence": evidence,
    }


def evaluate_speech_coverage(
    intended_text: str,
    transcribed_text: str,
    language: str = "",
) -> list[dict]:
    """Missing spoken content and truncation, from an ordered token diff.

    Substitutions are *not* counted as missing content: they are usually
    pronunciation or spelling variants, and :func:`evaluate_speech` already
    surfaces them for review. Deleted tokens are counted, and a deletion
    that runs to the end of the intended text is reported as truncation.
    """
    intended = normalize_spoken_numbers(tokenize(intended_text), language)
    transcribed = normalize_spoken_numbers(tokenize(transcribed_text), language)

    if not intended:
        return []

    opcodes = SequenceMatcher(a=intended, b=transcribed, autojunk=False).get_opcodes()

    deleted_spans = [intended[i1:i2] for op, i1, i2, _, _ in opcodes if op == "delete"]
    deleted = sum(len(span) for span in deleted_spans)
    missing_ratio = deleted / len(intended)

    if not transcribed or missing_ratio >= MISSING_RATIO_FAIL or any(
        len(span) >= 2 for span in deleted_spans
    ):
        missing_status = "fail"
    elif deleted:
        missing_status = "review"
    else:
        missing_status = "pass"

    tail: list[str] = []
    if not transcribed:
        tail = intended
    elif opcodes[-1][0] == "delete" and opcodes[-1][2] == len(intended):
        tail = intended[opcodes[-1][1]:]

    if len(tail) >= TRUNCATION_TOKENS_FAIL or (tail and len(tail) / len(intended) >= 0.2):
        truncation_status = "fail"
    elif tail:
        truncation_status = "review"
    else:
        truncation_status = "pass"

    return [
        _speech_check("speech_missing_content", missing_status, "high", {
            "missing_spans": deleted_spans,
            "missing_ratio": round(missing_ratio, 3),
            "fail_ratio": MISSING_RATIO_FAIL,
        }),
        _speech_check("speech_truncation", truncation_status, "high", {
            "missing_tail": tail,
            "intended_token_count": len(intended),
            "transcribed_token_count": len(transcribed),
        }),
    ]
