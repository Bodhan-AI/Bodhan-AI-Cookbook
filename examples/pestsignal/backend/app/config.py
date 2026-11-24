import os
from pathlib import Path
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

class Settings(BaseSettings):
    PROJECT_NAME: str = "PestSignal API"
    VERSION: str = "1.0.0"
    API_V1_PREFIX: str = "/api"

    # Bodhan AI
    BODHAN_TRANSCRIBE_API_KEY: str = os.getenv("BODHAN_TRANSCRIBE_API_KEY", "")
    BODHAN_TRANSLATE_API_KEY: str = os.getenv("BODHAN_TRANSLATE_API_KEY", "")
    BODHAN_BASE_URL: str = os.getenv("BODHAN_BASE_URL", "https://api.bodhan.ai")

    # Security
    SECRET_KEY: str = os.getenv("SECRET_KEY", "pestsignal-secret-key-production-internship-2026")
    ALGORITHM: str = os.getenv("ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))

    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR}/pestsignal.db")

    # Storage
    UPLOAD_DIR: str = os.getenv("UPLOAD_DIR", str(BASE_DIR / "uploads" / "audio"))
    MAX_AUDIO_SIZE_BYTES: int = 15 * 1024 * 1024  # 15 MB limit

    # CORS
    CORS_ORIGINS: str = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    class Config:
        case_sensitive = True
        extra = "allow"

settings = Settings()

# Ensure upload directory exists
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
