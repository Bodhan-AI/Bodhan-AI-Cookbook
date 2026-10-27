"""The processing pipeline: convert -> split -> transcribe -> translate -> analyse -> CallRecord.

Used by the background jobs (``jobs.py``) and the command-line tool (``cli.py``).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from . import audio
from .analysis import AnalysisError, CallAnalyzer
from .bodhan_client import BodhanClient, BodhanError
from .config import Settings
from .diarization import Diarizer
from .languages import translation_supported
from .models import CallAnalysis, CallRecord, CallStatus, Progress, Segment, Stage
from .segmenter import Piece, SegmenterConfig, split_by_pauses, split_by_turns

log = logging.getLogger(__name__)

ProgressCallback = Callable[[Progress], None]


class PipelineError(Exception):
    """The whole call failed. ``code`` and ``message`` are shown to the user."""

    def __init__(self, code: str, message: str, record: CallRecord | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.record = record


@dataclass(frozen=True)
class CallInput:
    call_id: str
    audio_path: Path
    source_file: str
    language: str | None = None
    ticket_id: str | None = None
    agent_name: str | None = None


async def process_call(
    call: CallInput,
    *,
    client: BodhanClient,
    settings: Settings,
    workdir: Path,
    diarizer: Diarizer | None = None,
    analyzer: CallAnalyzer | None = None,
    on_progress: ProgressCallback = lambda _p: None,
) -> CallRecord:
    warnings: list[str] = []

    on_progress(Progress(stage=Stage.converting, message="Converting audio"))
    wav_path = workdir / "converted_16k_mono.wav"
    await asyncio.to_thread(
        audio.convert_to_wav,
        call.audio_path,
        wav_path,
        max_seconds=settings.max_duration_seconds,
        ffmpeg=settings.ffmpeg_path,
    )
    samples = await asyncio.to_thread(audio.read_wav, wav_path)
    duration = round(len(samples) / audio.SAMPLE_RATE, 2)
    audio.check_duration(duration, settings.max_duration_seconds)

    pieces = await _split(samples, wav_path, duration, settings, diarizer, warnings, on_progress)
    diarized = diarizer is not None and any(p.speaker for p in pieces)
    segments = [Segment(segment_no=i + 1, start=p.start, end=p.end, speaker=p.speaker) for i, p in enumerate(pieces)]
    if not segments:
        warnings.append("No speech was found in the recording.")

    await _transcribe(segments, samples, call.language, client, on_progress)
    if all(s.error for s in segments) and segments:
        raise PipelineError(
            "transcription_failed",
            "Bodhan Speech-to-Text failed for every piece of the call. Please try again later.",
            _record(call, duration, segments, warnings, CallStatus.failed, settings, diarized),
        )

    translation_available = translation_supported(call.language)
    if call.language == "en":
        for s in segments:
            if not s.error:
                s.english_text = s.original_text
    elif not translation_available:
        warnings.append("Bodhan Translation does not support this language, so only the original transcript is shown.")
    else:
        await _translate(segments, client, on_progress)

    analysis = await _analyze(segments, analyzer, warnings, on_progress) if analyzer is not None else None

    failed = sum(1 for s in segments if s.error)
    if failed:
        warnings.append(f"{failed} of {len(segments)} pieces could not be fully processed; see the marked segments.")
    status = CallStatus.partial if failed else CallStatus.completed
    on_progress(Progress(stage=Stage.done, done=len(segments), total=len(segments), message="Done"))
    record = _record(call, duration, segments, warnings, status, settings, diarized)
    record.translation_available = translation_available
    record.analysis = analysis
    return record


async def _split(
    samples: np.ndarray,
    wav_path: Path,
    duration: float,
    settings: Settings,
    diarizer: Diarizer | None,
    warnings: list[str],
    on_progress: ProgressCallback,
) -> list[Piece]:
    cfg = SegmenterConfig(max_len=settings.max_piece_seconds)
    if diarizer is not None:
        on_progress(Progress(stage=Stage.diarizing, message="Identifying speakers"))
        try:
            turns = await diarizer.diarize(wav_path, duration)
        except Exception as exc:  # diarization is an extra; never fail the call because of it
            log.warning("diarization failed: %s", type(exc).__name__)
            warnings.append("Speaker identification failed, so speakers are not labelled.")
        else:
            if turns:
                return await asyncio.to_thread(split_by_turns, samples, turns, cfg)
            warnings.append("The speaker model found no speech turns, so speakers are not labelled.")
    on_progress(Progress(stage=Stage.segmenting, message="Finding pauses"))
    return await asyncio.to_thread(split_by_pauses, samples, cfg)


async def _for_each(
    items: Sequence[Segment], work: Callable[[Segment], Awaitable[None]], on_done: Callable[[int], None]
) -> None:
    """Run ``work`` on every segment concurrently (the client limits parallelism)."""
    done = 0

    async def run(seg: Segment) -> None:
        nonlocal done
        await work(seg)
        done += 1
        on_done(done)

    async with asyncio.TaskGroup() as tg:  # an auth error cancels the remaining pieces
        for seg in items:
            tg.create_task(run(seg))


def _step(verb: str, done: int, total: int) -> str:
    return f"{verb} piece {min(done + 1, total)} of {total}" if done < total else f"{verb} finished"


def _raise_if_fatal(exc: BodhanError) -> None:
    if exc.is_auth_error:
        raise PipelineError("bodhan_auth", "The server's Bodhan API key is missing or was rejected.")


async def _transcribe(
    segments: list[Segment],
    samples: np.ndarray,
    language: str | None,
    client: BodhanClient,
    on_progress: ProgressCallback,
) -> None:
    total = len(segments)
    if not total:
        return

    async def work(seg: Segment) -> None:
        wav = audio.wav_bytes(samples, seg.start, seg.end)
        try:
            seg.original_text = await client.transcribe(wav, language)
        except BodhanError as exc:
            _raise_if_fatal(exc)
            seg.error = f"Transcription failed ({exc.code})"

    on_progress(Progress(stage=Stage.transcribing, done=0, total=total, message=f"Transcribing piece 1 of {total}"))
    await _guard(
        _for_each(
            segments,
            work,
            lambda n: on_progress(
                Progress(stage=Stage.transcribing, done=n, total=total, message=_step("Transcribing", n, total))
            ),
        )
    )


async def _translate(segments: list[Segment], client: BodhanClient, on_progress: ProgressCallback) -> None:
    todo = [s for s in segments if not s.error and s.original_text]
    for s in segments:
        if not s.error and not s.original_text:
            s.english_text = ""
    total = len(todo)
    if not total:
        return

    async def work(seg: Segment) -> None:
        try:
            result = await client.translate(seg.original_text, "en")
        except BodhanError as exc:
            _raise_if_fatal(exc)
            seg.error = f"Translation failed ({exc.code})"
            return
        if result.truncated:
            # FR-7: never present a cut-off translation as complete.
            seg.error = "Translation was cut off by Bodhan (truncated), so it is not shown"
            return
        seg.english_text = result.text

    on_progress(Progress(stage=Stage.translating, done=0, total=total, message=f"Translating piece 1 of {total}"))
    await _guard(
        _for_each(
            todo,
            work,
            lambda n: on_progress(
                Progress(stage=Stage.translating, done=n, total=total, message=_step("Translating", n, total))
            ),
        )
    )


async def _analyze(
    segments: list[Segment], analyzer: CallAnalyzer, warnings: list[str], on_progress: ProgressCallback
) -> CallAnalysis | None:
    on_progress(Progress(stage=Stage.analyzing, message="Summarizing the call"))
    try:
        return await analyzer.analyze(segments)
    except AnalysisError as exc:  # the summary is an extra; never fail the call because of it
        log.warning("analysis failed: %s", exc.code)
        for s in segments:
            s.sentiment = None
        warnings.append(f"The AI summary and sentiment could not be produced: {exc.message}")
        return None


async def _guard(coro: Awaitable[None]) -> None:
    """Unwrap the ExceptionGroup a TaskGroup raises so callers see the PipelineError."""
    try:
        await coro
    except* PipelineError as group:
        raise group.exceptions[0] from None


def _join(segments: list[Segment], field: str) -> str:
    lines = []
    for s in segments:
        text = getattr(s, field)
        if text:
            lines.append(f"{s.speaker}: {text}" if s.speaker else text)
    return "\n".join(lines)


def _record(
    call: CallInput,
    duration: float,
    segments: list[Segment],
    warnings: list[str],
    status: CallStatus,
    settings: Settings,
    diarized: bool,
) -> CallRecord:
    return CallRecord(
        call_id=call.call_id,
        source_file=call.source_file,
        ticket_id=call.ticket_id,
        agent_name=call.agent_name,
        duration_seconds=duration,
        source_language=call.language,
        processed_at=datetime.now(ZoneInfo(settings.timezone)).replace(microsecond=0),
        status=status,
        speaker_count=len({s.speaker for s in segments if s.speaker}) if diarized else None,
        segments=segments,
        full_text_original=_join(segments, "original_text"),
        full_text_english=_join(segments, "english_text"),
        warnings=warnings,
    )
