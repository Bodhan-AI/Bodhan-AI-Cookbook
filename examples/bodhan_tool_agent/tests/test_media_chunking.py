#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import wave
from pathlib import Path

import pytest

from bodhan_mcp.media import (
    inspect_audio_video,
    convert_to_wav_16khz_mono,
    chunk_wav_silence_aware,
    split_sentences,
    chunk_text_for_translation,
    pdf_to_pngs,
    detect_script,
    script_ratio,
    count_table_rows,
    count_table_cols,
    verify_table_parity,
    stitch_wav,
)


def create_test_wav(path: str, duration_sec: float = 1.0, sample_rate: int = 16000):
    """Create a simple test WAV file."""
    n_frames = int(duration_sec * sample_rate)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * n_frames)


def create_test_pdf(path: str):
    """Create a minimal test PDF using pypdfium2 if available."""
    try:
        import pypdfium2
        doc = pypdfium2.PdfDocument.new()
        doc.new_page(width=612, height=792)
        doc.save(path)
        doc.close()
        return True
    except Exception:
        return False


class TestAudioInspection:
    def test_inspect_audio_video(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wav_path = Path(tmpdir) / "test.wav"
            create_test_wav(str(wav_path), duration_sec=2.0)
            info = inspect_audio_video(str(wav_path))
            assert info["duration_sec"] == pytest.approx(2.0, abs=0.1)
            assert info["sample_rate"] == 16000
            assert info["channels"] == 1

    def test_convert_to_wav_16khz_mono(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "src.wav"
            dst = Path(tmpdir) / "dst.wav"
            create_test_wav(str(src), duration_sec=1.0, sample_rate=44100)
            convert_to_wav_16khz_mono(str(src), str(dst))
            info = inspect_audio_video(str(dst))
            assert info["sample_rate"] == 16000
            assert info["channels"] == 1


class TestAudioChunking:
    def test_chunk_short_audio(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wav_path = Path(tmpdir) / "short.wav"
            create_test_wav(str(wav_path), duration_sec=5.0)
            chunks = list(chunk_wav_silence_aware(str(wav_path), max_sec=30.0))
            assert len(chunks) == 1
            idx, chunk_path, (start, end) = chunks[0]
            assert idx == 0
            assert start == 0.0
            assert end == pytest.approx(5.0, abs=0.1)
            Path(chunk_path).unlink(missing_ok=True)

    def test_chunk_long_audio_fixed_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wav_path = Path(tmpdir) / "long.wav"
            create_test_wav(str(wav_path), duration_sec=120.0)
            chunks = list(chunk_wav_silence_aware(str(wav_path), max_sec=30.0))
            assert len(chunks) == 5
            for i, (idx, chunk_path, (start, end)) in enumerate(chunks):
                assert idx == i
                assert end - start <= 28.0 + 0.1
                Path(chunk_path).unlink(missing_ok=True)

    def test_global_timestamps_preserved(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wav_path = Path(tmpdir) / "long.wav"
            create_test_wav(str(wav_path), duration_sec=65.0)
            chunks = list(chunk_wav_silence_aware(str(wav_path), max_sec=30.0))
            timestamps = [(s, e) for _, _, (s, e) in chunks]
            assert timestamps[0] == (0.0, pytest.approx(28.0, abs=1.0))
            assert timestamps[1][0] == pytest.approx(28.0, abs=1.0)
            assert timestamps[-1][1] == pytest.approx(65.0, abs=1.0)
            for _, chunk_path, _ in chunks:
                Path(chunk_path).unlink(missing_ok=True)


class TestSentenceSplitting:
    def test_split_english_sentences(self):
        text = "Hello world. How are you? I'm fine! Great."
        sentences = split_sentences(text, "en")
        assert len(sentences) == 1
        assert "Hello world" in sentences[0]

    def test_split_hindi_sentences(self):
        text = "नमस्ते दुनिया। आप कैसे हैं? मैं ठीक हूँ! बहुत अच्छा।"
        sentences = split_sentences(text, "hi")
        assert len(sentences) == 1
        assert "नमस्ते" in sentences[0]

    def test_tts_chunking_long_text(self):
        long_text = " ".join(["This is a test sentence."] * 200)
        chunks = split_sentences(long_text, "en")
        assert len(chunks) >= 2
        for chunk in chunks:
            assert len(chunk) <= 3000

    def test_empty_text(self):
        assert split_sentences("", "en") == []
        assert split_sentences("   ", "en") == []


class TestTranslationChunking:
    def test_chunk_preserves_markdown_tables(self):
        md = """| Name | Age |
|------|-----|
| Alice | 30 |
| Bob | 25 |"""
        chunks = chunk_text_for_translation(md)
        assert len(chunks) == 1
        chunk = chunks[0]
        assert "| Name | Age |" in chunk
        assert "|------|-----|" in chunk
        assert "| Alice | 30 |" in chunk
        assert "| Bob | 25 |" in chunk

    def test_chunk_large_markdown_table(self):
        md = """| Name | Data |
|------|------|
| Row0 | Data0 |
| Row1 | Data1 |"""
        chunks = chunk_text_for_translation(md)
        assert len(chunks) == 1
        chunk = chunks[0]
        assert "| Name | Data |" in chunk
        assert "|------|------|" in chunk
        assert "| Row0 | Data0 |" in chunk
        assert "| Row1 | Data1 |" in chunk

    def test_chunk_paragraphs_separately(self):
        text = "Para 1.\n\nPara 2.\n\nPara 3."
        chunks = chunk_text_for_translation(text)
        assert len(chunks) == 3

    def test_chunk_long_paragraph(self):
        long_para = " ".join(["Sentence."] * 200)
        chunks = chunk_text_for_translation(long_para)
        assert all(len(c) <= 5000 for c in chunks)


class TestPDFRendering:
    def test_pdf_to_pngs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "test.pdf"
            if create_test_pdf(str(pdf_path)):
                pngs = pdf_to_pngs(str(pdf_path))
                assert len(pngs) == 1
                assert Path(pngs[0]).exists()
                Path(pngs[0]).unlink(missing_ok=True)
            else:
                pytest.skip("pypdfium2 not available for PDF creation")


class TestScriptDetection:
    def test_detect_devanagari(self):
        assert detect_script("नमस्ते") == "devanagari"

    def test_detect_tamil(self):
        assert detect_script("வணக்கம்") == "tamil"

    def test_detect_telugu(self):
        assert detect_script("నమస్కారం") == "telugu"

    def test_detect_bengali(self):
        assert detect_script("নমস্কার") == "bengali"

    def test_detect_latin(self):
        assert detect_script("Hello world") == "latin"

    def test_script_ratio(self):
        text = "नमस्ते hello नमस्ते"
        ratio = script_ratio(text, "devanagari")
        assert ratio == pytest.approx(0.71, abs=0.02)


class TestTableVerification:
    def test_count_table_rows(self):
        md = """| A | B |
|---|---|
| 1 | 2 |
| 3 | 4 |"""
        assert count_table_rows(md) == 3

    def test_count_table_cols(self):
        md = """| A | B | C |
|---|---|---|
| 1 | 2 | 3 |"""
        assert count_table_cols(md) == 3

    def test_verify_table_parity_same(self):
        md = """| A | B |
|---|---|
| 1 | 2 |"""
        assert verify_table_parity(md, md) is True

    def test_verify_table_parity_different(self):
        md1 = """| A | B |
|---|---|
| 1 | 2 |"""
        md2 = """| A | B | C |
|---|---|---|
| 1 | 2 | 3 |"""
        assert verify_table_parity(md1, md2) is False


class TestAudioStitching:
    def test_stitch_wav(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = []
            for i in range(3):
                p = Path(tmpdir) / f"part{i}.wav"
                create_test_wav(str(p), duration_sec=0.5)
                paths.append(str(p))
            out = Path(tmpdir) / "out.wav"
            stitch_wav(paths, str(out))
            info = inspect_audio_video(str(out))
            assert info["duration_sec"] == pytest.approx(1.5, abs=0.1)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
