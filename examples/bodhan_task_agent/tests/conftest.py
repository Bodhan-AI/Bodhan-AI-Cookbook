from __future__ import annotations

import io
import math
import shutil
import struct
import subprocess
import wave
from pathlib import Path

import pytest

from bodhan_mcp.config import Settings

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
HAS_POPPLER = bool(shutil.which("pdfinfo") and shutil.which("pdftoppm"))
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg and ffprobe are required")
needs_poppler = pytest.mark.skipif(not HAS_POPPLER, reason="pdfinfo and pdftoppm are required")

KEY_ENV = ("BODHAN_TRANSCRIBE_KEY", "BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY", "BODHAN_OCR_KEY")


def make_settings(root: Path, **overrides: object) -> Settings:
    input_dir = root / "inputs"
    output_dir = root / "outputs"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    values: dict[str, object] = {
        "base_url": "https://api.test",
        "input_dir": input_dir,
        "output_dir": output_dir,
        "allowed_dirs": (),
        "max_concurrency": 4,
        "requests_per_minute": 10_000,
        "timeout_s": 5.0,
        "max_retries": 2,
        "translate_chunk_chars": 2000,
        "translate_parallel_chunks": 4,
        "tool_deadline_s": 60.0,
        "max_input_chars": 200_000,
        "inline_text_limit": 4000,
        "transcribe_max_duration_s": 1800.0,
        "transcribe_max_file_mb": 200,
        "speak_requests_per_minute": 10_000,
        "ocr_max_pages": 20,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture(autouse=True)
def api_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BODHAN_API_KEY", raising=False)
    for name in KEY_ENV:
        monkeypatch.setenv(name, "test-key")


async def no_sleep(_: float) -> None:
    return None


def make_audio(path: Path, duration_s: float, quiet: list[tuple[float, float]] | None = None) -> Path:
    muted = "+".join(f"between(t,{start},{end})" for start, end in quiet or []) or "0"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", f"aevalsrc='if({muted},0,0.5*sin(2*PI*440*t))':s=16000:d={duration_s}", "-ac", "1", str(path)],
        check=True,
    )  # fmt: skip
    return path


def speech_wav(seconds: float = 1.0, rate: int = 24_000) -> bytes:
    frames = b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * 220 * i / rate))) for i in range(int(seconds * rate))
    )
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(frames)
    return buffer.getvalue()


def make_pdf(path: Path, pages: int) -> Path:
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{3 + i * 2} 0 R" for i in range(pages))
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode())
    for i in range(pages):
        content = f"BT /F1 24 Tf 72 720 Td (Page {i + 1}) Tj ET".encode()
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {4 + i * 2} 0 R "
            f"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> >>".encode()
        )
        objects.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % number + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref))
    path.write_bytes(out.getvalue())
    return path


def make_png(path: Path, width: int = 64, height: int = 48) -> Path:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=white:s={width}x{height}", "-frames:v", "1",
         str(path)],
        check=True,
    )  # fmt: skip
    return path
