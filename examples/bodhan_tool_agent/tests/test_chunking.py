"""Tests for model limits handling: audio chunking, TTS sentence splitting, and text paragraph splitting."""

import os
os.environ["BODHAN_MOCK"] = "1"

import contextlib
import tempfile
import unittest
import wave
from pathlib import Path

from bodhan_mcp.tools.speak import _split_into_sentences, bodhan_speak
from bodhan_mcp.tools.transcribe import _chunk_wav_data, bodhan_transcribe
from bodhan_mcp.tools.translate import _split_into_paragraphs, bodhan_translate


class TestChunkingLimits(unittest.TestCase):
    def test_tts_sentence_splitting(self):
        text = "पहला वाक्य यहाँ समाप्त होता है। दूसरा वाक्य यह है! क्या यह तीसरा वाक्य है?"
        sentences = _split_into_sentences(text)
        self.assertEqual(len(sentences), 3)
        self.assertEqual(sentences[0], "पहला वाक्य यहाँ समाप्त होता है।")
        self.assertEqual(sentences[1], "दूसरा वाक्य यह है!")
        self.assertEqual(sentences[2], "क्या यह तीसरा वाक्य है?")

    def test_paragraph_splitting(self):
        doc = "\n\n".join([f"Paragraph {i}: Content for testing chunking limits." for i in range(10)])
        chunks = _split_into_paragraphs(doc, max_chunk_chars=100)
        self.assertGreater(len(chunks), 1)
        self.assertIn("Paragraph 0", chunks[0])

    def test_long_audio_chunking(self):
        with tempfile.TemporaryDirectory() as td:
            wav_path = Path(td) / "long_audio.wav"
            sample_rate = 16000
            duration_sec = 32
            silent_frame = b"\x00\x00" * sample_rate

            with contextlib.closing(wave.open(str(wav_path), "wb")) as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                for _ in range(duration_sec):
                    wf.writeframes(silent_frame)

            chunks = _chunk_wav_data(wav_path, seconds=25.0)
            self.assertEqual(len(chunks), 2)
            self.assertEqual(chunks[0][2], 0.0)
            self.assertEqual(chunks[0][3], 25.0)

            res = bodhan_transcribe(str(wav_path), language="hi")
            self.assertEqual(res.chunk_count, 2)
            self.assertGreaterEqual(res.duration_seconds, 30.0)
            self.assertIsNotNone(res.srt_subtitles)


if __name__ == "__main__":
    unittest.main()
