"""Configuration for Bodhan MCP & Agent.

Reads credentials strictly from environment variables (or .env file):
  BODHAN_TRANSCRIBE_KEY, BODHAN_TRANSLATE_KEY, BODHAN_SPEAK_KEY, BODHAN_OCR_KEY
  BODHAN_BASE_URL (defaults to https://api.bodhan.ai/v1)
  LLM_BASE_URL, LLM_MODEL, LLM_API_KEY, LLM_REASONING_EFFORT
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import find_dotenv, load_dotenv


def ensure_utf8_streams() -> None:
    """Reconfigure stdout/stderr to UTF-8 on Windows."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream and hasattr(stream, "reconfigure"):
            try:
                if getattr(stream, "encoding", "").lower() != "utf-8":
                    stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def ensure_windows_asyncio_patch() -> None:
    """Apply sandbox-safe asyncio proactor pipe patch on Windows if loopback socket connection fails."""
    if sys.platform != "win32":
        return

    import asyncio.proactor_events as pe
    import socket

    needs_patch = False
    try:
        test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        test_sock.bind(("127.0.0.1", 0))
        test_sock.listen(1)
        test_port = test_sock.getsockname()[1]
        conn_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        conn_sock.settimeout(0.1)
        try:
            conn_sock.connect(("127.0.0.1", test_port))
        except (OSError, TimeoutError):
            needs_patch = True
        finally:
            conn_sock.close()
            test_sock.close()
    except Exception:
        needs_patch = True

    if needs_patch:
        class _PipeSocket:
            def __init__(self, fd: int) -> None:
                self._fd: int | None = fd

            def fileno(self) -> int:
                return self._fd if self._fd is not None else -1

            def setblocking(self, _blocking: bool) -> None:
                pass

            def close(self) -> None:
                if self._fd is not None:
                    try:
                        os.close(self._fd)
                    except OSError:
                        pass
                    self._fd = None

            def send(self, data: bytes) -> int:
                if self._fd is not None:
                    return os.write(self._fd, data)
                return 0

        def _sandbox_safe_make_self_pipe(self: pe.BaseProactorEventLoop) -> None:
            r, w = os.pipe()
            self._ssock = _PipeSocket(r)  # type: ignore[assignment]
            self._csock = _PipeSocket(w)  # type: ignore[assignment]
            self._internal_fds += 1

        pe.BaseProactorEventLoop._make_self_pipe = _sandbox_safe_make_self_pipe
        pe.BaseProactorEventLoop._loop_self_reading = lambda self, f=None: None


# Automatically configure streams and Windows asyncio patch at module load
ensure_utf8_streams()
ensure_windows_asyncio_patch()



@dataclass(frozen=True)
class BodhanConfig:
    """Unified configuration loaded from environment variables."""

    transcribe_key: str = ""
    translate_key: str = ""
    speak_key: str = ""
    ocr_key: str = ""
    base_url: str = "https://api.bodhan.ai/v1"
    llm_base_url: str = ""
    llm_model: str = "gpt-4o"
    llm_api_key: str = ""
    llm_reasoning_effort: str = ""

    @classmethod
    def from_env(cls, load_env_file: bool = True) -> BodhanConfig:
        """Load configuration from environment variables and optional .env file."""
        if load_env_file:
            env_file = find_dotenv(usecwd=True)
            if not env_file:
                recipe_env = Path(__file__).resolve().parent.parent.parent / ".env"
                if recipe_env.exists():
                    env_file = str(recipe_env)
            if env_file:
                load_dotenv(env_file, override=False)

        return cls(
            transcribe_key=os.getenv("BODHAN_TRANSCRIBE_KEY", ""),
            translate_key=os.getenv("BODHAN_TRANSLATE_KEY", ""),
            speak_key=os.getenv("BODHAN_SPEAK_KEY", ""),
            ocr_key=os.getenv("BODHAN_OCR_KEY", ""),
            base_url=os.getenv("BODHAN_BASE_URL", "https://api.bodhan.ai/v1").rstrip("/"),
            llm_base_url=os.getenv("LLM_BASE_URL", "").rstrip("/"),
            llm_model=os.getenv("LLM_MODEL", "gpt-4o"),
            llm_api_key=os.getenv("LLM_API_KEY", ""),
            llm_reasoning_effort=os.getenv("LLM_REASONING_EFFORT", ""),
        )

    @classmethod
    def load(cls, load_env_file: bool = True) -> BodhanConfig:
        """Alias for from_env for notebook compatibility."""
        return cls.from_env(load_env_file=load_env_file)

    def snapshot(self) -> dict:
        """Redacted, report-safe view of the configuration.

        Never includes any key material — only base URLs, the model name, and
        boolean key-presence flags, so it can be embedded in report.json.
        """
        return {
            "base_url": self.base_url,
            "llm_base_url": self.llm_base_url,
            "llm_model": self.llm_model,
            "llm_reasoning_effort": self.llm_reasoning_effort or None,
            "has_bodhan_keys": bool(
                self.transcribe_key or self.translate_key or self.speak_key or self.ocr_key
            ),
            "has_llm_key": bool(self.llm_api_key),
        }


def load_config(load_env_file: bool = True) -> BodhanConfig:
    """Convenience functional loader for notebook compatibility."""
    return BodhanConfig.from_env(load_env_file=load_env_file)

