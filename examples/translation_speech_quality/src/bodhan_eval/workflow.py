"""Evaluate each row and route costly checks using available evidence."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from pathlib import Path

from .audio_checks import ASR_SECONDS, Audio, AudioError, chunks, decode_audio, merge_wav, signal_checks, speech_findings, speech_parts
from .judge import RUBRIC, judge_input, parse_judgment, verdict_signature
from .providers import Call, CallError, Providers, digest
from .schema import Evaluation, Finding, Item, Span
from .text_checks import CHRF_PLUS_PLUS, entity_findings, format_findings, number_findings, script_findings

LOG = logging.getLogger(__name__)


def register(result: Evaluation, call: Call) -> str:
    """Attach successful model usage to the item and return its UTF-8 response."""
    result.usage[call.key] = call.usage
    return call.value.decode()


def record_failure(result: Evaluation, check: str, error: CallError | AudioError | OSError) -> None:
    """Record a failed operation without stopping unrelated checks or rows."""
    if isinstance(error, CallError):
        status = "skipped" if error.code in ("budget", "missing_credentials", "missing_configuration", "credit_exhausted") else "error"
        result.record(check, status, reason=str(error), code=error.code)
    else:
        reason = str(error) if isinstance(error, AudioError) else "Could not access or write a local audio file."
        result.record(check, "error", reason=reason)


async def prepare_translation(result: Evaluation, providers: Providers, generate: bool) -> None:
    """Select a supplied translation or create a missing candidate when requested."""
    item = result.item
    if item.translation is not None:
        result.translation = item.translation
        result.record("translation_input", origin="supplied")
    elif generate:
        try:
            if item.lang == "en":
                result.translation = item.source
                result.record("translation_input", origin="same_language")
            else:
                result.translation = register(result, await providers.translate(item.source, item.lang))
                result.record("translation_input", origin="generated")
        except CallError as error:
            record_failure(result, "translation_input", error)
    else:
        result.record("translation_input", "unavailable", reason="No translation was supplied.")


async def prepare_audio(
    result: Evaluation,
    providers: Providers,
    dataset_dir: Path,
    output_dir: Path,
    generate: bool,
) -> tuple[Audio | None, str | None]:
    """Decode supplied media or synthesize a missing clip and return the known script."""
    item = result.item
    path = None
    expected = item.translation if item.lang != "en" else item.translation or item.source
    try:
        if item.audio is not None:
            path = (dataset_dir / item.audio).resolve()
            result.audio_file = item.audio
            result.record("audio_input", origin="supplied")
        elif generate and result.translation and result.translation.strip():
            parts = []
            for part in speech_parts(result.translation):
                call = await providers.speak(part, item.lang)
                result.usage[call.key] = call.usage
                parts.append(call.value)
            data = merge_wav(parts)
            result.audio_file = f"generated/{digest(data)}.wav"
            path = output_dir / result.audio_file
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            expected = result.translation
            result.record("audio_input", origin="generated", tts_parts=len(parts))
        else:
            result.record("audio_input", "unavailable", reason="No usable audio or script was supplied.")
        if path is not None:
            decoded = await asyncio.to_thread(decode_audio, path)
            result.audio_digest = digest(decoded.samples.tobytes())
            return decoded, expected
    except (CallError, AudioError, OSError) as error:
        record_failure(result, "audio_input", error)
    return None, expected


async def text_checks(result: Evaluation, providers: Providers) -> str | None:
    """Gather source/reference differences and an optional English round trip."""
    item, candidate = result.item, result.translation
    if candidate is None:
        return None
    if not candidate.strip():
        result.candidates.append(
            Finding(
                "omission",
                "major",
                Span("source", 0, len(item.source)),
                "empty_translation",
                item.source,
                "The supplied translation is empty.",
                1.0,
            )
        )
        result.record("translation_structure", checks=["nonempty"])
        return None
    reference = item.reference
    result.candidates.extend(number_findings(reference or item.source, candidate, "reference" if reference else "source"))
    result.candidates.extend(format_findings(item.source, candidate))
    result.candidates.extend(entity_findings(item.source, candidate, "source"))
    result.candidates.extend(script_findings(candidate, item.lang, item.source))
    result.record("translation_structure", checks=["numbers", "entities", "markdown", "latex", "script"])
    if reference:
        result.record("chrf", score=round(CHRF_PLUS_PLUS.sentence_score(candidate, [reference]).score, 4))
    else:
        result.record("chrf", "unavailable", reason="No reference translation.")
    if item.lang == "en":
        return candidate
    try:
        back = register(result, await providers.translate(candidate, "en"))
        result.record("back_translation", text=back)
        return back
    except CallError as error:
        record_failure(result, "back_translation", error)
        return None


async def transcribe(
    result: Evaluation,
    providers: Providers,
    audio: Audio,
    *,
    seconds: int = ASR_SECONDS,
) -> str:
    """Combine independently transcribed chunks without inventing word timestamps."""
    pieces = []
    for wav, duration_ms in chunks(audio, seconds):
        pieces.append(register(result, await providers.transcribe(wav, result.item.lang, duration_ms)).strip())
    return " ".join(piece for piece in pieces if piece)


async def speech_checks(
    result: Evaluation,
    providers: Providers,
    audio: Audio | None,
    expected: str | None,
) -> None:
    """Combine waveform evidence and transcript alignment when a script is known."""
    if audio is None:
        return
    metrics, findings = await asyncio.to_thread(signal_checks, audio)
    result.record("signal", **metrics)
    result.candidates.extend(findings)
    try:
        result.transcript = await transcribe(result, providers, audio)
        result.record("transcription")
    except CallError as error:
        record_failure(result, "transcription", error)
        return
    if not expected:
        result.record("speech_alignment", "unavailable", reason="The intended spoken script is unknown.")
        return
    metrics, findings = speech_findings(expected, result.transcript, audio.duration_ms)
    result.record("speech_alignment", "unavailable" if metrics.get("alignment") == "unavailable" else "ok", **metrics)
    result.candidates.extend(findings)


async def judge_checks(
    result: Evaluation,
    providers: Providers,
    back: str | None,
    audio: Audio | None,
    expected: str | None,
    repeat_sample: bool,
) -> None:
    """Validate judge evidence and optionally corroborate an unclear speech alignment."""
    if not (result.translation and result.translation.strip()) and result.transcript is None:
        result.record("judge", "unavailable", reason="No text output or back-transcript to judge.")
        return
    uncertain_speech = (
        audio is not None
        and audio.duration_ms > 14_000
        and expected is not None
        and result.transcript is not None
        and any(finding.check == "transcript_alignment" for finding in result.candidates)
    )
    payload, segments = judge_input(result, back, uncertain_speech)
    try:
        call = await providers.judge(RUBRIC, payload)
        result.usage[call.key] = call.usage
        findings, verify, rejected = parse_judgment(call.value, segments)
        if result.translation is None:
            rejected += len(findings)
            findings = []
        result.candidates.extend(findings)
        result.record("judge", "partial" if rejected else "ok", findings=len(findings), rejected=rejected)
    except CallError as error:
        record_failure(result, "judge", error)
        return
    if verify and uncertain_speech:
        try:
            second = await transcribe(result, providers, audio, seconds=14)
            _, repeated_findings = speech_findings(expected, second, audio.duration_ms)
            corroborated = []
            for index, finding in enumerate(result.candidates):
                if finding.check != "transcript_alignment":
                    continue
                if any(
                    item.category == finding.category
                    and (
                        (item.span.field == finding.span.field == "translation" and item.span.start < finding.span.end and finding.span.start < item.span.end)
                        or (finding.category == "speech_repetition" and item.evidence.strip() == finding.evidence.strip())
                    )
                    for item in repeated_findings
                ):
                    corroborated.append(finding.category)
                    result.candidates[index] = replace(
                        finding,
                        strength=max(finding.strength, 0.87),
                        detail=finding.detail + " Observed again after re-segmenting the audio.",
                    )
            result.record("speech_recheck", corroborated=sorted(set(corroborated)))
        except CallError as error:
            record_failure(result, "speech_recheck", error)
    if repeat_sample:
        try:
            repeat = await providers.judge(RUBRIC, payload, fresh=True)
            # Fresh repeats have their own usage in telemetry and never overwrite cached judgments.
            again, _, repeat_rejected = parse_judgment(repeat.value, segments)
            if rejected or repeat_rejected:
                result.record("judge_repeat", "partial", reason="At least one verdict contained invalid evidence.")
            else:
                result.record("judge_repeat", agrees=verdict_signature(findings) == verdict_signature(again))
        except CallError as error:
            record_failure(result, "judge_repeat", error)


async def evaluate_item(
    item: Item,
    providers: Providers,
    dataset_dir: Path,
    output_dir: Path,
    generate: bool,
    repeat_sample: bool,
    *,
    enable_speech: bool = True,
) -> Evaluation:
    """Evaluate both modalities independently and attach logs after all checks finish."""
    result = Evaluation(item)
    await prepare_translation(result, providers, generate)
    audio, expected = None, None
    if enable_speech:
        audio, expected = await prepare_audio(result, providers, dataset_dir, output_dir, generate)
    else:
        result.record("audio_input", "unavailable", reason="Speech checks disabled for this run.")
    back, _ = await asyncio.gather(
        text_checks(result, providers),
        speech_checks(result, providers, audio, expected),
    )
    await judge_checks(result, providers, back, audio, expected, repeat_sample)
    for name, value in result.checks.items():
        LOG.info(
            "item_check",
            extra={
                "event_fields": {
                    "id": item.id,
                    "lang": item.lang,
                    "check": name,
                    "status": value["status"],
                    "code": value.get("code"),
                    "reason": value.get("reason"),
                }
            },
        )
    return result
