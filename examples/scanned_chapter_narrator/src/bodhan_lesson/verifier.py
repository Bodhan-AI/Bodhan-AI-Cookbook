"""Three-dimensional audio quality assurance: Coverage, Repetition detection, and CER/WER."""

from __future__ import annotations

import re
from typing import Any

from bodhan_lesson.client import bodhan_client
from bodhan_lesson.schemas import QAReportSection


def levenshtein_distance(seq1: list[Any] | str, seq2: list[Any] | str) -> int:
    """Compute standard Levenshtein edit distance between two sequences (chars or words)."""
    m, n = len(seq1), len(seq2)
    if m == 0:
        return n
    if n == 0:
        return m

    # Use two-row space-optimized dynamic programming
    prev = list(range(n + 1))
    curr = [0] * (n + 1)

    for i in range(1, m + 1):
        curr[0] = i
        for j in range(1, n + 1):
            cost = 0 if seq1[i - 1] == seq2[j - 1] else 1
            curr[j] = min(
                prev[j] + 1,       # deletion
                curr[j - 1] + 1,   # insertion
                prev[j - 1] + cost  # substitution
            )
        prev, curr = curr, [0] * (n + 1)

    return prev[n]


def calculate_cer_and_wer(reference_text: str, hypothesis_text: str) -> tuple[float, float]:
    """Calculate Character Error Rate (CER) and Word Error Rate (WER) normalized [0.0, 1.0]."""
    ref_norm = re.sub(r"\s+", " ", reference_text).strip()
    hyp_norm = re.sub(r"\s+", " ", hypothesis_text).strip()

    if not ref_norm:
        return (0.0, 0.0) if not hyp_norm else (1.0, 1.0)

    # CER
    ref_chars = list(ref_norm)
    hyp_chars = list(hyp_norm)
    char_edits = levenshtein_distance(ref_chars, hyp_chars)
    cer = min(1.0, char_edits / max(1, len(ref_chars)))

    # WER
    ref_words = ref_norm.split()
    hyp_words = hyp_norm.split()
    word_edits = levenshtein_distance(ref_words, hyp_words)
    wer = min(1.0, word_edits / max(1, len(ref_words)))

    return round(cer, 4), round(wer, 4)


def detect_speech_repetitions(transcript: str, min_ngram_size: int = 3, max_repeats: int = 2) -> tuple[bool, list[str]]:
    """Detect runaway repetitive loops from TTS models while tolerating natural repetition."""
    words = transcript.strip().split()
    if len(words) < min_ngram_size * 2:
        return False, []

    repeated_phrases: list[str] = []
    # Check for identical consecutive 3-gram or 4-gram repeats
    for n in range(min_ngram_size, min(6, len(words) // 2 + 1)):
        for i in range(len(words) - 2 * n + 1):
            gram1 = words[i : i + n]
            gram2 = words[i + n : i + 2 * n]
            if gram1 == gram2:
                phrase = " ".join(gram1)
                # Filter out legitimate short math symbols/numbers repetition e.g. "x equals 2"
                if len(phrase) > 10 and phrase not in repeated_phrases:
                    repeated_phrases.append(phrase)

    has_repetition = len(repeated_phrases) >= max_repeats
    return has_repetition, repeated_phrases


def compute_coverage_score(reference: str, hypothesis: str) -> float:
    """Measure semantic coverage: proportion of reference key words found in transcription."""
    ref_words = set(re.findall(r"\w+", reference.lower()))
    if not ref_words:
        return 1.0

    hyp_words = set(re.findall(r"\w+", hypothesis.lower()))
    matched = ref_words.intersection(hyp_words)
    coverage = len(matched) / float(len(ref_words))
    return round(min(1.0, coverage), 4)


class AudioVerifier:
    """Performs 3D verification on synthesized audio sections via Indic-Transcribe."""

    def __init__(self) -> None:
        self.client = bodhan_client

    def verify_section_audio(
        self,
        audio_bytes: bytes,
        reference_script: str,
        section_id: str,
        language: str,
    ) -> QAReportSection:
        """Transcribe synthesized audio and compute coverage, repetition, and CER/WER."""
        # 1. Back-transcribe via Indic-Transcribe
        asr_result = self.client.transcribe_audio(audio_bytes=audio_bytes, language=language)

        # Check if Indic-Transcribe was skipped due to circuit breaker or service outage
        if (
            asr_result.get("status") == "skipped_due_to_service_unavailability"
            or asr_result.get("error") == "transcribe_circuit_broken"
        ):
            return QAReportSection(
                section_id=section_id,
                language=language,
                coverage_score=0.0,
                repetition_detected=False,
                repetition_phrases=[],
                cer=1.0,
                wer=1.0,
                passed=False,
                qa_status="skipped_due_to_service_unavailability",
                retry_count=0,
                failure_notes="QA verification skipped: Indic-Transcribe service unavailable (circuit open due to consecutive 502/503 errors).",
            )

        hypothesis_text = asr_result.get("text", "")

        # In mock mode, simulate successful transcription
        from bodhan_lesson.config import is_mock_mode
        if is_mock_mode() and hypothesis_text == "हम्म":
            hypothesis_text = reference_script

        # 2. Check 3 dimensions
        coverage = compute_coverage_score(reference_script, hypothesis_text)
        has_repetition, rep_phrases = detect_speech_repetitions(hypothesis_text)
        cer, wer = calculate_cer_and_wer(reference_script, hypothesis_text)

        # 3. Determine pass/fail thresholds
        passed = (coverage >= 0.65) and (not has_repetition) and (cer <= 0.45)
        qa_status = "passed" if passed else "failed"
        failure_notes = None
        if not passed:
            reasons = []
            if coverage < 0.65:
                reasons.append(f"Low coverage ({coverage:.2f} < 0.65)")
            if has_repetition:
                reasons.append(f"TTS repetition loop detected: {rep_phrases}")
            if cer > 0.45:
                reasons.append(f"High CER ({cer:.2f} > 0.45)")
            failure_notes = "; ".join(reasons)

        return QAReportSection(
            section_id=section_id,
            language=language,
            coverage_score=coverage,
            repetition_detected=has_repetition,
            repetition_phrases=rep_phrases,
            cer=cer,
            wer=wer,
            passed=passed,
            qa_status=qa_status,
            retry_count=0,
            failure_notes=failure_notes,
        )


audio_verifier = AudioVerifier()
