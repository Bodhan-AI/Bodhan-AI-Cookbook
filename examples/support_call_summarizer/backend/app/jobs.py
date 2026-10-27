"""Background jobs and the in-memory job store.

The upload returns a ``call_id`` at once and the work runs in the background, so
a dropped mobile connection does not lose the call: the app just polls again.
Results live in memory for ``result_ttl_seconds`` (default 1 hour) and the
uploaded audio is deleted as soon as processing ends, even on failure (FR-12).
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import shutil
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .analysis import CallAnalyzer
from .audio import AudioError
from .bodhan_client import BodhanClient
from .config import Settings
from .diarization import Diarizer
from .models import CallRecord, CallStatus, JobError, JobView, Progress
from .pipeline import CallInput, PipelineError, process_call

log = logging.getLogger(__name__)


def new_call_id() -> str:
    # Without login, knowing the id is what grants access to a transcript, so it must not be guessable.
    return secrets.token_hex(8)


@dataclass
class Job:
    call_id: str
    workdir: Path
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    status: CallStatus = CallStatus.queued
    progress: Progress = field(default_factory=Progress)
    record: CallRecord | None = None
    error: JobError | None = None
    finished_at: datetime | None = None

    def view(self, ttl: int) -> JobView:
        anchor = self.finished_at or datetime.now(UTC)
        return JobView(
            call_id=self.call_id,
            status=self.status,
            progress=self.progress,
            error=self.error,
            record=self.record,
            created_at=self.created_at,
            expires_at=anchor + timedelta(seconds=ttl),
        )


class JobStore:
    def __init__(
        self,
        settings: Settings,
        client: BodhanClient,
        diarizer: Diarizer | None = None,
        analyzer: CallAnalyzer | None = None,
    ):
        self.settings = settings
        self.client = client
        self.diarizer = diarizer
        self.analyzer = analyzer
        self._jobs: dict[str, Job] = {}
        self._tasks: set[asyncio.Task] = set()
        self._slots = asyncio.Semaphore(max(1, settings.max_concurrent_jobs))

    def get(self, call_id: str) -> Job | None:
        self.sweep()
        return self._jobs.get(call_id)

    def submit(self, job: Job, call: CallInput) -> None:
        self._jobs[job.call_id] = job
        task = asyncio.create_task(self._run(job, call))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, job: Job, call: CallInput) -> None:
        started = time.monotonic()
        try:
            async with self._slots:
                job.status = CallStatus.processing

                def on_progress(p: Progress) -> None:
                    job.progress = p

                record = await process_call(
                    call,
                    client=self.client,
                    settings=self.settings,
                    workdir=job.workdir,
                    diarizer=self.diarizer,
                    analyzer=self.analyzer,
                    on_progress=on_progress,
                )
                job.record, job.status = record, record.status
        except (AudioError, PipelineError) as exc:
            job.status = CallStatus.failed
            job.error = JobError(code=exc.code, message=exc.message)
            job.record = getattr(exc, "record", None)
        except asyncio.CancelledError:
            job.status = CallStatus.failed
            job.error = JobError(code="cancelled", message="The server stopped before the call was processed.")
            raise
        except Exception:
            log.exception("call %s crashed", job.call_id)
            job.status = CallStatus.failed
            job.error = JobError(code="internal_error", message="Something went wrong while processing the call.")
        finally:
            shutil.rmtree(job.workdir, ignore_errors=True)
            job.finished_at = datetime.now(UTC)
            log.info("call %s %s in %.1fs", job.call_id, job.status.value, time.monotonic() - started)

    def sweep(self) -> None:
        cutoff = datetime.now(UTC) - timedelta(seconds=self.settings.result_ttl_seconds)
        for call_id in [cid for cid, j in self._jobs.items() if j.finished_at and j.finished_at < cutoff]:
            del self._jobs[call_id]

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
