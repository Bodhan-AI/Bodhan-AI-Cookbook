"""The database: one `jobs` table. SQLite by default; set DATABASE_URL in .env to use another database."""

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, inspect
from sqlalchemy.engine import Engine
from sqlmodel import JSON, Column, Field, Session, SQLModel, col, create_engine, select

from .config import BACKEND_DIR, settings

DEFAULT_DATABASE = BACKEND_DIR / "data" / "subtitles.db"


class Job(SQLModel, table=True):
    """One video's subtitle job. Only text is kept: the video and its audio are deleted when the job ends."""

    __tablename__ = "jobs"  # pyright: ignore[reportAssignmentType]  # SQLModel types this as a declared_attr

    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)
    source: str = "upload"  # "upload", "url" (direct link to a video file) or "youtube"
    source_url: str | None = None  # the pasted link ("url"), or the video's clean watch link ("youtube")
    file_name: str
    language: str
    target_language: str = ""
    status: str = "queued"  # queued, running, done, failed
    progress: int = 0  # percent
    step: str = ""
    error: str | None = None
    segments: list[dict] | None = Field(default=None, sa_column=Column(JSON))
    translated_segments: list[dict] | None = Field(default=None, sa_column=Column(JSON))


_engines: dict[str, Engine] = {}

# Columns added after the first version, so databases created earlier get them too (no migration tool yet)
ADDED_COLUMNS = {"source_url": "VARCHAR"}


def engine() -> Engine:
    """The engine for the configured database, creating the database and table on first use."""
    url = settings.database_url or f"sqlite:///{DEFAULT_DATABASE.as_posix()}"
    if url not in _engines:
        connect_args = {}
        if url.startswith("sqlite:///"):
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
            connect_args = {"check_same_thread": False}  # jobs update their row from worker threads
        _engines[url] = create_engine(url, connect_args=connect_args)
        SQLModel.metadata.create_all(_engines[url])
        _add_missing_columns(_engines[url])
    return _engines[url]


def _add_missing_columns(engine: Engine) -> None:
    existing = {column["name"] for column in inspect(engine).get_columns("jobs")}
    with engine.begin() as connection:
        for name, kind in ADDED_COLUMNS.items():
            if name not in existing:
                connection.exec_driver_sql(f"ALTER TABLE jobs ADD COLUMN {name} {kind}")


def add_job(job: Job) -> Job:
    with Session(engine(), expire_on_commit=False) as session:
        session.add(job)
        session.commit()
        return job


def get_job(job_id: str) -> Job | None:
    with Session(engine()) as session:
        return session.get(Job, job_id)


def list_jobs(limit: int = 50, offset: int = 0) -> list[Job]:
    """Jobs newest first, one page at a time."""
    with Session(engine()) as session:
        return list(session.exec(select(Job).order_by(col(Job.created_at).desc()).offset(offset).limit(limit)))


def count_jobs() -> int:
    with Session(engine()) as session:
        return session.exec(select(func.count()).select_from(Job)).one()


def unfinished_job() -> Job | None:
    """The job that is queued or running, if any."""
    with Session(engine()) as session:
        return session.exec(select(Job).where(col(Job.status).in_(["queued", "running"]))).first()


def update_job(job_id: str, **fields) -> None:
    with Session(engine()) as session:
        job = session.get(Job, job_id)
        for name, value in fields.items():
            setattr(job, name, value)
        session.commit()


def fail_unfinished_jobs() -> None:
    """At startup: jobs that were queued or running when the server stopped. Their video is gone; saved text is kept."""
    with Session(engine()) as session:
        for job in session.exec(select(Job).where(col(Job.status).in_(["queued", "running"]))):
            job.status = "failed"
            if job.segments:
                job.error = "The server stopped during the translation. The subtitles are saved; resume to finish the translation."
            elif job.source == "upload":
                job.error = "The server stopped before the transcription finished. Choose the video again to run it again."
            else:
                job.error = "The server stopped before the transcription finished. Use Run again to start it from the link."
        session.commit()
