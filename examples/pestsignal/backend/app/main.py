import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.config import settings
from app.database.session import engine, SessionLocal, Base
from app.services.seed_service import seed_database
from app.routes.auth_routes import router as auth_router
from app.routes.report_routes import router as report_router
from app.routes.ai_routes import router as ai_router
from app.routes.alert_routes import router as alert_router
from app.routes.analytics_routes import router as analytics_router
from app.routes.notification_routes import router as notification_router
from app.routes.user_routes import router as user_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Create tables and seed initial database
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_database(db)
    finally:
        db.close()
    yield
    # Shutdown

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Multilingual Agricultural Reporting Platform powered by Bodhan AI",
    lifespan=lifespan
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Allow all for local dev & internship demos
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount audio uploads for browser audio playback
uploads_path = os.path.abspath(settings.UPLOAD_DIR)
os.makedirs(uploads_path, exist_ok=True)
app.mount("/uploads/audio", StaticFiles(directory=uploads_path), name="audio_uploads")

# Include Routers
app.include_router(auth_router, prefix=settings.API_V1_PREFIX)
app.include_router(report_router, prefix=settings.API_V1_PREFIX)
app.include_router(ai_router, prefix=settings.API_V1_PREFIX)
app.include_router(alert_router, prefix=settings.API_V1_PREFIX)
app.include_router(analytics_router, prefix=settings.API_V1_PREFIX)
app.include_router(notification_router, prefix=settings.API_V1_PREFIX)
app.include_router(user_router, prefix=settings.API_V1_PREFIX)

@app.get("/api/health", tags=["Health"])
def health_check():
    return {
        "status": "healthy",
        "service": "PestSignal API",
        "version": settings.VERSION,
        "bodhan_api_configured": bool(settings.BODHAN_TRANSCRIBE_API_KEY)
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
