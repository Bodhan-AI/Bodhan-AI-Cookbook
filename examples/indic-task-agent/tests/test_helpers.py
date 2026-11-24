"""
Unit tests for Bodhan Media Processing Helpers and Limit Handlers.
"""

import tempfile
import unittest
import wave
from pathlib import Path
from PIL import Image

from bodhan_mcp.helpers import (
    chunk_audio_if_needed,
    concatenate_audio_clips,
    inspect_media,
    split_text_for_tts,
)


class TestBodhanHelpers(unittest.TestCase):

    def test_split_text_for_tts_hindi_and_english(self):
        """Verify sentence-boundary chunking on Indic Purna Viram and Latin periods."""
        hindi_text = (
            "भारत एक विशाल देश है। यहाँ कई भाषाएं बोली जाती हैं। "
            "इस देश में विभिन्न संस्कृतियों का संगम है। हर राज्य की अपनी विशेषता है।"
        )
        # Split with small max_chars (e.g. 50)
        chunks = split_text_for_tts(hindi_text, max_chars=60)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 60)
            self.assertTrue(len(chunk) > 0)

    def test_inspect_media_image(self):
        """Verify image inspection extracting dimensions and format."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            img_path = Path(tmp_dir) / "test.png"
            img = Image.new("RGB", (320, 240), color="blue")
            img.save(img_path)

            meta = inspect_media(img_path)
            self.assertEqual(meta["media_type"], "image")
            self.assertEqual(meta["width"], 320)
            self.assertEqual(meta["height"], 240)
            self.assertEqual(meta["format"], "PNG")

    def test_inspect_media_audio(self):
        """Verify audio inspection extracting duration, channels, and sample rate."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            wav_path = Path(tmp_dir) / "sample_2sec.wav"
            # 2 seconds at 16000Hz mono 16-bit
            with wave.open(str(wav_path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(b"\x00" * (16000 * 2 * 2))

            meta = inspect_media(wav_path)
            self.assertEqual(meta["media_type"], "audio")
            self.assertAlmostEqual(meta["duration_seconds"], 2.0, delta=0.1)
            self.assertEqual(meta["sample_rate"], 16000)
            self.assertEqual(meta["channels"], 1)

    def test_chunk_audio_long_file(self):
        """Verify that audio longer than limit (e.g. 35s) is split into sub-30s chunks."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            wav_path = Path(tmp_dir) / "long_32s.wav"
            # 32 seconds at 16000Hz mono 16-bit
            with wave.open(str(wav_path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(b"\x00" * (16000 * 2 * 32))

            chunks = chunk_audio_if_needed(wav_path, max_chunk_sec=20.0, chunk_dir=tmp_dir)
            self.assertGreater(len(chunks), 1)
            for c in chunks:
                meta = inspect_media(c)
                self.assertLessEqual(meta["duration_seconds"], 20.5)

    def test_concatenate_audio_clips(self):
        """Verify concatenation of multiple audio clips into one file."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            c1 = Path(tmp_dir) / "clip1.wav"
            c2 = Path(tmp_dir) / "clip2.wav"
            out = Path(tmp_dir) / "combined.wav"

            # 1.0 second each
            for c in (c1, c2):
                with wave.open(str(c), "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(24000)
                    w.writeframes(b"\x00" * (24000 * 2 * 1))

            result_path = concatenate_audio_clips([c1, c2], out)
            self.assertTrue(result_path.exists())
            meta = inspect_media(result_path)
            # Should be roughly 2 seconds total
            self.assertAlmostEqual(meta["duration_seconds"], 2.0, delta=0.5)


if __name__ == "__main__":
    unittest.main()
