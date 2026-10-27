"""FastAPI app: upload a call, poll its progress, export the result.

Run locally with ``uvicorn app.main:app --reload`` from the ``backend`` folder.
API docs are served at /docs.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import shutil
import tempfile
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import audio
from .analysis import CallAnalyzer, build_analyzer
from .bodhan_client import BodhanClient
from .config import ALLOWED_EXTENSIONS, Settings, get_settings
from .diarization import Diarizer, build_diarizer
from .exporters import export
from .jobs import Job, JobStore, new_call_id
from .languages import all_languages, is_supported
from .models import AppConfig, CreateCallResponse, ErrorResponse, JobView, LanguagesResponse
from .pipeline import CallInput

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("support_call_summarizer")

UPLOAD_CHUNK = 1024 * 1024
UPLOAD_OVERHEAD = 64 * 1024  # multipart headers and form fields around the file
MAX_TEXT_FIELD = 100


class ApiError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(status_code=status_code, detail={"code": code, "message": message})


def create_app(
    settings: Settings | None = None,
    client: BodhanClient | None = None,
    diarizer: Diarizer | None = None,
    analyzer: CallAnalyzer | None = None,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        bodhan = client or BodhanClient(
            settings.transcribe_key,
            settings.translate_key,
            base_url=settings.bodhan_base_url,
            timeout=settings.bodhan_timeout_seconds,
            max_retries=settings.bodhan_max_retries,
            concurrency=settings.bodhan_concurrency,
            rpm=settings.bodhan_rpm,
        )
        # loading the speaker model takes seconds (minutes on its first download), so keep the loop free
        diar = diarizer if diarizer is not None else await asyncio.to_thread(build_diarizer, settings)
        if client is None and not (settings.transcribe_key and settings.translate_key):
            log.warning("A Bodhan API key is missing (see .env.example): calls will fail until it is set.")
        analysis = analyzer if analyzer is not None else build_analyzer(settings)
        if analysis is None:
            log.info("Neither SARVAM_API_KEY nor ANTHROPIC_API_KEY is set: calls get no AI summary or sentiment.")
        store = JobStore(settings, bodhan, diar, analysis)
        app.state.store = store
        sweeper = asyncio.create_task(_sweep_forever(store))
        try:
            yield
        finally:
            sweeper.cancel()
            await store.shutdown()
            await bodhan.aclose()
            if diar is not None:
                await diar.aclose()
            if analysis is not None:
                await analysis.aclose()

    app = FastAPI(
        title="Support Call Summarizer",
        version="0.1.0",
        description=(
            "Transcribe Indian-language support calls with Bodhan, translate them to English,"
            " and summarize them with Sarvam (Claude as the fallback)."
        ),
        lifespan=lifespan,
        responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    )
    app.state.settings = settings

    @app.middleware("http")
    async def reject_oversized_uploads(request: Request, call_next):
        # Starlette reads the whole multipart body before the route runs, so refuse
        # declared-too-large uploads up front instead of receiving them first.
        if request.method == "POST" and request.url.path == "/api/calls":
            limit = settings.max_file_mb * 1_000_000 + UPLOAD_OVERHEAD
            length = request.headers.get("content-length", "")
            if length.isdigit() and int(length) > limit:
                message = f"The file is larger than the {settings.max_file_mb} MB limit."
                return JSONResponse({"error": {"code": "file_too_large", "message": message}}, status_code=413)
        return await call_next(request)

    # Added last so it is the outermost layer and also covers the 413 above.
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST"],
            allow_headers=["X-App-Key", "Content-Type"],
            expose_headers=["Content-Disposition"],
        )

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException):
        detail = (
            exc.detail
            if isinstance(exc.detail, dict)
            else {"code": f"http_{exc.status_code}", "message": str(exc.detail)}
        )
        return JSONResponse({"error": detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(p) for p in first.get("loc", [])[1:]) or "request"
        return JSONResponse(
            {"error": {"code": "invalid_request", "message": f"{field}: {first.get('msg', 'invalid value')}"}},
            status_code=422,
        )

    @app.exception_handler(audio.AudioError)
    async def audio_error(_request: Request, exc: audio.AudioError):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status_code)

    def require_app_key(x_app_key: Annotated[str | None, Header()] = None) -> None:
        if settings.app_key and not secrets.compare_digest(x_app_key or "", settings.app_key):
            raise ApiError(401, "unauthorized", "Missing or wrong app key.")

    def store_of(request: Request) -> JobStore:
        return request.app.state.store

    api = [Depends(require_app_key)]

    @app.get("/health", tags=["meta"])
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/config", response_model=AppConfig, dependencies=api, tags=["meta"])
    async def config(request: Request) -> AppConfig:
        return AppConfig(
            max_file_mb=settings.max_file_mb,
            max_duration_seconds=settings.max_duration_seconds,
            allowed_extensions=list(ALLOWED_EXTENSIONS),
            # what actually loaded, not what .env asked for: 'nemotron' turns itself off without a usable GPU
            diarization_enabled=request.app.state.store.diarizer is not None,
            analysis_enabled=request.app.state.store.analyzer is not None,
            max_speakers=settings.max_speakers,
            result_ttl_seconds=settings.result_ttl_seconds,
        )

    @app.get("/api/languages", response_model=LanguagesResponse, dependencies=api, tags=["meta"])
    async def languages() -> LanguagesResponse:
        return LanguagesResponse(languages=all_languages())

    @app.post("/api/calls", status_code=202, response_model=CreateCallResponse, dependencies=api, tags=["calls"])
    async def create_call(
        request: Request,
        file: Annotated[UploadFile, File(description="WAV, MP3, M4A, AAC, AMR, OGG, FLAC or WebM")],
        language: Annotated[str | None, Form()] = None,
        ticket_id: Annotated[str | None, Form(max_length=MAX_TEXT_FIELD)] = None,
        agent_name: Annotated[str | None, Form(max_length=MAX_TEXT_FIELD)] = None,
        store: JobStore = Depends(store_of),
    ) -> CreateCallResponse:
        language = (language or "").strip() or None
        if language and not is_supported(language):
            raise ApiError(400, "invalid_language", f"Language '{language}' is not supported.")
        max_bytes = settings.max_file_mb * 1_000_000
        source_file = Path(file.filename or "call").name[:200] or "call"

        workdir = Path(tempfile.mkdtemp(prefix="scs_"))
        try:
            upload = workdir / ("upload" + Path(source_file).suffix.lower())
            await _save_upload(file, upload, max_bytes)
            audio.validate_upload(upload, source_file, allowed_extensions=ALLOWED_EXTENSIONS, max_bytes=max_bytes)
            probed = await asyncio.to_thread(audio.probe, upload, settings.ffprobe_path)
            if probed.duration is not None:  # otherwise checked after conversion
                audio.check_duration(probed.duration, settings.max_duration_seconds)
        except BaseException:
            shutil.rmtree(workdir, ignore_errors=True)
            raise

        call_id = new_call_id()
        store.submit(
            Job(call_id=call_id, workdir=workdir),
            CallInput(
                call_id=call_id,
                audio_path=upload,
                source_file=source_file,
                language=language,
                ticket_id=(ticket_id or "").strip() or None,
                agent_name=(agent_name or "").strip() or None,
            ),
        )
        log.info("call %s queued (%.1f MB)", call_id, upload.stat().st_size / 1e6)
        return CreateCallResponse(call_id=call_id, status=store.get(call_id).status)

    def find_job(call_id: str, store: JobStore) -> Job:
        job = store.get(call_id)
        if job is None:
            raise ApiError(404, "not_found", "This call was not found. Results are kept for a limited time only.")
        return job

    @app.get("/api/calls/{call_id}", response_model=JobView, dependencies=api, tags=["calls"])
    async def get_call(call_id: str, store: JobStore = Depends(store_of)) -> JobView:
        return find_job(call_id, store).view(settings.result_ttl_seconds)

    @app.get("/api/calls/{call_id}/export", dependencies=api, tags=["calls"])
    async def export_call(
        call_id: str,
        format: Annotated[Literal["json", "calls_csv", "segments_csv"], Query()] = "json",
        store: JobStore = Depends(store_of),
    ) -> Response:
        job = find_job(call_id, store)
        if job.record is None:
            raise ApiError(409, "not_ready", "The call has not finished processing yet.")
        body, media_type, filename = export(job.record, format)
        return Response(
            body,
            media_type=media_type,
            headers={"Content-Disposition": f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"},
        )

    if settings.web_dist_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.web_dist_dir, html=True), name="web")

    return app


async def _save_upload(file: UploadFile, dest: Path, max_bytes: int) -> None:
    size = 0
    with dest.open("wb") as out:
        while chunk := await file.read(UPLOAD_CHUNK):
            size += len(chunk)
            if size > max_bytes:
                raise audio.AudioError(
                    "file_too_large", f"The file is larger than the {max_bytes // 1_000_000} MB limit.", 413
                )
            out.write(chunk)


async def _sweep_forever(store: JobStore) -> None:
    while True:
        await asyncio.sleep(60)
        with suppress(Exception):
            store.sweep()


app = create_app()
