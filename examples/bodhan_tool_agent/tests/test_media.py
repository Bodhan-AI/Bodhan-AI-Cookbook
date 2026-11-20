from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

import pytest
from PIL import Image

from bodhan_mcp.errors import ToolError
from bodhan_mcp.media import (
    audio_duration,
    convert_wav_to_mp3,
    image_pages,
    inspect_media_file,
    split_audio,
    split_text,
    tts_chunks,
)


def _write_wav(path: Path, duration: float) -> None:
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\x00\x00" * int(8000 * duration))


def test_audio_chunks_do_not_exceed_transcribe_limit(tmp_path: Path) -> None:
    path = tmp_path / "long.wav"
    _write_wav(path, 65)
    chunks = list(split_audio(path))
    try:
        assert [round(duration) for _, duration in chunks] == [30, 30, 5]
        assert all(duration <= 30 for _, duration in chunks)
        assert [round(audio_duration(path)) for path, _ in chunks] == [30, 30, 5]
    finally:
        for chunk, _ in chunks:
            chunk.unlink(missing_ok=True)


def test_translate_chunks_preserve_all_source_text() -> None:
    source = ("A paragraph about a language task.\n\n" * 300)
    chunks = split_text(source, 2500)
    assert all(len(chunk) <= 2500 for chunk in chunks)
    assert "\n\n".join(chunks).replace("\n\n", " ").split() == source.split()


def test_tts_chunks_are_bounded_and_keep_word_order() -> None:
    source = ("This is a sentence with a few words. " * 80).strip()
    chunks = tts_chunks(source, 80)
    assert all(len(chunk) <= 80 for chunk in chunks)
    assert " ".join(chunks).split() == source.split()


def test_empty_translation_is_rejected() -> None:
    with pytest.raises(ToolError, match="must not be empty"):
        split_text("  ", 100)


def test_webp_page_is_converted_to_png_for_ocr(tmp_path: Path) -> None:
    path = tmp_path / "worksheet.webp"
    Image.new("RGB", (4, 3), "white").save(path, format="WEBP")
    page_number, image_bytes, mime_type = next(image_pages(path))
    assert page_number == 1
    assert image_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert mime_type == "image/png"


def test_wav_to_mp3_conversion_produces_readable_audio(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    destination = tmp_path / "speech.mp3"
    sample_rate = 24000
    with wave.open(str(source), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        samples = (
            struct.pack("<h", int(7000 * math.sin(2 * math.pi * 440 * index / sample_rate)))
            for index in range(sample_rate)
        )
        audio.writeframes(b"".join(samples))

    convert_wav_to_mp3(source, destination)

    assert destination.is_file()
    assert destination.stat().st_size > 0
    assert audio_duration(destination) > 0


def test_media_inspection_reports_audio_metadata(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    _write_wav(source, 2)

    inspection = inspect_media_file(source)

    assert inspection == {
        "status": "ok",
        "kind": "audio",
        "format": "WAV/PCM",
        "duration_seconds": 2.0,
        "sample_rate_hz": 8000,
        "channels": 1,
    }
