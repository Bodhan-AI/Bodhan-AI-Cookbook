"""Media decoding, measured signal checks, and aligned speech evidence."""

from __future__ import annotations

import io
import math
import re
import subprocess
import sys
import unicodedata
import wave
from array import array
from collections import Counter
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
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


# Signal-level constants. Each one is a convention, not a value fitted to recordings.
FULL_SCALE = 32768
# ffmpeg's silencedetect uses -60 dB as its default noise tolerance. Below it, a
# 16-bit frame carries no usable speech, whatever the recording level.
SILENCE_FLOOR_DBFS = -60.0
# ITU-T P.56 counts speech as active down to 15.9 dB below the active speech
# level. The loud-frame reference sits a few dB above that level, so a 25 dB
# margin calls a frame quiet only when it is clearly outside speech.
QUIET_MARGIN_DB = 25.0
# The 95th percentile of 100 ms frame levels follows the voice, not a single click.
LOUD_FRAME_PERCENTILE = 0.95
LONG_GAP_SECONDS = 1.4  # Longer than an ordinary sentence pause. A weak cue on its own.
STRONG_GAP_SECONDS = 2.5  # Well beyond any natural pause inside one utterance.
EDGE_SHARE = 0.4  # Edge silence matters only when it rivals the speech it pads.
MOSTLY_SILENT_RATIO = 0.90
# Normalized script characters per second beyond which speech is implausibly fast.
# speech_findings already uses this pace bound for its duration check.
MAX_CHARACTERS_PER_SECOND = 24
# Broadband noise crosses zero on about half of all samples. Voiced speech stays well
# below 0.15, and fricatives that reach this rate last a few hundred milliseconds.
NOISE_ZERO_CROSSINGS = 0.35
WEAK_NOISE_SECONDS = 1.0  # Several times longer than any fricative.
STRONG_NOISE_SECONDS = 1.5


def level_dbfs(rms: float) -> float:
    """Express a frame RMS in dB relative to 16-bit full scale, bounded at one LSB."""
    return 20 * math.log10(max(rms, 1.0) / FULL_SCALE)


def frame_levels(samples: array) -> list[float]:
    """Return the RMS of each 100 ms frame, including a shorter final frame."""
    levels = []
    for start in range(0, len(samples), WINDOW):
        frame = samples[start : start + WINDOW]
        levels.append(math.sqrt(sum(value * value for value in frame) / len(frame)))
    return levels


def quiet_threshold(levels: list[float]) -> tuple[float, float]:
    """Return ``(threshold_rms, loud_rms)``: a quiet level set by the clip's own loud frames, with an absolute floor.

    A voice that is quiet throughout but continuous keeps its frames above a
    threshold relative to its own level. Digital silence stays below the floor
    however quiet the rest of the clip is.
    """
    floor = FULL_SCALE * 10 ** (SILENCE_FLOOR_DBFS / 20)
    ordered = sorted(levels)
    loud = ordered[min(len(ordered) - 1, int(LOUD_FRAME_PERCENTILE * len(ordered)))] if ordered else 0.0
    return max(floor, loud * 10 ** (-QUIET_MARGIN_DB / 20)), loud


def signal_checks(audio: Audio) -> tuple[dict, list[Finding]]:
    """Measure quiet/clipped windows and localize long silence or sustained saturation.

    Quiet frames are judged against the clip's own loud-frame level (see
    ``quiet_threshold``). Internal quiet runs are flagged by length. Leading or
    trailing runs count as normal TTS padding unless they are long and a large
    share of the clip. A clip that is quiet almost throughout gets one
    whole-clip finding instead of one finding per run.
    """
    samples = audio.samples
    if not samples:
        raise AudioError("Audio is empty.")
    levels = frame_levels(samples)
    threshold, loud = quiet_threshold(levels)
    floor = FULL_SCALE * 10 ** (SILENCE_FLOOR_DBFS / 20)
    quiet_runs, peak_runs = [], []
    quiet_start = peak_start = None
    quiet_samples = clipped_samples = 0
    run_level = 0.0
    noise_runs = []
    noise_start = None
    for index, start in enumerate(range(0, len(samples), WINDOW)):
        frame = samples[start : start + WINDOW]
        quiet = levels[index] <= threshold
        crossings = sum((frame[k] >= 0) != (frame[k - 1] >= 0) for k in range(1, len(frame))) / max(1, len(frame) - 1)
        noisy = not quiet and crossings >= NOISE_ZERO_CROSSINGS
        if noisy and noise_start is None:
            noise_start = start
        if not noisy and noise_start is not None:
            noise_runs.append((noise_start, start))
            noise_start = None
        clipped = sum(value <= -32760 or value >= 32760 for value in frame)
        quiet_samples += len(frame) if quiet else 0
        clipped_samples += clipped
        if quiet and quiet_start is None:
            quiet_start, run_level = start, 0.0
        if quiet:
            run_level = max(run_level, levels[index])
        if not quiet and quiet_start is not None:
            quiet_runs.append((quiet_start, start, run_level))
            quiet_start = None
        if clipped >= max(1, len(frame) * 0.03) and peak_start is None:
            peak_start = start
        if clipped < max(1, len(frame) * 0.03) and peak_start is not None:
            peak_runs.append((peak_start, start))
            peak_start = None
    if quiet_start is not None:
        quiet_runs.append((quiet_start, len(samples), run_level))
    if peak_start is not None:
        peak_runs.append((peak_start, len(samples)))
    if noise_start is not None:
        noise_runs.append((noise_start, len(samples)))
    longest = max((end - start for start, end, _ in quiet_runs), default=0)
    silence_ratio = quiet_samples / len(samples)
    clipping_ratio = clipped_samples / len(samples)
    metrics = {
        "duration_ms": audio.duration_ms,
        "silence_ratio": round(silence_ratio, 5),
        "clipping_ratio": round(clipping_ratio, 5),
        "longest_silence_ms": longest * 1000 // SAMPLE_RATE,
        "loud_frame_dbfs": round(level_dbfs(loud), 1),
        "quiet_threshold_dbfs": round(level_dbfs(threshold), 1),
    }
    findings = []
    total_seconds = len(samples) / SAMPLE_RATE
    if silence_ratio >= MOSTLY_SILENT_RATIO or loud <= floor:
        evidence = f"Signal is quiet for {silence_ratio:.0%} of the {total_seconds:.2f} s clip (loud frames reach only {level_dbfs(loud):.0f} dBFS)."
        findings.append(audio_finding(0, len(samples), "silence", evidence, 0.99))
    else:
        for start, end, level in quiet_runs:
            seconds = (end - start) / SAMPLE_RATE
            if start == 0 or end == len(samples):
                # TTS pads both ends. Only padding that rivals the speech is a defect.
                if seconds < STRONG_GAP_SECONDS or seconds < EDGE_SHARE * total_seconds:
                    continue
                strength, where = 0.84, "Leading" if start == 0 else "Trailing"
            else:
                if seconds < LONG_GAP_SECONDS:
                    continue
                strength, where = (0.99 if seconds >= STRONG_GAP_SECONDS else 0.68), "Internal"
            evidence = (
                f"{where} quiet signal for {seconds:.2f} s, from {start / SAMPLE_RATE:.2f} s "
                f"to {end / SAMPLE_RATE:.2f} s (no frame above {level_dbfs(level):.0f} dBFS)."
            )
            findings.append(audio_finding(start, end, "silence", evidence, strength))
    for start, end in noise_runs:
        seconds = (end - start) / SAMPLE_RATE
        if seconds >= WEAK_NOISE_SECONDS:
            evidence = f"Noise-like signal for {seconds:.2f} s, from {start / SAMPLE_RATE:.2f} s to {end / SAMPLE_RATE:.2f} s (no speech structure)."
            findings.append(audio_finding(start, end, "noise", evidence, 0.9 if seconds >= STRONG_NOISE_SECONDS else 0.66))
    metrics["longest_noise_ms"] = max((end - start for start, end in noise_runs), default=0) * 1000 // SAMPLE_RATE
    for start, end in peak_runs:
        if end - start >= WINDOW:
            findings.append(audio_finding(start, end, "clipping", "Sustained near-full-scale PCM samples.", 0.88))
    return metrics, findings


def reconcile_signal_with_transcript(
    findings: list[Finding],
    alignment_metrics: dict,
    signal_metrics: dict,
) -> list[Finding]:
    """Downgrade a clip's silence findings when the time left for speech cannot hold the recognised script.

    Contract:
    - Pure: returns a new list and never mutates ``findings`` or its items.
    - ``alignment_metrics`` is the metrics dict from ``speech_findings``.
      ``signal_metrics`` is the metrics dict from ``signal_checks`` and must carry
      ``duration_ms`` and ``silence_ratio``. If either input is missing those keys,
      or alignment is ``{"alignment": "unavailable"}``, the findings come back unchanged.
    - The script counts as recognised when it has at least 12 aligned characters,
      at least 90% of them were matched and no more than 5% were deleted.
      Insertions are ignored, so a repeated utterance (high CER, no deletions)
      still counts as recognised.
    - Voiced time is ``duration * (1 - silence_ratio)``. If speaking the
      recognised characters in that time would take more than
      ``MAX_CHARACTERS_PER_SECOND``, the same pace bound ``speech_findings`` uses,
      then the quiet-frame measurement must have marked speech as quiet. Every
      ``garbled_audio`` finding from the ``silence`` check is then kept for audit
      at strength 0.5, below every calibrated threshold.
    - Otherwise nothing changes. In particular, a real gap in a clip whose
      voiced time plausibly holds the script stays at full strength, because
      dead air is audible even when every word is spoken. Clipping and
      transcript findings are never changed.
    """
    keys = ("matched_characters", "substitutions", "deletions")
    if alignment_metrics.get("alignment") == "unavailable" or any(key not in alignment_metrics for key in keys):
        return list(findings)
    if "duration_ms" not in signal_metrics or "silence_ratio" not in signal_metrics:
        return list(findings)
    hits, substitutions, deletions = (alignment_metrics[key] for key in keys)
    script = hits + substitutions + deletions
    if script < 12 or hits / script < 0.9 or deletions > 0.05 * script:
        return list(findings)
    voiced_seconds = signal_metrics["duration_ms"] * (1 - signal_metrics["silence_ratio"]) / 1000
    if hits <= MAX_CHARACTERS_PER_SECOND * voiced_seconds:
        return list(findings)
    note = (
        f" The transcript recovered {hits / script:.0%} of the script, too much to speak in the "
        f"{voiced_seconds:.2f} s measured as voiced, so the quiet reading likely reflects a low recording level."
    )
    return [
        replace(finding, strength=min(finding.strength, 0.5), detail=finding.detail + note)
        if finding.category == "garbled_audio" and finding.check == "silence"
        else finding
        for finding in findings
    ]


def audio_finding(start: int, end: int, check: str, evidence: str, strength: float) -> Finding:
    """Convert measured sample indices to a garbled-audio finding in milliseconds."""
    span = Span("audio_ms", start * 1000 // SAMPLE_RATE, math.ceil(end * 1000 / SAMPLE_RATE))
    return Finding("garbled_audio", "major", span, check, evidence, "Inspect the measured interval.", strength)


def aligned_span(text: str, offsets: list[tuple[int, int]], start: int, end: int) -> tuple[int, int]:
    """Map a substantive alignment edit back to its original text, trimming boundary spaces."""
    start += text[start] == " "
    end -= text[end - 1] == " "
    return offsets[start][0], offsets[end - 1][1]


# Repetition constants, all stated in units of the normalized script.
MIN_BLOCK = 5  # A shared run shorter than a short word plus its space may be chance syllable agreement.
REPEAT_PURITY = 0.6  # Most of the extra transcript material must be script text, not noise.
CLAUSE_SHARE = 0.6  # Re-speaking most of a clause is a repeat, not a stutter.
REPEAT_WORDS = 4  # ASR chunk seams duplicate one or two words. Four whole words are spoken twice.
CLAUSE_MARK_NAMES = ("FULL STOP", "COMMA", "DANDA", "COLON", "QUESTION MARK", "EXCLAMATION MARK")


def clause_ids(text: str, offsets: list[tuple[int, int]]) -> list[int]:
    """Number each normalized character by the clause it falls in within the original text.

    Clause marks are recognised by Unicode name, not by a fixed list, so dandas,
    Arabic-script commas and full stops in unseen scripts all count. A point or
    comma between two digits is a number separator, not a clause mark.
    """
    prefix, count = [0], 0
    for index, char in enumerate(text):
        mark = char == "\n" or (unicodedata.category(char) == "Po" and any(name in unicodedata.name(char, "") for name in CLAUSE_MARK_NAMES))
        if mark and char in ".,:" and 0 < index < len(text) - 1 and text[index - 1].isdigit() and text[index + 1].isdigit():
            mark = False
        count += mark
        prefix.append(count)
    return [prefix[start] for start, _ in offsets]


def script_blocks(reference: str, text: str, start: int, end: int) -> list[tuple[int, int, int]]:
    """Match pieces of ``text[start:end]`` independently against the whole script.

    Returns ``(reference_start, text_start, size)`` blocks of at least ``MIN_BLOCK``
    characters in text order. Each piece is matched on its own, so a rotation
    (the tail of one copy followed by the head of the next) or a phrase looped
    several times is still credited. A single monotone alignment cannot do this.
    """
    matcher = SequenceMatcher(None, reference, text, autojunk=False)
    blocks, pending = [], [(start, end)]
    while pending:
        low, high = pending.pop()
        if high - low < MIN_BLOCK:
            continue
        match = matcher.find_longest_match(0, len(reference), low, high)
        if match.size < MIN_BLOCK:
            continue
        blocks.append((match.a, match.b, match.size))
        pending += [(low, match.b), (match.b + match.size, high)]
    return sorted(blocks, key=lambda block: block[1])


def inserted_regions(edits) -> list[tuple[int, int, int]]:
    """Group inserted transcript characters into ``(start, end, inserted)`` regions.

    Agreement too short to form a block cannot anchor the alignment, so such gaps are bridged.
    """
    regions = []
    for edit in edits:
        if edit.type != "insert":
            continue
        start, end = edit.hyp_start_idx, edit.hyp_end_idx
        if regions and start - regions[-1][1] < MIN_BLOCK:
            first, _, inserted = regions[-1]
            regions[-1] = (first, end, inserted + end - start)
        else:
            regions.append((start, end, end - start))
    return regions


def repetition_findings(expected: str, transcript: str, alignment, min_length: int) -> tuple[list[Finding], int]:
    """Find extra transcript material that re-speaks script content already heard once.

    Only characters the global alignment left over (insertions) are examined.
    A phrase the script itself contains twice is consumed by the alignment and
    can never be counted as a repeat. Leftover material counts only when it
    matches script positions the alignment also matched elsewhere. Moved text
    (deleted in one place, inserted in another) therefore does not count.
    Returns the findings and the inserted characters they explain.
    """
    reference, reference_offsets = normalize_with_offsets(expected)
    actual, actual_offsets = normalize_with_offsets(transcript)
    edits = alignment.alignments[0]
    heard = [False] * len(reference)
    for edit in edits:
        if edit.type == "equal":
            heard[edit.ref_start_idx : edit.ref_end_idx] = [True] * (edit.ref_end_idx - edit.ref_start_idx)
    clauses = clause_ids(expected, reference_offsets)
    clause_sizes = Counter(clauses[index] for index, char in enumerate(reference) if char != " ")
    words = [(match.start(), match.end()) for match in re.finditer(r"\S+", reference)]
    findings, explained = [], 0
    for region_start, region_end, inserted in inserted_regions(edits):
        if inserted < min_length:
            continue
        blocks = script_blocks(reference, actual, region_start, region_end)
        covered, positions = 0, set()
        for reference_start, _, size in blocks:
            for index in range(reference_start, reference_start + size):
                if heard[index]:
                    covered += 1
                    positions.add(index)
        if covered < min_length or covered < REPEAT_PURITY * (region_end - region_start):
            continue
        share = max(sum(clauses[index] == clause and reference[index] != " " for index in positions) / size for clause, size in clause_sizes.items())
        whole_words = sum(all(index in positions for index in range(first, last)) for first, last in words)
        # A one-word clause (a list item, "Yes,") re-spoken once is too close to an ASR seam to call strong.
        strong = whole_words >= REPEAT_WORDS or (share >= CLAUSE_SHARE and whole_words >= 2)
        start, end = aligned_span(actual, actual_offsets, blocks[0][1], max(block[1] + block[2] for block in blocks))
        explained += inserted
        findings.append(
            Finding(
                "speech_repetition",
                "major",
                Span("transcript", start, end),
                "transcript_alignment",
                transcript[start:end],
                f"The back-transcript re-speaks script text already heard: {whole_words} whole words, {share:.0%} of a clause.",
                0.84 if strong else 0.66,
            )
        )
    return findings, explained


def repeated_script(evidence: str, reference: str) -> set[int]:
    """Return the normalized script positions that a repetition's evidence re-speaks."""
    text = normalize_with_offsets(evidence)[0]
    return {index for start, _, size in script_blocks(reference, text, 0, len(text)) for index in range(start, start + size)}


def same_repeat(evidence: str, other: str, reference: str) -> bool:
    """Tell whether two repetition findings re-speak mostly the same script text.

    A second segmentation can transcribe or rotate the repeated stretch
    differently, so the evidence is compared by the script it covers, not as a string.
    """
    if evidence.strip() == other.strip():
        return True
    first, second = repeated_script(evidence, reference), repeated_script(other, reference)
    return bool(first and second) and len(first & second) >= 0.5 * min(len(first), len(second))


def transcript_time(finding: Finding, transcript_length: int, duration_ms: int) -> Finding:
    """Place a transcript-span finding on the audio timeline, keeping the text span as anchor.

    Hosted ASR returns no word timestamps, so the interval assumes an even speaking rate
    across the clip. The transcript excerpt stays the evidence and the exact text anchor.
    """
    if finding.span.field != "transcript" or transcript_length <= 0:
        return finding
    start = finding.span.start * duration_ms // transcript_length
    end = max(start + 1, -(-finding.span.end * duration_ms // transcript_length))
    detail = f"{finding.detail} Time is estimated from the transcript position."
    return replace(finding, span=Span("audio_ms", start, min(end, duration_ms)), anchor=finding.span, detail=detail)


def speech_findings(
    expected: str,
    transcript: str,
    duration_ms: int,
    *,
    script_field: str = "translation",
) -> tuple[dict, list[Finding]]:
    """Align script and ASR text, using sustained edits as speech-quality evidence."""
    reference, reference_offsets = normalize_with_offsets(expected)
    actual, actual_offsets = normalize_with_offsets(transcript)
    if not reference:
        return {"alignment": "unavailable"}, []
    alignment = process_characters(reference, actual)
    findings = []
    character_rate = len(reference) * 1000 / max(duration_ms, 1)
    duration_risk = duration_ms < 450 or character_rate > MAX_CHARACTERS_PER_SECOND
    min_length = max(6, min(16, math.ceil(len(reference) * 0.14)))
    for edit in alignment.alignments[0]:
        if edit.type == "delete" and edit.ref_end_idx - edit.ref_start_idx >= min_length:
            start, end = aligned_span(reference, reference_offsets, edit.ref_start_idx, edit.ref_end_idx)
            suffix = edit.ref_end_idx >= len(reference) - 1
            # Character rate is only corroboration. Scripts and voices differ in pace.
            strength = 0.89 if suffix and duration_risk else 0.68
            findings.append(
                Finding(
                    "speech_omission",
                    "major",
                    Span(script_field, start, end),
                    "transcript_alignment",
                    expected[start:end],
                    "A sustained script span is missing from the back-transcript.",
                    strength,
                )
            )
    repeats, repeated_characters = repetition_findings(expected, transcript, alignment, min_length)
    findings.extend(transcript_time(finding, len(transcript), duration_ms) for finding in repeats)
    # Material explained as a spoken repeat is not evidence of unintelligible audio.
    residual = alignment.substitutions + alignment.deletions + max(0, alignment.insertions - repeated_characters)
    adjusted_cer = residual / len(reference)
    if adjusted_cer >= 0.65 and actual and len(reference) >= 12:
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
        "matched_characters": alignment.hits,
        "repeated_characters": repeated_characters,
        "repeat_adjusted_cer": round(adjusted_cer, 5),
        "script_characters_per_second": round(character_rate, 3),
        "short_for_script": duration_risk,
    }, findings


def speech_recheck_window(duration_ms: int) -> int | None:
    """Choose new ASR boundaries for clips long enough to give both chunks context."""
    if duration_ms < 3_000:
        return None
    return max(2, min(14, duration_ms // 2_000))


def corroborate_speech(
    expected: str,
    transcript: str,
    duration_ms: int,
    findings: list[Finding],
    *,
    script_field: str,
) -> tuple[list[Finding], list[str], int]:
    """Update speech evidence only when differently segmented audio supports or refutes it."""
    metrics, repeated = speech_findings(expected, transcript, duration_ms, script_field=script_field)
    # A blank or garbled second transcript can repeat an ASR failure rather than
    # confirm a speech defect. Require recognisable material before deciding.
    if metrics.get("matched_characters", 0) < 6:
        return findings, [], 0
    normalized = normalize_with_offsets(transcript)[0]
    normalized_expected = normalize_with_offsets(expected)[0]
    confirmed = set()
    recovered = 0
    updated = []
    for finding in findings:
        if finding.check == "transcript_alignment" and finding.category == "speech_omission":
            missing = normalize_with_offsets(finding.evidence)[0]
            # A phrase that was spoken once may still be missing a second time.
            if missing and normalized.count(missing) >= normalized_expected.count(missing):
                recovered += 1
                continue
        if finding.check == "transcript_alignment" and any(
            other.category == finding.category
            and (
                (
                    finding.category == "speech_omission"
                    and other.span.field == finding.span.field
                    and max(other.span.start, finding.span.start) < min(other.span.end, finding.span.end)
                )
                or (finding.category == "speech_repetition" and same_repeat(finding.evidence, other.evidence, normalized_expected))
            )
            for other in repeated
        ):
            confirmed.add(finding.category)
            finding = replace(
                finding,
                strength=max(finding.strength, 0.87),
                detail=finding.detail + " Observed again after re-segmenting the same audio.",
            )
        updated.append(finding)
    return updated, sorted(confirmed), recovered
