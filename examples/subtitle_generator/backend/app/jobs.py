"""Subtitle jobs, run in the background: (download) -> video -> audio -> pieces -> transcripts -> timed lines -> translation."""

import asyncio
import logging
import os
import shutil
import subprocess
import time
from collections.abc import Callable

import httpx

from . import audio, bodhan, db, download, vad, youtube
from .config import settings
from .subtitles import Segment, clean_transcript, lines_from_words, time_lines

logger = logging.getLogger("subtitle_generator")

# Pause between transcription requests, so long videos don't burst the rate limit
PAUSE_BETWEEN_PIECES = 1.5
# Subtitle lines translated at the same time
TRANSLATE_CONCURRENCY = 2
# Seconds between saves of the translated lines while translating
SAVE_EVERY = 1.0

Report = Callable[[int, str], None]


def check_video(video_path: str) -> None:
    """Refuse a video before a job is created: too long, or nothing to transcribe."""
    duration, has_audio = audio.probe(video_path)
    if duration and duration > settings.max_video_seconds:
        raise audio.MediaError(f"The video is {duration / 60:.1f} minutes long; the limit is {settings.max_video_seconds // 60} minutes.")
    if has_audio is False:
        raise audio.MediaError("The video has no audio track to transcribe.")


def translating(language: str, target_language: str) -> bool:
    return bool(target_language and target_language != language)


def can_resume(job: db.Job) -> bool:
    """A job that failed after its transcription was saved, with translation lines still missing."""
    if job.status != "failed" or not job.segments or not translating(job.language, job.target_language):
        return False
    return not job.translated_segments or any(line is None for line in job.translated_segments)


def _reporter(job_id: str) -> Report:
    def report(progress: int, step: str) -> None:
        db.update_job(job_id, progress=progress, step=step)

    return report


async def run_job(job_id: str, video_path: str, workdir: str, language: str, target_language: str) -> None:
    """Run one job and record its progress and result in the database. Deletes workdir once transcribed."""
    try:
        db.update_job(job_id, status="running")
        until = 75 if translating(language, target_language) else 98
        segments = await transcribe_video(video_path, workdir, language, _reporter(job_id), until)
        # Saved before translating: if translation fails, the subtitles are kept and the job can be resumed
        db.update_job(job_id, segments=[s.model_dump() for s in segments])
    except Exception as exc:
        db.update_job(job_id, status="failed", error=_message(exc))
        return
    finally:
        shutil.rmtree(workdir, ignore_errors=True)  # the video and audio aren't needed after transcription
    await _translate_and_finish(job_id, segments, language, target_language, [])


async def run_url_job(job_id: str, url: str, workdir: str, language: str, target_language: str) -> None:
    """Download the video from a link (a video file, or YouTube), then run the job as for an upload."""

    def report(percent: int | None) -> None:
        db.update_job(job_id, progress=1, step=f"Downloading the video ({percent}%)" if percent is not None else "Downloading the video")

    try:
        db.update_job(job_id, status="running", step="Downloading the video")
        fetch = youtube.download if youtube.is_youtube(url) else download.download
        video_path, title = await fetch(url, workdir, report)
        db.update_job(job_id, file_name=title[:200])
        await asyncio.to_thread(check_video, video_path)
    except Exception as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        db.update_job(job_id, status="failed", error=_message(exc))
        return
    await run_job(job_id, video_path, workdir, language, target_language)


async def resume_job(job_id: str) -> None:
    """Finish a job's translation, translating only the lines still missing."""
    job = db.get_job(job_id)
    if job is None or not job.segments:  # the API checks this before starting; nothing to resume otherwise
        return
    segments = [Segment(**s) for s in job.segments]
    await _translate_and_finish(job_id, segments, job.language, job.target_language, job.translated_segments or [])


async def _translate_and_finish(job_id: str, segments: list[Segment], language: str, target_language: str, saved: list) -> None:
    try:
        translated = None
        if translating(language, target_language):
            translated = await translate_lines(job_id, segments, target_language, saved)
        db.update_job(
            job_id, status="done", progress=100, step="Done", error=None, translated_segments=[s.model_dump() for s in translated] if translated else None
        )
    except Exception as exc:
        db.update_job(job_id, status="failed", error=_message(exc))


async def translate_lines(job_id: str, segments: list[Segment], target_language: str, saved: list) -> list[Segment]:
    """
    Translate each line, keeping its timing. Finished lines are saved as they come in (at most once a second, and
    always when the job stops; None marks a line still to do), so a job stopped halfway resumes with only the rest.
    """
    lines: list[dict | None] = list(saved) + [None] * (len(segments) - len(saved))
    limit = asyncio.Semaphore(TRANSLATE_CONCURRENCY)
    last_save = 0.0

    def save_progress(force: bool = False) -> None:
        # At most once a second: each save rewrites the whole list, and long videos have hundreds of lines
        nonlocal last_save
        if not force and time.monotonic() - last_save < SAVE_EVERY:
            return
        last_save = time.monotonic()
        done = sum(line is not None for line in lines)
        db.update_job(job_id, translated_segments=list(lines), progress=75 + 23 * done // len(lines), step=f"Translating line {done} of {len(lines)}")

    async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=20)) as client:

        async def translate_line(i: int) -> None:
            async with limit:
                text = await bodhan.translate(client, segments[i].text, target_language)
            lines[i] = Segment(start=segments[i].start, end=segments[i].end, text=text).model_dump()
            save_progress()

        save_progress(force=True)
        tasks = [asyncio.create_task(translate_line(i)) for i, line in enumerate(lines) if line is None]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:  # stop the lines not yet sent
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            save_progress(force=True)  # keep every finished line, so a resume translates only the rest
            raise
    return [Segment(**line) for line in lines if line is not None]  # all filled by now


def _message(exc: Exception) -> str:
    if isinstance(exc, (audio.MediaError, bodhan.BodhanError)):
        return str(exc)
    if isinstance(exc, subprocess.TimeoutExpired):
        return "Processing timed out. Try a shorter video."
    logger.exception("Subtitle generation failed", exc_info=exc)
    return f"Subtitle generation failed: {exc or type(exc).__name__}"  # some errors carry no text


async def transcribe_video(video_path: str, workdir: str, language: str, report: Report, until: int = 98) -> list[Segment]:
    """Audio -> pieces -> transcripts -> timed subtitle lines, for one checked video. Progress runs up to `until`%."""
    report(3, "Extracting the audio")
    wav_path = os.path.join(workdir, "audio.wav")
    await asyncio.to_thread(audio.extract_audio, video_path, wav_path)
    report(8, "Finding the speech")
    # Every stretch of sound is sent; cuts fall at pauses, so each piece's lines can be placed on measured audio
    found = await asyncio.to_thread(vad.analyse, wav_path)
    if not found.sound:
        raise audio.MediaError("The audio is silent: there is nothing to transcribe.")
    pieces = audio.plan_pieces(found.sound, settings.chunk_seconds, cut_at=found.quietest_point)
    for piece in pieces:
        piece.speech = found.timing_stretches(piece.start, piece.end, piece.speech)
    sent = sum(p.end - p.start for p in pieces)
    logger.info("Sending %.1f s of %.1f s of audio in %d pieces (%.1f s heard as speech)", sent, found.total, len(pieces), sum(e - s for s, e in found.speech))

    segments: list[Segment] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=20)) as client:
        for i, piece in enumerate(pieces):
            report(10 + (until - 10) * i // len(pieces), f"Transcribing part {i + 1} of {len(pieces)}")
            if i > 0 and not settings.dev_mock_mode:
                await asyncio.sleep(PAUSE_BETWEEN_PIECES)
            piece_path = os.path.join(workdir, f"piece_{i:04d}.wav")
            await asyncio.to_thread(audio.cut_audio, wav_path, piece.start, piece.end, piece_path)
            transcript = await bodhan.transcribe(client, piece_path, language, i)
            segments += timed_lines(transcript, piece)
    if not segments:
        raise audio.MediaError("No speech was found in the video.")
    return segments


def timed_lines(transcript: bodhan.Transcript, piece: audio.Piece) -> list[Segment]:
    """Best timing available: word timestamps, then segment timestamps, then estimates over the measured speech."""
    if transcript.words:
        return lines_from_words(transcript.words, offset=piece.start)
    if transcript.segments:
        lines = []
        for seg in transcript.segments:
            lines += time_lines(clean_transcript(seg["text"]), [(piece.start + seg["start"], piece.start + seg["end"])])
        return lines
    return time_lines(clean_transcript(transcript.text), piece.speech)
