"""Unit tests for audio chunking and media utilities."""

import os
import shutil
import struct
import tempfile
from pathlib import Path

import pytest

from bodhan_mcp.media import (
    chunk_audio,
    get_audio_duration,
    inspect_file,
    detect_file_type,
    is_audio,
    is_image,
    is_pdf,
    is_text,
    MAX_AUDIO_CHUNK_SECONDS,
)


def _create_wav(path: str, duration_seconds: float = 5.0, sample_rate: int = 16000):
    """Create a minimal valid WAV file for testing."""
    channels = 1
    bits_per_sample = 16
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    num_samples = int(sample_rate * duration_seconds)
    data_size = num_samples * block_align

    with open(path, "wb") as f:
        # RIFF header
        f.write(b"RIFF")
        f.write(struct.pack("<I", 36 + data_size))
        f.write(b"WAVE")
        # fmt chunk
        f.write(b"fmt ")
        f.write(struct.pack("<I", 16))
        f.write(struct.pack("<H", 1))  # PCM
        f.write(struct.pack("<H", channels))
        f.write(struct.pack("<I", sample_rate))
        f.write(struct.pack("<I", byte_rate))
        f.write(struct.pack("<H", block_align))
        f.write(struct.pack("<H", bits_per_sample))
        # data chunk
        f.write(b"data")
        f.write(struct.pack("<I", data_size))
        f.write(b"\x00" * data_size)  # Silence


@pytest.mark.unit
class TestAudioDuration:
    def test_wav_duration(self, tmp_path):
        wav = str(tmp_path / "test.wav")
        _create_wav(wav, duration_seconds=10.0)
        dur = get_audio_duration(wav)
        assert 9.5 < dur < 10.5

    def test_short_wav(self, tmp_path):
        wav = str(tmp_path / "short.wav")
        _create_wav(wav, duration_seconds=1.0)
        dur = get_audio_duration(wav)
        assert 0.5 < dur < 1.5

    def test_missing_file_returns_zero(self):
        dur = get_audio_duration("C:/nonexistent/file.wav")
        # Should return 0, not crash
        assert dur == 0.0


@pytest.mark.unit
class TestChunkAudio:
    def test_short_audio_single_chunk(self, tmp_path):
        """Audio shorter than 30s should return one chunk."""
        wav = str(tmp_path / "short.wav")
        _create_wav(wav, duration_seconds=10.0)
        chunks = chunk_audio(wav)
        assert len(chunks) == 1
        assert chunks[0]["start_seconds"] == 0.0
        assert chunks[0]["index"] == 0

    @pytest.mark.skipif(
        not shutil.which("ffmpeg"), reason="ffmpeg not installed"
    )
    def test_long_audio_multiple_chunks(self, tmp_path):
        """Audio longer than 30s should be split."""
        wav = str(tmp_path / "long.wav")
        _create_wav(wav, duration_seconds=65.0)
        chunks = chunk_audio(wav, max_seconds=30)
        assert len(chunks) >= 3  # 65s / 30s = at least 3 chunks

        # Verify chunk boundaries are sequential
        for i, chunk in enumerate(chunks):
            assert chunk["index"] == i
            if i > 0:
                assert chunk["start_seconds"] >= chunks[i - 1]["end_seconds"] - 0.1

    @pytest.mark.skipif(
        not shutil.which("ffmpeg"), reason="ffmpeg not installed"
    )
    def test_custom_chunk_size(self, tmp_path):
        wav = str(tmp_path / "test.wav")
        _create_wav(wav, duration_seconds=20.0)
        chunks = chunk_audio(wav, max_seconds=5)
        assert len(chunks) >= 4  # 20s / 5s = 4

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            chunk_audio("/nonexistent/audio.wav")


@pytest.mark.unit
class TestInspectFile:
    def test_audio_file(self, tmp_path):
        wav = str(tmp_path / "test.wav")
        _create_wav(wav, duration_seconds=5.0)
        info = inspect_file(wav)
        assert info["exists"] is True
        assert info["category"] == "audio"
        assert info["extension"] == ".wav"
        assert info["size_bytes"] > 0

    def test_text_file(self, tmp_path):
        txt = tmp_path / "test.txt"
        txt.write_text("Hello world\nLine 2\n", encoding="utf-8")
        info = inspect_file(str(txt))
        assert info["category"] == "text"
        assert info["char_count"] > 0
        assert info["line_count"] >= 2

    def test_missing_file(self):
        info = inspect_file("/nonexistent/file.xyz")
        assert info["exists"] is False

    def test_image_detection(self, tmp_path):
        # Create a minimal PNG
        png = tmp_path / "test.png"
        from PIL import Image
        img = Image.new("RGB", (100, 50), color="white")
        img.save(str(png))
        info = inspect_file(str(png))
        assert info["category"] == "image"
        assert info["width"] == 100
        assert info["height"] == 50


@pytest.mark.unit
class TestFileTypeHelpers:
    def test_is_audio(self):
        assert is_audio("test.wav") is True
        assert is_audio("test.mp3") is True
        assert is_audio("test.flac") is True
        assert is_audio("test.ogg") is True
        assert is_audio("test.txt") is False
        assert is_audio("test.pdf") is False

    def test_is_image(self):
        assert is_image("test.png") is True
        assert is_image("test.jpg") is True
        assert is_image("test.jpeg") is True
        assert is_image("test.wav") is False

    def test_is_pdf(self):
        assert is_pdf("doc.pdf") is True
        assert is_pdf("doc.txt") is False

    def test_is_text(self):
        assert is_text("file.txt") is True
        assert is_text("file.md") is True
        assert is_text("file.csv") is True
        assert is_text("file.wav") is False
