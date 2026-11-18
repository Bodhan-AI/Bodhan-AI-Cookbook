"""Tests for helper MCP tools: inspect_media and read_text_file."""

import struct
import wave
from pathlib import Path

import pymupdf

from bodhan_mcp.tools.helpers import (
    inspect_media_file,
    read_text_file_content,
    resolve_secure_path,
)


def create_test_wav(path: Path, duration_seconds: float = 2.0, sample_rate: int = 24000) -> Path:
    """Creates a valid 24 kHz mono 16-bit PCM WAV on disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    num_samples = int(duration_seconds * sample_rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        samples = bytearray()
        for i in range(num_samples):
            val = int(2000 * ((i % 40) / 20.0 - 1.0))
            samples.extend(struct.pack("<h", val))
        wf.writeframes(samples)
    return path


def create_test_pdf(path: Path, pages: int = 2) -> Path:
    """Creates a valid multi-page PDF using PyMuPDF."""
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text((50, 50), f"Test page {i + 1}")
    doc.save(str(path))
    doc.close()
    return path


# ------------------------------------------------------------------------------
# Security & Path Traversal Tests
# ------------------------------------------------------------------------------


def test_resolve_secure_path_prevents_traversal(tmp_path: Path):
    """Path traversal via .. or outside absolute paths is rejected."""
    files_dir = tmp_path / "permitted_files"
    files_dir.mkdir()
    secret_file = tmp_path / "secret.env"
    secret_file.write_text("SECRET=12345", encoding="utf-8")

    # Inside file succeeds
    inside_file = files_dir / "safe.txt"
    inside_file.write_text("Safe content", encoding="utf-8")
    resolved, err = resolve_secure_path("safe.txt", base_dir=files_dir)
    assert err is None
    assert resolved == inside_file.resolve()

    # Traversal via .. is rejected
    resolved, err = resolve_secure_path("../secret.env", base_dir=files_dir)
    assert resolved is None
    assert "Path traversal rejected" in err

    # Absolute path outside base_dir is rejected
    resolved, err = resolve_secure_path(str(secret_file), base_dir=files_dir)
    assert resolved is None
    assert "Path traversal rejected" in err


# ------------------------------------------------------------------------------
# inspect_media Tests
# ------------------------------------------------------------------------------


def test_inspect_media_pdf(tmp_path: Path):
    """Inspects a PDF file returning page count and file size."""
    create_test_pdf(tmp_path / "doc.pdf", pages=3)
    res = inspect_media_file("doc.pdf", base_dir=tmp_path)

    assert res.success is True
    assert res.media_type == "pdf"
    assert res.page_count == 3
    assert res.file_size_bytes > 0
    assert res.format_name == "pdf"


def test_inspect_media_image(tmp_path: Path):
    """Inspects an image returning media_type image and page_count 1."""
    # Create simple 100x100 PNG via PyMuPDF
    img_path = tmp_path / "photo.png"
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 100), 1)
    pix.save(str(img_path))

    res = inspect_media_file("photo.png", base_dir=tmp_path)
    assert res.success is True
    assert res.media_type == "image"
    assert res.page_count == 1
    assert res.format_name == "png"
    assert res.file_size_bytes > 0


def test_inspect_media_audio(tmp_path: Path):
    """Inspects an audio file returning media_type audio and ffprobe duration."""
    create_test_wav(tmp_path / "speech.wav", duration_seconds=2.5)
    res = inspect_media_file("speech.wav", base_dir=tmp_path)

    assert res.success is True
    assert res.media_type == "audio"
    assert res.duration_seconds is not None
    assert abs(res.duration_seconds - 2.5) <= 0.1
    assert res.format_name == "wav"


def test_inspect_media_missing_file_or_traversal(tmp_path: Path):
    """Non-existent file and path traversal fail safely."""
    # Missing file
    res = inspect_media_file("nonexistent.wav", base_dir=tmp_path)
    assert res.success is False
    assert "File not found" in res.error

    # Path traversal
    res = inspect_media_file("../../escape.mp4", base_dir=tmp_path)
    assert res.success is False
    assert "Path traversal rejected" in res.error


# ------------------------------------------------------------------------------
# read_text_file Tests
# ------------------------------------------------------------------------------


def test_read_text_file_allowed_extensions(tmp_path: Path):
    """Reads allowed extensions: .txt, .md, .srt, .csv."""
    files_dir = tmp_path / "files"
    files_dir.mkdir()

    for ext in [".txt", ".md", ".srt", ".csv"]:
        f = files_dir / f"sample{ext}"
        f.write_text(f"Content of sample{ext}", encoding="utf-8")
        res = read_text_file_content(f"sample{ext}", base_dir=files_dir)
        assert res.success is True
        assert res.text == f"Content of sample{ext}"
        assert res.character_count == len(f"Content of sample{ext}")
        assert res.truncated is False


def test_read_text_file_max_chars_truncation(tmp_path: Path):
    """Truncates file content if larger than max_chars."""
    f = tmp_path / "long_text.txt"
    f.write_text("A" * 1500, encoding="utf-8")

    res = read_text_file_content("long_text.txt", max_chars=500, base_dir=tmp_path)
    assert res.success is True
    assert len(res.text) == 500
    assert res.character_count == 500
    assert res.truncated is True


def test_read_text_file_disallowed_extension(tmp_path: Path):
    """Disallowed extensions like .py or .env are rejected."""
    f = tmp_path / "script.py"
    f.write_text("print('hello')", encoding="utf-8")

    res = read_text_file_content("script.py", base_dir=tmp_path)
    assert res.success is False
    assert "not allowed" in res.error


def test_read_text_file_path_traversal(tmp_path: Path):
    """Path traversal outside permitted directory is rejected."""
    res = read_text_file_content("../outside.txt", base_dir=tmp_path)
    assert res.success is False
    assert "Path traversal rejected" in res.error
