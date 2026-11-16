"""Media decoding, measured signal checks, and aligned speech evidence."""

from __future__ import annotations

import io
import math
import re
import subprocess
import sys
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path

from jiwer import process_characters

from .schema import Finding, Span
from .text_checks import normalize_with_offsets

SAMPLE_RATE = 16_000
WINDOW = SAMPLE_RATE // 10
ASR_SECONDS = 28


class AudioError(ValueError):
    """A local audio input could not be measured or decoded."""


@dataclass
class Audio:
    """One decoded mono PCM signal with exact sample count."""

    samples: array

    @property
    def duration_ms(self) -> int:
        """Return the decoded duration rounded up to an integer millisecond."""
        return (len(self.samples) * 1000 + SAMPLE_RATE - 1) // SAMPLE_RATE


def decode_audio(path: Path) -> Audio:
    """Decode media for local checks and normalize the ASR wire format."""
    if not path.is_file():
        raise AudioError("Audio file does not exist.")
    command = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-protocol_whitelist",
        "file,pipe",
        "-i",
        str(path),
        "-map",
        "0:a:0",
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "-f",
        "s16le",
        "pipe:1",
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=90, check=False)
    except FileNotFoundError as error:
        raise AudioError("Install ffmpeg and make it available on PATH.") from error
    except subprocess.TimeoutExpired as error:
        raise AudioError("Audio decode timed out.") from error
    if result.returncode != 0 or not result.stdout:
        raise AudioError("Audio is empty or ffmpeg could not decode it.")
    samples = array("h")
    samples.frombytes(result.stdout)
    if sys.byteorder != "little":
        samples.byteswap()
    return Audio(samples)


def pcm_wav(samples: array) -> bytes:
    """Encode decoded 16 kHz samples as a standard mono PCM WAV."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
        if sys.byteorder != "little":
            samples = samples[:]
            samples.byteswap()
        output.writeframes(samples.tobytes())
    return buffer.getvalue()


def merge_wav(parts: list[bytes]) -> bytes:
    """Join TTS responses without re-encoding or changing their sample rate."""
    try:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as output:
            for index, data in enumerate(parts):
                with wave.open(io.BytesIO(data), "rb") as part:
                    signature = part.getnchannels(), part.getsampwidth(), part.getframerate()
                    if index == 0:
                        output.setparams((*signature, 0, "NONE", "not compressed"))
                        expected = signature
                    if signature != expected or part.getnframes() == 0:
                        raise AudioError("Indic-Speak returned incompatible or empty audio chunks.")
                    output.writeframes(part.readframes(part.getnframes()))
        return buffer.getvalue()
    except (wave.Error, EOFError) as error:
        raise AudioError("Indic-Speak returned unreadable audio.") from error


def speech_parts(text: str, limit: int = 280) -> list[str]:
    """Bound TTS input length while preferring punctuation and word boundaries."""
    sentences = re.split(r"(?<=[.!?।॥])\s+|\n+", text.strip())
    parts = []
    for sentence in sentences:
        while len(sentence) > limit:
            cut = sentence.rfind(" ", 0, limit + 1)
            cut = limit if cut < 1 else cut
            parts.append(sentence[:cut])
            sentence = sentence[cut:].lstrip()
        if sentence:
            parts.append(sentence)
    return parts


def chunks(audio: Audio, seconds: int = ASR_SECONDS):
    """Yield WAV chunks and their measured durations, cutting near lower energy."""
    size = seconds * SAMPLE_RATE
    start, length = 0, len(audio.samples)
    while start < length:
        end = min(length, start + size)
        if end < length:
            # Search only backwards so the chosen cut never exceeds the hosted limit.
            options = range(end - SAMPLE_RATE, end + 1, WINDOW)
            end = min(options, key=lambda cut: sum(abs(x) for x in audio.samples[cut - WINDOW : cut]))
        part = audio.samples[start:end]
        duration_ms = (len(part) * 1000 + SAMPLE_RATE - 1) // SAMPLE_RATE
        yield pcm_wav(part), duration_ms
        start = end


def signal_checks(audio: Audio) -> tuple[dict, list[Finding]]:
    """Measure quiet/clipped windows and localize long silence or sustained saturation."""
    samples = audio.samples
    quiet_runs, peak_runs = [], []
    quiet_start = peak_start = None
    quiet_samples = clipped_samples = 0
    for start in range(0, len(samples), WINDOW):
        frame = samples[start : start + WINDOW]
        quiet = sum(value * value for value in frame) <= len(frame) * 184**2  # Approx. -45 dBFS RMS.
        clipped = sum(value <= -32760 or value >= 32760 for value in frame)
        quiet_samples += len(frame) if quiet else 0
        clipped_samples += clipped
        if quiet and quiet_start is None:
            quiet_start = start
        if not quiet and quiet_start is not None:
            quiet_runs.append((quiet_start, start))
            quiet_start = None
        if clipped >= max(1, len(frame) * 0.03) and peak_start is None:
            peak_start = start
        if clipped < max(1, len(frame) * 0.03) and peak_start is not None:
            peak_runs.append((peak_start, start))
            peak_start = None
    if quiet_start is not None:
        quiet_runs.append((quiet_start, len(samples)))
    if peak_start is not None:
        peak_runs.append((peak_start, len(samples)))
    longest = max((end - start for start, end in quiet_runs), default=0)
    silence_ratio = quiet_samples / len(samples)
    clipping_ratio = clipped_samples / len(samples)
    metrics = {
        "duration_ms": audio.duration_ms,
        "silence_ratio": round(silence_ratio, 5),
        "clipping_ratio": round(clipping_ratio, 5),
        "longest_silence_ms": longest * 1000 // SAMPLE_RATE,
    }
    findings = []
    for start, end in quiet_runs:
        seconds = (end - start) / SAMPLE_RATE
        if seconds >= 1.4 or silence_ratio >= 0.90:
            strength = 0.99 if silence_ratio >= 0.90 or seconds >= 2.5 else 0.68
            findings.append(audio_finding(start, end, "silence", f"Quiet signal for {seconds:.2f} seconds.", strength))
    for start, end in peak_runs:
        if end - start >= WINDOW:
            findings.append(audio_finding(start, end, "clipping", "Sustained near-full-scale PCM samples.", 0.88))
    return metrics, findings


def audio_finding(start: int, end: int, check: str, evidence: str, strength: float) -> Finding:
    """Convert measured sample indices to a garbled-audio finding in milliseconds."""
    span = Span("audio_ms", start * 1000 // SAMPLE_RATE, math.ceil(end * 1000 / SAMPLE_RATE))
    return Finding("garbled_audio", "major", span, check, evidence, "Inspect the measured interval.", strength)


def speech_findings(expected: str, transcript: str, duration_ms: int) -> tuple[dict, list[Finding]]:
    """Align script and ASR text, using sustained edits as speech-quality evidence."""
    reference, reference_offsets = normalize_with_offsets(expected)
    actual, actual_offsets = normalize_with_offsets(transcript)
    if not reference:
        return {"alignment": "unavailable"}, []
    alignment = process_characters(reference, actual)
    findings = []
    character_rate = len(reference) * 1000 / max(duration_ms, 1)
    duration_risk = duration_ms < 450 or character_rate > 24
    min_length = max(6, min(16, math.ceil(len(reference) * 0.14)))
    for edit in alignment.alignments[0]:
        if edit.type == "delete" and edit.ref_end_idx - edit.ref_start_idx >= min_length:
            start, end = reference_offsets[edit.ref_start_idx][0], reference_offsets[edit.ref_end_idx - 1][1]
            suffix = edit.ref_end_idx >= len(reference) - 1
            # Character rate is only corroboration. Scripts and voices differ in pace.
            strength = 0.89 if suffix and duration_risk else 0.68
            findings.append(
                Finding(
                    "speech_omission",
                    "major",
                    Span("translation", start, end),
                    "transcript_alignment",
                    expected[start:end],
                    "A sustained script span is missing from the back-transcript.",
                    strength,
                )
            )
        elif edit.type == "insert" and edit.hyp_end_idx - edit.hyp_start_idx >= min_length:
            inserted = actual[edit.hyp_start_idx : edit.hyp_end_idx].strip()
            if len(inserted) >= min_length and inserted in reference and actual.count(inserted) > reference.count(inserted):
                start, end = actual_offsets[edit.hyp_start_idx][0], actual_offsets[edit.hyp_end_idx - 1][1]
                findings.append(
                    Finding(
                        "speech_repetition",
                        "major",
                        Span("transcript", start, end),
                        "transcript_alignment",
                        transcript[start:end],
                        "The back-transcript repeats a phrase more often than the script.",
                        0.84,
                    )
                )
    if alignment.cer >= 0.65 and actual and len(reference) >= 12:
        findings.append(
            Finding(
                "garbled_audio",
                "major",
                Span("transcript", 0, len(transcript)),
                "transcript_alignment",
                transcript,
                "High character error rate may reflect speech or recognizer difficulty.",
                0.61,
            )
        )
    return {
        "cer": round(alignment.cer, 5),
        "substitutions": alignment.substitutions,
        "deletions": alignment.deletions,
        "insertions": alignment.insertions,
        "script_characters_per_second": round(character_rate, 3),
        "short_for_script": duration_risk,
    }, findings
