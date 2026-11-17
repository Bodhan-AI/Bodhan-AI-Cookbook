from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, "").strip() or default).expanduser().resolve()


def _paths(name: str) -> tuple[Path, ...]:
    raw = os.environ.get(name, "")
    return tuple(Path(part.strip()).expanduser().resolve() for part in raw.split(os.pathsep) if part.strip())


def _cache_dir() -> Path | None:
    if os.environ.get("BODHAN_CACHE", "on").strip().lower() in {"off", "0", "false", "no"}:
        return None
    return _path("BODHAN_CACHE_DIR", "./.cache/bodhan")


@dataclass(frozen=True)
class Settings:
    base_url: str
    input_dir: Path
    output_dir: Path
    allowed_dirs: tuple[Path, ...]
    max_concurrency: int
    requests_per_minute: int
    timeout_s: float
    max_retries: int
    translate_chunk_chars: int
    translate_parallel_chunks: int
    tool_deadline_s: float
    max_input_chars: int
    inline_text_limit: int
    transcribe_max_duration_s: float
    transcribe_max_file_mb: int
    speak_requests_per_minute: int
    ocr_max_pages: int
    cache_dir: Path | None = None
    cache_ttl_s: float = 7 * 86_400.0

    def __post_init__(self) -> None:
        checks = {
            "input_dir": self.input_dir.is_absolute(),
            "output_dir": self.output_dir.is_absolute(),
            "allowed_dirs": all(p.is_absolute() for p in self.allowed_dirs),
            "max_concurrency": self.max_concurrency >= 1,
            "requests_per_minute": self.requests_per_minute >= 1,
            "timeout_s": self.timeout_s > 0,
            "max_retries": self.max_retries >= 0,
            "translate_chunk_chars": self.translate_chunk_chars >= 1,
            "translate_parallel_chunks": self.translate_parallel_chunks >= 1,
            "tool_deadline_s": self.tool_deadline_s > 0,
            "max_input_chars": self.max_input_chars >= 1,
            "inline_text_limit": self.inline_text_limit >= 0,
            "transcribe_max_duration_s": self.transcribe_max_duration_s > 0,
            "transcribe_max_file_mb": self.transcribe_max_file_mb >= 1,
            "speak_requests_per_minute": self.speak_requests_per_minute >= 1,
            "ocr_max_pages": self.ocr_max_pages >= 1,
            "cache_dir": self.cache_dir is None or self.cache_dir.is_absolute(),
            "cache_ttl_s": self.cache_ttl_s > 0,
        }
        invalid = [name for name, ok in checks.items() if not ok]
        if invalid:
            raise ValueError(f"Invalid settings: {', '.join(invalid)}")

    @property
    def readable_roots(self) -> tuple[Path, ...]:
        return (self.input_dir, self.output_dir, *self.allowed_dirs)

    @property
    def writable_roots(self) -> tuple[Path, ...]:
        return (self.output_dir,)

    @staticmethod
    def from_env() -> Settings:
        return Settings(
            base_url=os.environ.get("BODHAN_BASE_URL", "https://api.bodhan.ai").rstrip("/"),
            input_dir=_path("BODHAN_MCP_INPUT_DIR", "./inputs"),
            output_dir=_path("BODHAN_MCP_OUTPUT_DIR", "./outputs/mcp"),
            allowed_dirs=_paths("BODHAN_MCP_ALLOWED_DIRS"),
            max_concurrency=max(1, _int("BODHAN_MAX_CONCURRENCY", 4)),
            requests_per_minute=max(1, _int("BODHAN_REQUESTS_PER_MINUTE", 100)),
            timeout_s=max(1.0, _float("BODHAN_TIMEOUT_S", 60.0)),
            max_retries=max(0, _int("BODHAN_MAX_RETRIES", 3)),
            translate_chunk_chars=max(200, _int("BODHAN_TRANSLATE_CHUNK_CHARS", 2000)),
            translate_parallel_chunks=max(1, _int("BODHAN_TRANSLATE_PARALLEL_CHUNKS", 4)),
            tool_deadline_s=max(1.0, _float("BODHAN_TOOL_DEADLINE_S", 240.0)),
            max_input_chars=max(1000, _int("BODHAN_MAX_INPUT_CHARS", 200_000)),
            inline_text_limit=max(0, _int("BODHAN_INLINE_TEXT_LIMIT", 4000)),
            transcribe_max_duration_s=max(1.0, _float("BODHAN_TRANSCRIBE_MAX_DURATION_S", 1800.0)),
            transcribe_max_file_mb=max(1, _int("BODHAN_TRANSCRIBE_MAX_FILE_MB", 200)),
            speak_requests_per_minute=max(1, _int("BODHAN_SPEAK_REQUESTS_PER_MINUTE", 6)),
            ocr_max_pages=max(1, _int("BODHAN_OCR_MAX_PAGES", 20)),
            cache_dir=_cache_dir(),
            cache_ttl_s=max(0.01, _float("BODHAN_CACHE_TTL_DAYS", 7.0)) * 86_400.0,
        )

    @property
    def transcribe_max_file_bytes(self) -> int:
        return self.transcribe_max_file_mb * 1024 * 1024


API_KEY_ENV = {
    "transcribe": "BODHAN_TRANSCRIBE_KEY",
    "translate": "BODHAN_TRANSLATE_KEY",
    "speak": "BODHAN_SPEAK_KEY",
    "ocr": "BODHAN_OCR_KEY",
}


SHARED_API_KEY_ENV = "BODHAN_API_KEY"


def api_key(model: str) -> str | None:
    for name in (API_KEY_ENV[model], SHARED_API_KEY_ENV):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def api_key_hint(model: str) -> str:
    return f"{SHARED_API_KEY_ENV} (or {API_KEY_ENV[model]})"
