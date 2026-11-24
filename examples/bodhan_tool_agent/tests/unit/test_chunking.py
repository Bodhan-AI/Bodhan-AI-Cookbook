"""Unit tests for text and audio chunking algorithms."""

from __future__ import annotations

import wave
from pathlib import Path
from bodhan_mcp.tools.media import chunk_wav_file, stitch_wav_bytes
from bodhan_mcp.tools.speak import _split_text_for_speech
from bodhan_mcp.tools.translate import _chunk_text_preserving_structure


def test_text_translation_chunking_preserves_paragraphs():
    long_doc = "Paragraph one with some text.\n\nParagraph two with more details.\n\nParagraph three."
    chunks = _chunk_text_preserving_structure(long_doc, max_chars=40)
    assert len(chunks) >= 2
    rejoined = "\n\n".join(chunks)
    assert "Paragraph one" in rejoined
    assert "Paragraph three" in rejoined


def test_speech_sentence_splitting():
    text = "नमस्ते दोस्तों। आज मौसम बहुत अच्छा है! क्या आप घूमने चलेंगे?"
    sentences = _split_text_for_speech(text)
    assert len(sentences) == 3
    assert sentences[0] == "नमस्ते दोस्तों।"
    assert sentences[1] == "आज मौसम बहुत अच्छा है!"
    assert sentences[2] == "क्या आप घूमने चलेंगे?"


def test_audio_wav_chunk_and_stitch(tmp_path: Path):
    wav_path = tmp_path / "test.wav"
    # Create 2 seconds of silent PCM16 mono WAV at 24000 Hz
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(b"\x00\x00" * 48000)

    # Chunk into 1-second chunks
    chunks = list(chunk_wav_file(wav_path, chunk_seconds=1.0))
    assert len(chunks) == 2

    # Stitch back together
    stitched_path = tmp_path / "stitched.wav"
    blobs = [c[3] for c in chunks]
    stitch_wav_bytes(blobs, stitched_path)

    assert stitched_path.exists()
    with wave.open(str(stitched_path), "rb") as wf:
        assert wf.getnframes() == 48000
