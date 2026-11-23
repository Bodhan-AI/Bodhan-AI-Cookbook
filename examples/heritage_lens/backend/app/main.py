from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    ai, audio, auth, capsules, config, dashboard, derived, discover, documents, health, images, saved, stories,
)
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import register_secret, setup_logging
from app.database.connection import create_db_engine, create_session_factory, init_db
from app.services.bodhan.client import BodhanClient
from app.services.storage.files import FileStore

logger = logging.getLogger("heritagelens")


def create_app(settings: Settings | None = None, *, bodhan_client: BodhanClient | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level)
    for secret in settings.all_secret_values():
        register_secret(secret)

    engine = create_db_engine(settings.database_url)
    init_db(engine)  # dev convenience; use Alembic migrations before production
    owns_client = bodhan_client is None
    client = bodhan_client or BodhanClient.from_settings(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        if owns_client:
            await client.aclose()
        engine.dispose()

    is_prod = settings.app_env == "production"
    app = FastAPI(title="HeritageLens API", version="0.1.0", lifespan=lifespan,
                  docs_url=None if is_prod else "/docs", redoc_url=None, openapi_url=None if is_prod else "/openapi.json")
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.state.file_store = FileStore(settings.storage_dir)
    app.state.bodhan = client

    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
                       allow_headers=["Authorization", "Content-Type"], expose_headers=["X-Request-ID"])

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = request.headers.get("x-request-id", "")[:64] or uuid.uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response
    
    @app.get("/", include_in_schema=False)
    def root():
        return {"app": "HeritageLens API", "health": "/api/health", "docs": "/docs",
                "frontend": "Open http://localhost:5173 for the website."}

    register_exception_handlers(app)
    for router in (health.router, config.router, auth.router, discover.router, stories.router, audio.router,
                   documents.router, images.router, capsules.router, saved.router, dashboard.router,
                   ai.router, derived.router):
        app.include_router(router, prefix="/api")
    return app


# Run with:  uvicorn app.main:create_app --factory --reload
