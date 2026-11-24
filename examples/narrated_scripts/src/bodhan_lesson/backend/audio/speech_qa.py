"""Compare exact narration text to ASR evidence and verify bounded speech repairs."""

import re
import unicodedata
from collections.abc import Callable
from dataclasses import replace
from itertools import groupby
from typing import Literal

from ..models import (
    ChunkSpeechQA, NarrationScript, ScriptChunk, SectionSpeechQA, SpeechCandidate, SpeechComparison,
    SpeechEdit, SpeechQAAttempt, SpeechQAMetrics, SpokenNumber, TargetSpeech, TargetSpeechQA,
)
from .narration import split_speech_for_repair
from .spoken_numbers import alternate_number_words, normalize_digit_text
from .tamil_numbers import TAMIL_NUMBER_WORDS, normalize_tamil_numbers
from .transcribe import Transcription

Synthesize = Callable[[ScriptChunk, str, str], SpeechCandidate]
Transcribe = Callable[[SpeechCandidate, str], Transcription]
NUMBER = re.compile(r"[-+−]?\d+(?:[.,٫٬:/-]\d+)*")
SPEECH_QA_POLICY_VERSION = 5
ENGLISH_NUMBERS_IN_TAMIL = {
    "ஸீரோ": "0", "ஜீரோ": "0", "ஒன்": "1", "வன்": "1", "டூ": "2", "த்ரீ": "3", "ஃபோர்": "4",
    "ஃபைவ்": "5", "ஃபைவ்வில்": "5 இல்", "ஃபைவில்": "5 இல்", "சிக்ஸ்": "6", "ஸிக்ஸ்": "6",
    "செவன்": "7", "எய்ட்": "8", "நைன்": "9", "நைன்ட்டி": "90", "நைன்டி": "90",
    "ஒன் ஹண்ட்ரட் அண்ட் எய்ட்டி": "180", "ஒன் ஹண்ட்ரட் எய்ட்டி": "180",
    "ஒன் ஹண்ட்ரட் அண்ட் எயிட்டி": "180", "ஒன் ஹண்ட்ரட் எயிட்டி": "180",
    "த்ரீ ஹண்ட்ரட் அண்ட் சிக்ஸ்டி": "360", "த்ரீ ஹண்ட்ரட் சிக்ஸ்டி": "360",
}
ENGLISH_NUMBER_WORD = re.compile(
    r"(?<!\S)(?:"
    + "|".join(re.escape(term) for term in sorted(ENGLISH_NUMBERS_IN_TAMIL, key=len, reverse=True))
    + r"|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|"
      r"fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|"
      r"eighty|ninety|hundred|thousand|million|billion)(?!\S)"
)
TAMIL_SPOKEN_FORMS = {
    "சாப்டர்": "chapter", "அத்தியாயம்": "chapter", "fig": "figure", "ஃபிகர்": "figure",
    "படம்": "figure", "ஆங்கிள்": "angle", "கோணம்": "angle",
    "ஈக்குவல்ஸ்": "equals", "இக்வல்ஸ்": "equals", "சமம்": "equals",
    "டிகிரி": "degrees", "டிகிரீஸ்": "degrees", "பாகை": "degrees", "degree": "degrees",
    "டிகிரி க்குச்": "degrees க்கு", "டிகிரி க்கு": "degrees க்கு",
    "டிகிரிக்குச்": "degrees க்கு", "டிகிரிக்கு": "degrees க்கு",
    "நேர் கோணம்": "நேர்கோணம்",
    "பாயிண்ட்": "point", "பாயின்ட்": "point", "புள்ளி": "point",
    **ENGLISH_NUMBERS_IN_TAMIL,
}
TAMIL_FORM = re.compile(r"(?<!\S)(?:" + "|".join(
    re.escape(term) for term in sorted(TAMIL_SPOKEN_FORMS, key=len, reverse=True)
) + r")(?!\S)")
SPOKEN_DECIMAL = re.compile(r"(?<!\S)(\d+) point (\d+)(?!\S)")
TAMIL_LETTER = {
    "ஏ": "a", "பீ": "b", "பி": "b", "சீ": "c", "சி": "c", "டீ": "d", "டி": "d",
    "ஓ": "o", "வீ": "v", "வி": "v", "எஸ்": "s", "ஒய்": "y", "எக்ஸ்": "x",
}
TAMIL_LETTERS = "|".join(map(re.escape, sorted(TAMIL_LETTER, key=len, reverse=True)))
# Match the context words after the Tamil spoken-form substitutions.
GEOMETRY_CONTEXT = r"angle|கதிர்|point"
SPOKEN_GEOMETRY_LABEL = re.compile(
    rf"(?<!\S)({GEOMETRY_CONTEXT}) ((?:{TAMIL_LETTERS})(?: ?(?:{TAMIL_LETTERS})){{0,3}})(?!\S)"
)
GEOMETRY_LABEL = re.compile(rf"(?<!\S)({GEOMETRY_CONTEXT}) ([a-z]{{1,4}})(?!\S)")
SPOKEN_VARIABLE = re.compile(rf"(?<!\S)({TAMIL_LETTERS})(?= என்பது(?: |$))")
LATIN_LABEL = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2,4}(?![A-Za-z0-9])")
SPELLED_LATIN_LABEL = re.compile(r"(?<!\w)(?:[A-Z] ){1,3}[A-Z](?!\w)")
SPOKEN_ASIDE = re.compile(r"[ \t]+\(([^()\n]*[^()\s][^()\n]*)\)")
NEGATION = {
    "as": {"নহয়", "নাই", "নোৱাৰি"},
    "bn": {"না", "নয়", "নেই", "নি"},
    "en": {"no", "not", "never", "without"},
    "gu": {"નહીં", "નથી", "નહિ", "વગર"},
    "hi": {"न", "नहीं", "नही", "मत", "बिना"},
    "kn": {"ಇಲ್ಲ", "ಅಲ್ಲ", "ಬೇಡ"},
    "ml": {"ഇല്ല", "അല്ല", "വേണ്ട"},
    "mr": {"नाही", "नको", "नका", "विना"},
    "or": {"ନାହିଁ", "ନୁହେଁ", "ନା"},
    "pa": {"ਨਹੀਂ", "ਨਾ", "ਬਿਨਾਂ"},
    "ta": {"இல்லை", "அல்ல", "இன்றி", "வேண்டாம்", "கூடாது"},
    "te": {"లేదు", "కాదు", "వద్దు"},
    "ur": {"نہیں", "نہ", "مت", "بغیر"},
}


def _normalize_surface(text: str) -> str:
    characters = []
    for index, char in enumerate(text):
        if unicodedata.category(char).startswith("P"):
            left = text[index - 1] if index else ""
            right = text[index + 1:index + 2]
            if char in ".,٫٬:/-" and left.isdecimal() and right.isdecimal():
                characters.append(char)
            elif char in ".٫" and (not left or left.isspace() or left in "+-−") and right.isdecimal():
                characters.append(char)
            elif char == "-" and (
                right.isdecimal() or (right in {".", "٫"} and text[index + 2:index + 3].isdecimal())
            ):
                characters.append(char)
            elif char in "'’" and left.isalpha() and right.isalpha():
                characters.append("'")
        else:
            characters.append(char)
    return unicodedata.normalize("NFC", " ".join("".join(characters).casefold().split()))


def normalize_speech(text: str, language: str = "en") -> str:
    """Compare NFC case-folded text with single spaces and punctuation outside values removed."""
    if language == "ta":
        text = SPELLED_LATIN_LABEL.sub(lambda match: match[0].replace(" ", ""), text)
    normalized = _normalize_surface(text)
    if language == "ta":
        normalized = normalize_tamil_numbers(normalize_digit_text(normalized))
        normalized = TAMIL_FORM.sub(lambda match: TAMIL_SPOKEN_FORMS[match[0]], normalized)
        normalized = SPOKEN_DECIMAL.sub(r"\1.\2", normalized)
        normalized = SPOKEN_GEOMETRY_LABEL.sub(
            lambda match: match[1] + " " + "".join(TAMIL_LETTER[letter]
                                                  for letter in re.findall(TAMIL_LETTERS, match[2])),
            normalized,
        )
        normalized = SPOKEN_VARIABLE.sub(lambda match: TAMIL_LETTER[match[1]], normalized)
    return normalized


def _matching_prefix(first: str, second: str) -> int:
    return next((index for index, (left, right) in enumerate(zip(first, second)) if left != right),
                min(len(first), len(second)))


def _alignment(reference: str, transcript: str) -> tuple[SpeechEdit, ...]:
    # Preserve matched edges so a missing ending stays a single deletion even
    # when the last spoken character also occurs in the omitted phrase.
    prefix = _matching_prefix(reference, transcript)
    reference, transcript = reference[prefix:], transcript[prefix:]
    suffix = _matching_prefix(reference[::-1], transcript[::-1])
    if suffix:
        reference, transcript = reference[:-suffix], transcript[:-suffix]
    columns = len(transcript) + 1
    distance = [list(range(columns))]
    for row, expected in enumerate(reference, 1):
        previous = distance[-1]
        current = [row]
        for column, observed in enumerate(transcript, 1):
            current.append(min(previous[column] + 1, current[-1] + 1,
                               previous[column - 1] + (expected != observed)))
        distance.append(current)

    row, column = len(reference), len(transcript)
    steps = []
    while row or column:
        cost = distance[row][column]
        if row and column and reference[row - 1] == transcript[column - 1]:
            row, column = row - 1, column - 1
            operation = "match"
        elif row and column and cost == distance[row - 1][column - 1] + 1:
            row, column = row - 1, column - 1
            operation = "substitute"
        elif row and cost == distance[row - 1][column] + 1:
            row -= 1
            operation = "delete"
        else:
            column -= 1
            operation = "insert"
        steps.append((operation, row, column))

    edits = []
    for operation, grouped in groupby(reversed(steps), key=lambda step: step[0]):
        if operation == "match":
            continue
        span = list(grouped)
        _, start_ref, start_asr = span[0]
        width_ref = 0 if operation == "insert" else len(span)
        width_asr = 0 if operation == "delete" else len(span)
        edits.append(SpeechEdit(operation, prefix + start_ref, prefix + start_asr,
                                reference[start_ref:start_ref + width_ref], transcript[start_asr:start_asr + width_asr]))
    return tuple(edits)


def _omitted_words(reference: str, edit: SpeechEdit) -> bool:
    omitted = edit.reference_text
    start, end = edit.reference_start, edit.reference_start + len(omitted)
    return (any(char.isalnum() for char in omitted)
            and (not start or reference[start - 1].isspace() or omitted[0].isspace())
            and (end == len(reference) or reference[end].isspace() or omitted[-1].isspace()))


def _repeated_words(transcript: str, edit: SpeechEdit) -> bool:
    inserted = edit.transcript_text
    phrase = inserted.split()
    if not phrase or not any(char.isalnum() for char in inserted):
        return False
    start, end = edit.transcript_start, edit.transcript_start + len(inserted)
    before, after = transcript[:start], transcript[end:]
    left_boundary = not before or before[-1].isspace() or inserted[0].isspace()
    right_boundary = not after or after[0].isspace() or inserted[-1].isspace()
    return (left_boundary and right_boundary
            and (before.split()[-len(phrase):] == phrase or after.split()[:len(phrase)] == phrase))


def _negations(text: str, language: str) -> list[str]:
    words = NEGATION.get(language, ())
    return [word for word in text.split() if word in words or (language == "en" and word.endswith("n't"))]


def _extra_repeated_phrase(expected: str, observed: str) -> bool:
    """Catch multiword repetitions even when character edits also contain spelling changes."""
    words = observed.split()
    for width in range(3, min(12, len(words) // 2) + 1):
        for start in range(len(words) - 2 * width + 1):
            middle = start + width
            phrase = words[start:middle]
            if phrase == words[middle:middle + width]:
                doubled = " " + " ".join(phrase + phrase) + " "
                if doubled not in f" {expected} ":
                    return True
    return False


def _compare_number_forms(
    expected: str, observed: str, language: str, numbers: tuple[SpokenNumber, ...],
) -> tuple[str, str, bool]:
    aliases = {}
    for number in numbers:
        spoken = _normalize_surface(number.text)
        matches = tuple(re.finditer(rf"(?<!\S){re.escape(spoken)}(?!\S)(?: (?=(\S+)))?", expected))
        if not matches:
            continue
        aliases[spoken] = number.value
        for variation in alternate_number_words(number.value, language):
            alias = _normalize_surface(variation)
            if alias and not any(char.isdecimal() for char in alias):
                aliases.setdefault(alias, number.value)
        for match in matches:
            suffix = match[1]
            if suffix and not suffix[0].isascii() and suffix[0].isalpha():
                aliases.setdefault(f"{spoken}{suffix}", f"{number.value} {suffix}")
    if not aliases:
        return expected, observed, False

    pattern = "|".join(map(re.escape, sorted(aliases, key=len, reverse=True)))
    numeric_words = re.compile(rf"(?<!\S)(?:{pattern})(?!\S)")
    expected = numeric_words.sub(lambda match: aliases[match[0]], normalize_digit_text(expected))
    observed = numeric_words.sub(lambda match: aliases[match[0]], normalize_digit_text(observed))
    return expected, observed, True


def compare_speech(
    reference: str, transcript: str | None, language: str = "en",
    *, spoken_numbers: tuple[SpokenNumber, ...] = (),
) -> SpeechComparison:
    """Calculate aligned Unicode character edits and flag suspicious content changes."""
    expected = normalize_speech(reference, language)
    observed = None if transcript is None else normalize_speech(transcript, language)
    if not expected or observed is None:
        reason = "The normalized reference is empty." if not expected else "Transcription is unavailable."
        return SpeechComparison(reference, expected, transcript, observed, len(expected), reason=reason)

    if language == "ta":
        local_number_words = bool(TAMIL_NUMBER_WORDS.search(_normalize_surface(reference)))
    else:
        expected, observed, local_number_words = _compare_number_forms(expected, observed, language, spoken_numbers)

    edits = _alignment(expected, observed) if expected != observed else ()
    insertions = sum(len(edit.transcript_text) for edit in edits if edit.operation == "insert")
    deletions = sum(len(edit.reference_text) for edit in edits if edit.operation == "delete")
    substitutions = sum(len(edit.reference_text) for edit in edits if edit.operation == "substitute")
    total = insertions + deletions + substitutions
    signals = []
    if edits and (any(edit.operation == "insert" and _repeated_words(observed, edit) for edit in edits)
                  or _extra_repeated_phrase(expected, observed)):
        signals.append("repetition")
    if not observed or any(edit.operation == "delete" and _omitted_words(expected, edit) for edit in edits):
        signals.append("omission")
    if NUMBER.findall(expected) != NUMBER.findall(observed):
        signals.append("number_change")
    if language != "en" and local_number_words and transcript is not None:
        reference_surface = _normalize_surface(reference)
        observed_words = set(ENGLISH_NUMBER_WORD.findall(_normalize_surface(transcript)))
        if observed_words.difference(ENGLISH_NUMBER_WORD.findall(reference_surface)):
            signals.append("number_language_change")
    if language == "ta" and GEOMETRY_LABEL.findall(expected) != GEOMETRY_LABEL.findall(observed):
        signals.append("label_change")
    if _negations(expected, language) != _negations(observed, language):
        signals.append("negation_change")
    return SpeechComparison(reference, expected, transcript, observed, len(expected), insertions, deletions,
                            substitutions, total, total / len(expected), edits, tuple(signals))


def _review(
    chunk: ScriptChunk, candidate: SpeechCandidate, language: str, transcribe: Transcribe, threshold: float,
    phase: Literal["original", "regenerated", "split"],
) -> SpeechQAAttempt:
    if candidate.status == "ok" and candidate.attempt_id is not None and candidate.audio_path:
        transcript = transcribe(candidate, language)
    else:
        transcript = Transcription(None, reason=candidate.reason or "The speech candidate is unavailable.")
    return _judge(chunk.text, candidate, language, transcript, threshold, phase, chunk.spoken_numbers)


def _judge(
    text: str, candidate: SpeechCandidate, language: str, transcript: Transcription, threshold: float,
    phase: Literal["original", "regenerated", "split"],
    spoken_numbers: tuple[SpokenNumber, ...] = (),
) -> SpeechQAAttempt:
    comparison = compare_speech(text, transcript.text, language, spoken_numbers=spoken_numbers)
    reason = transcript.reason or comparison.reason
    if transcript.error_category == "duration_limit":
        outcome = "suspect"
    elif comparison.cer is None:
        outcome = "unavailable"
    elif comparison.signals or comparison.cer > threshold:
        outcome = "suspect"
        findings = list(comparison.signals)
        if comparison.cer > threshold:
            findings.append(f"CER {comparison.cer:.3f} exceeded {threshold:.3f}.")
        reason = ", ".join(findings)
    else:
        outcome = "passed"
    return SpeechQAAttempt(phase, candidate, comparison, transcript.attempt_ids, outcome, reason)


def _spoken_asides(chunk: ScriptChunk, language: str) -> ScriptChunk:
    if language != "ta" or chunk.policy == "equation_arithmetic":
        return chunk

    def rephrase(match: re.Match[str]) -> str:
        tail = chunk.text[match.end():].lstrip()
        close = "," if tail and tail[0] not in ".,;:!?" else ""
        return f", {match[1].strip()}{close}"

    return replace(chunk, text=SPOKEN_ASIDE.sub(rephrase, chunk.text))


def verify_chunk(
    chunk: ScriptChunk, candidate: SpeechCandidate, language: str, synthesize: Synthesize,
    transcribe: Transcribe, threshold: float, *, previous: ChunkSpeechQA | None = None,
) -> ChunkSpeechQA:
    """Try a full regeneration and at most one split, accepting only complete paths."""
    def finish(
        accepted: tuple[SpeechQAAttempt, ...], status: Literal["accepted", "exhausted", "unavailable"],
        reason: str | None = None,
    ) -> ChunkSpeechQA:
        ids = tuple(attempt.candidate.attempt_id for attempt in accepted if attempt.candidate.attempt_id is not None)
        return ChunkSpeechQA(chunk.id, original.comparison.reference_characters, tuple(attempts), ids, status, reason)

    if previous is not None and previous.attempts:
        attempts = [
            _judge(review.comparison.reference, review.candidate, language,
                   Transcription(review.comparison.transcript, review.transcribe_attempt_ids), threshold, review.phase,
                   chunk.spoken_numbers)
            if review.comparison.transcript is not None else review
            for review in previous.attempts
        ]
        original = attempts[0]
        for review in attempts:
            if review.phase != "split" and review.outcome == "passed":
                return finish((review,), "accepted")
        split = tuple(review for review in attempts if review.phase == "split")
        if len(split) == 2 and all(review.outcome == "passed" for review in split):
            return finish(split, "accepted")
        revised = _spoken_asides(chunk, language)
        if revised.text != chunk.text:
            retry = _review(revised, synthesize(revised, language, candidate.voice),
                            language, transcribe, threshold, "regenerated")
            attempts.append(retry)
            if retry.outcome == "passed":
                return finish((retry,), "accepted")
            return finish((), "exhausted", retry.reason or "The spoken aside needs another reading.")
        if language == "ta" and len(split) == 2 and sum(review.outcome == "passed" for review in split) == 1:
            bad = next(index for index, review in enumerate(split) if review.outcome != "passed")
            rejected = split[bad]
            reference = rejected.comparison.reference
            text = "ஏன்." if reference.strip() == "ஏன்?" else reference
            if "label_change" in rejected.comparison.signals:
                text = LATIN_LABEL.sub(lambda match: " ".join(match[0]), text)
            if text != reference:
                retry_chunk = replace(chunk, id=rejected.candidate.chunk_id, text=text)
                retry = _review(retry_chunk, synthesize(retry_chunk, language, candidate.voice),
                                language, transcribe, threshold, "split")
                attempts.append(retry)
                if retry.outcome == "passed":
                    recovered = tuple(retry if index == bad else review for index, review in enumerate(split))
                    return finish(recovered, "accepted")
        return finish((), "unavailable" if original.outcome == "unavailable" else "exhausted",
                      previous.reason or "Saved candidates did not pass the current speech QA checks.")

    original = _review(chunk, candidate, language, transcribe, threshold, "original")
    attempts = [original]
    if original.outcome == "passed":
        return finish((original,), "accepted")
    if original.outcome == "unavailable":
        return finish((), "unavailable", original.reason)

    voice = candidate.voice
    revised = _spoken_asides(chunk, language)
    regenerated = _review(revised, synthesize(revised, language, voice), language, transcribe, threshold, "regenerated")
    attempts.append(regenerated)
    if regenerated.outcome == "passed":
        return finish((regenerated,), "accepted")
    if regenerated.outcome == "unavailable" and regenerated.candidate.status == "ok":
        return finish((), "exhausted", f"Regeneration could not be verified: {regenerated.reason}")
    parts = split_speech_for_repair(revised)
    if not parts:
        return finish((), "exhausted", "Regeneration failed QA and the script has no safe split.")
    reviews = tuple(_review(part, synthesize(part, language, voice), language, transcribe, threshold, "split")
                    for part in parts)
    attempts.extend(reviews)
    rejected = [review.candidate.chunk_id for review in reviews if review.outcome != "passed"]
    if rejected:
        return finish((), "exhausted", f"Split parts did not pass QA: {', '.join(rejected)}.")
    return finish(reviews, "accepted")


def summarize_speech(chunks: tuple[ChunkSpeechQA, ...]) -> SpeechQAMetrics:
    """Weight CER by measured reference length and count coverage by original chunk text."""
    comparisons = [attempt.comparison for chunk in chunks for attempt in chunk.attempts
                   if attempt.candidate.attempt_id in chunk.accepted_candidate_ids]
    accepted = tuple(chunk for chunk in chunks if chunk.accepted_candidate_ids)
    measured = sum(comparison.reference_characters for comparison in comparisons)
    total = sum(chunk.reference_characters for chunk in chunks)
    covered = sum(chunk.reference_characters for chunk in accepted)
    if measured:
        edits = sum(comparison.total_edits or 0 for comparison in comparisons)
        cer, reason = edits / measured, None
    else:
        edits, cer = None, None
        reason = "No accepted reference characters." if total else "No reference characters to measure."
    return SpeechQAMetrics(len(chunks), len(accepted), total, covered, measured, edits, cer,
                           covered / total if total else None, reason)


def verify_script(
    script: NarrationScript, speech: TargetSpeech, synthesize: Synthesize, transcribe: Transcribe, threshold: float,
    *, previous: TargetSpeechQA | None = None,
) -> TargetSpeechQA:
    candidates = {candidate.chunk_id: candidate for candidate in speech.chunks}
    sections = []
    prior = {chunk.chunk_id: chunk for section in previous.sections for chunk in section.chunks} if previous else {}
    for section in script.sections:
        chunks = []
        for chunk in section.chunks:
            candidate = candidates.get(chunk.id)
            if candidate is None:
                candidate = SpeechCandidate(chunk.id, section.section_id, speech.voice, len(chunk.text), "failed",
                                            reason="No initial speech candidate was recorded.")
            old = prior.get(chunk.id)
            if old is not None and (not old.attempts or old.attempts[0].candidate != candidate
                                    or old.attempts[0].comparison.reference != chunk.text):
                old = None
            chunks.append(verify_chunk(chunk, candidate, script.language, synthesize, transcribe, threshold,
                                       previous=old))
        missed = [chunk.chunk_id for chunk in chunks if chunk.status != "accepted"]
        reason = section.reason
        if missed:
            reason = f"Section {section.section_id} has unverified chunks: {', '.join(missed)}."
        sections.append(SectionSpeechQA(section.section_id, tuple(chunks), summarize_speech(tuple(chunks)), reason))

    chunks = tuple(chunk for section in sections for chunk in section.chunks)
    metrics = summarize_speech(chunks)
    if speech.status == "failed" or not chunks or all(chunk.status == "unavailable" for chunk in chunks):
        status, reason = "failed", speech.reason or "No script chunks could be evaluated by speech QA."
    elif speech.status == "completed_with_misses" or any(section.reason for section in sections):
        status, reason = "completed_with_misses", "Some narration is unverified or unavailable."
    else:
        status, reason = "completed", None
    return TargetSpeechQA(script.language, threshold, status, tuple(sections), metrics, reason)
