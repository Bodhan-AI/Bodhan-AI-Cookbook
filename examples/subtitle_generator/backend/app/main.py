"""HTTP API: health, and subtitle jobs (start one from an upload or a link, follow it, list, edit, resume). The pipeline is in jobs.py."""

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, youtube
from .audio import MediaError, find_ffmpeg
from .bodhan import BodhanError, check_keys
from .config import settings
from .download import VIDEO_EXTENSIONS, check_link, name_from_link
from .jobs import can_resume, check_video, resume_job, run_job, run_url_job
from .subtitles import Segment, to_srt

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.fail_unfinished_jobs()
    yield


app = FastAPI(title="Subtitle Generator", lifespan=lifespan)


class JobSummary(BaseModel):
    id: str
    created_at: datetime
    source: str
    source_url: str | None = None
    file_name: str
    language: str
    target_language: str
    status: str
    progress: int
    step: str
    error: str | None = None
    resumable: bool = False  # failed while translating: POST /api/jobs/{id}/resume finishes it


class JobDetail(JobSummary):
    segments: list[Segment] | None = None
    translated_segments: list[Segment] | None = None
    original_srt: str | None = None
    translated_srt: str | None = None


class LinkJob(BaseModel):
    url: str = Field(max_length=2000)
    language: str
    target_language: str = ""
    # YouTube links also need the user's OK in the page ("Allow YouTube links"), each visit
    allow_youtube: bool = False


class LineEdit(BaseModel):
    track: Literal["original", "translated"]
    index: int = Field(ge=0)
    text: str = Field(max_length=500)


def _summary(job: db.Job) -> dict:
    # SQLite hands datetimes back without a timezone; they are stored in UTC
    created = job.created_at if job.created_at.tzinfo else job.created_at.replace(tzinfo=timezone.utc)
    return {**job.model_dump(exclude={"segments", "translated_segments"}), "created_at": created, "resumable": can_resume(job)}


@app.get("/api/health")
def health():
    return {
        "ffmpeg_found": bool(find_ffmpeg()),
        "api_key_set": bool(settings.transcribe_key),
        "dev_mock_mode": settings.dev_mock_mode,
        "max_upload_mb": settings.max_upload_mb,
        "max_video_seconds": settings.max_video_seconds,
        "direct_video_links": settings.direct_video_links,
        "youtube_links": settings.youtube_links,
    }


@app.post("/api/jobs", response_model=JobSummary, status_code=202)
async def create_job(
    background: BackgroundTasks,
    video: Annotated[UploadFile, File()],
    language: Annotated[str, Form()],
    target_language: Annotated[str, Form()] = "",
):
    """Check the upload, then start the job in the background. Follow it with GET /api/jobs/{id}."""
    suffix = Path(video.filename or "").suffix.lower()
    if suffix not in VIDEO_EXTENSIONS:
        raise HTTPException(400, f"Unsupported file type '{suffix}'. Use MP4, MOV, MKV, WEBM, AVI or M4V.")
    language, target_language = language.strip(), target_language.strip()
    _refuse_if_busy()  # before the upload is read, so a refused upload isn't streamed for nothing

    workdir = tempfile.mkdtemp(prefix="subtitles-")
    try:
        check_keys(translate=bool(target_language and target_language != language))
        video_path = os.path.join(workdir, "video" + suffix)
        await _save_upload(video, video_path)
        await asyncio.to_thread(check_video, video_path)
    except BaseException as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        if isinstance(exc, MediaError):
            raise HTTPException(400, str(exc)) from None
        if isinstance(exc, BodhanError):
            raise HTTPException(exc.status, str(exc)) from None
        if isinstance(exc, subprocess.TimeoutExpired):
            raise HTTPException(408, "Could not read the video in time.") from None
        raise
    finally:
        await video.close()

    name = Path(video.filename or "video").name
    try:  # checked again: another job may have started while this upload was saved
        job = _start_one(lambda: db.add_job(db.Job(file_name=name, language=language, target_language=target_language, step="Starting")))
    except HTTPException:
        shutil.rmtree(workdir, ignore_errors=True)
        raise
    # The job owns workdir from here and deletes it when it ends
    background.add_task(run_job, job.id, video_path, workdir, language, target_language)
    return _summary(job)


@app.post("/api/jobs/url", response_model=JobSummary, status_code=202)
async def create_link_job(link: LinkJob, background: BackgroundTasks):
    """
    Start a job from a link: a direct link to a video file (DIRECT_VIDEO_LINKS) or a YouTube video (YOUTUBE_LINKS).
    The video is downloaded in the background, then processed like an upload.
    """
    language, target_language, url = link.language.strip(), link.target_language.strip(), link.url.strip()
    from_youtube = youtube.is_youtube(url)
    if from_youtube and not settings.youtube_links:
        raise HTTPException(403, "YouTube links are turned off. Upload the video file instead.")
    if not from_youtube and not settings.direct_video_links:
        raise HTTPException(403, "Direct video links are turned off. Upload the video file instead.")
    if from_youtube and not link.allow_youtube:
        raise HTTPException(400, "Turn on “Allow YouTube links” first.")
    if not language:
        raise HTTPException(422, "Choose the spoken language.")
    _refuse_if_busy()
    try:
        check_keys(translate=bool(target_language and target_language != language))
        if from_youtube:
            url = youtube.check_link(url)  # the clean watch link is what is downloaded and kept
        else:
            await asyncio.to_thread(check_link, url)
    except MediaError as exc:
        raise HTTPException(400, str(exc)) from None
    except BodhanError as exc:
        raise HTTPException(exc.status, str(exc)) from None

    # The video's title replaces this name once yt-dlp has it
    name = "YouTube video" if from_youtube else name_from_link(url)[:200]
    source = "youtube" if from_youtube else "url"
    # Checked again: another job may have started during the link check
    job = _start_one(
        lambda: db.add_job(db.Job(source=source, source_url=url, file_name=name, language=language, target_language=target_language, step="Starting"))
    )
    workdir = tempfile.mkdtemp(prefix="subtitles-")
    # The job owns workdir from here and deletes it when it ends
    background.add_task(run_url_job, job.id, url, workdir, language, target_language)
    return _summary(job)


def _job_or_404(job_id: str) -> db.Job:
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(404, "No such job.")
    return job


def _refuse_if_busy() -> None:
    """One job at a time for now; several at once is planned for V2."""
    if db.unfinished_job():
        raise HTTPException(409, "Another video is still being processed. Wait for it to finish, then try again.")


# Checking that nothing runs and starting a job must be one step, or two requests can both pass the check. A thread
# lock covers both the async endpoints (event loop) and the sync ones (worker threads).
_one_job = threading.Lock()


def _start_one(start: Callable[[], db.Job]) -> db.Job:
    with _one_job:
        _refuse_if_busy()
        return start()


class JobPage(BaseModel):
    jobs: list[JobSummary]
    total: int  # all jobs, for "11-20 of 37"


@app.get("/api/jobs", response_model=JobPage)
def list_jobs(limit: Annotated[int, Query(ge=1, le=100)] = 10, offset: Annotated[int, Query(ge=0)] = 0):
    """One page of jobs, newest first, without their subtitles, and how many there are in all."""
    return {"jobs": [_summary(job) for job in db.list_jobs(limit, offset)], "total": db.count_jobs()}


@app.get("/api/jobs/{job_id}", response_model=JobDetail)
def get_job(job_id: str):
    """A job's progress; once done, its subtitle lines and SRT files."""
    job = _job_or_404(job_id)
    return _detail(job)


@app.patch("/api/jobs/{job_id}/lines", response_model=JobDetail)
def edit_line(job_id: str, edit: LineEdit):
    """Correct the text of one subtitle line. Its timing is kept; SRT downloads are rebuilt from the saved lines."""
    job = _job_or_404(job_id)
    if job.status in ("queued", "running"):
        raise HTTPException(409, "Wait for the job to finish before editing its subtitles.")
    field = "segments" if edit.track == "original" else "translated_segments"
    lines = getattr(job, field)
    if not lines or None in lines:
        raise HTTPException(400, f"This job has no {edit.track} subtitles to edit.")
    if edit.index >= len(lines):
        raise HTTPException(400, "There is no such subtitle line.")
    text = " ".join(edit.text.split())  # one line of text, without stray spaces or line breaks
    if not text:
        raise HTTPException(400, "A subtitle line can't be empty.")
    lines = list(lines)
    lines[edit.index] = {**lines[edit.index], "text": text}
    db.update_job(job_id, **{field: lines})
    return _detail(_job_or_404(job_id))


def _detail(job: db.Job) -> dict:
    segments = [Segment(**s) for s in job.segments] if job.segments else None
    lines = job.translated_segments
    # A translation stopped halfway (None for lines still to do) is only returned once complete
    translated = [Segment(**s) for s in lines] if lines and None not in lines else None
    return {
        **_summary(job),
        "segments": segments,
        "translated_segments": translated,
        "original_srt": to_srt(segments) if segments else None,
        "translated_srt": to_srt(translated) if translated else None,
    }


@app.post("/api/jobs/{job_id}/resume", response_model=JobSummary, status_code=202)
def resume_job_route(job_id: str, background: BackgroundTasks):
    """Finish a job that failed while translating: only the missing lines are translated. No upload needed."""
    job = _job_or_404(job_id)
    if not can_resume(job):
        raise HTTPException(400, "Only a job that stopped during its translation can be resumed. Use Run again to start it from the beginning.")
    try:
        check_keys(translate=True)
    except BodhanError as exc:
        raise HTTPException(exc.status, str(exc)) from None

    def mark_running() -> db.Job:
        db.update_job(job_id, status="running", error=None, step="Resuming the translation")
        return _job_or_404(job_id)

    job = _start_one(mark_running)
    background.add_task(resume_job, job_id)
    return _summary(job)


async def _save_upload(upload: UploadFile, path: str) -> None:
    """Stream the upload to disk in 1 MB pieces, stopping at the size limit."""
    size, limit = 0, settings.max_upload_mb * 1024 * 1024
    with open(path, "wb") as out:
        while piece := await upload.read(1024 * 1024):
            size += len(piece)
            if size > limit:
                raise HTTPException(413, f"The video is larger than {settings.max_upload_mb} MB.")
            out.write(piece)
    if size == 0:
        raise HTTPException(400, "The uploaded file is empty.")


def serve_web_app(target: FastAPI, directory: str) -> None:
    """Serve the built web app at / (after the API routes, which keep priority). Used by the Docker image."""
    target.mount("/", StaticFiles(directory=directory, html=True), name="web")


if settings.web_dist:
    serve_web_app(app, settings.web_dist)
