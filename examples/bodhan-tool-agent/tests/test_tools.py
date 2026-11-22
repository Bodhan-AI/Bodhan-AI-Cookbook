"""Unit tests for Bodhan MCP tools with mocked HTTP (zero paid calls)."""

import json
import shutil
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

from bodhan_mcp.cache import DiskCache
from bodhan_mcp.config import Settings
from bodhan_mcp.pricing import BudgetManager
from bodhan_mcp.tools import BodhanTools, _split_text_by_length, format_srt_timestamp


class TestBodhanTools(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.cache_dir = self.temp_dir / "cache"
        self.cache = DiskCache(cache_dir=self.cache_dir)
        self.budget = BudgetManager(max_cost_inr=5.0)

        self.settings = Settings(
            bodhan_transcribe_key="mock_key",
            bodhan_translate_key="mock_key",
            bodhan_speak_key="mock_key",
            bodhan_ocr_key="mock_key",
            llm_base_url="https://api.openai.com/v1",
            llm_model="gpt-4o-mini",
            llm_api_key="mock_key",
        )

        self.mock_client = MagicMock()
        self.tools = BodhanTools(
            settings=self.settings,
            client=self.mock_client,
            cache=self.cache,
            budget=self.budget,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_synthetic_wav(self, path: Path, duration_s: float = 1.0, framerate: int = 16000) -> None:
        nframes = int(duration_s * framerate)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(framerate)
            w.writeframes(b"\x00\x00" * nframes)

    def test_text_splitting_boundary(self):
        long_sentence = "This is sentence one. " + ("word " * 40) + ". This is sentence two."
        chunks = _split_text_by_length(long_sentence, max_chars=100)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), 100)

    def test_srt_timestamp_formatting(self):
        self.assertEqual(format_srt_timestamp(0.0), "00:00:00,000")
        self.assertEqual(format_srt_timestamp(65.5), "00:01:05,500")
        self.assertEqual(format_srt_timestamp(3661.125), "01:01:01,125")

    def test_build_srt(self):
        segments = [
            {"start_s": 0.0, "end_s": 2.5, "text": "First subtitle line"},
            {"start_s": 2.5, "end_s": 5.0, "text": "Second subtitle line"},
        ]
        out_srt = self.temp_dir / "test.srt"
        res = self.tools.build_srt(segments, output_path=str(out_srt))
        self.assertTrue(res["success"])
        self.assertEqual(res["subtitle_count"], 2)
        content = out_srt.read_text(encoding="utf-8")
        self.assertIn("00:00:00,000 --> 00:00:02,500", content)
        self.assertIn("First subtitle line", content)

    def test_transcribe_audio_success(self):
        test_wav = self.temp_dir / "audio.wav"
        self._create_synthetic_wav(test_wav, duration_s=2.0)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"text": "नमस्ते यह एक परीक्षण है।"}
        self.mock_client.post.return_value = mock_resp

        res = self.tools.transcribe_audio(str(test_wav), language="hi")
        self.assertTrue(res["success"])
        self.assertEqual(res["transcript"], "नमस्ते यह एक परीक्षण है।")
        self.assertEqual(len(res["windows"]), 1)
        self.assertEqual(res["language_requested"], "hi")
        self.assertEqual(res["detected_script"], "hi")
        self.assertEqual(res["windows_count"], 1)
        self.assertFalse(res["windows"][0]["is_empty"])

    def test_transcribe_audio_cache_key_differentiation(self):
        audio_bytes = b"RIFF1234WAVEfmt "
        key_hi = self.cache.generate_key("indic-transcribe", audio_bytes, language="hi", vad="true")
        key_none = self.cache.generate_key("indic-transcribe", audio_bytes, language="none", vad="true")
        key_vad_false = self.cache.generate_key("indic-transcribe", audio_bytes, language="hi", vad="false")

        self.assertNotEqual(key_hi, key_none)
        self.assertNotEqual(key_hi, key_vad_false)

    def test_transcribe_audio_missing_file(self):
        res = self.tools.transcribe_audio("non_existent_file.wav")
        self.assertFalse(res["success"])
        self.assertIn("not found", res["error"])

    def test_translate_text_unsupported_language(self):
        # Must fail honestly on French / German without calling API
        res = self.tools.translate_text("Hello world", target_language_code="fr")
        self.assertFalse(res["success"])
        self.assertIn("Unsupported", res["error"])
        self.mock_client.post.assert_not_called()

    def test_translate_text_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "नमस्ते दुनिया"}}],
            "usage": {"completion_tokens": 5},
        }
        self.mock_client.post.return_value = mock_resp

        res = self.tools.translate_text("Hello world", target_language_code="hi")
        self.assertTrue(res["success"])
        self.assertEqual(res["translated_text"], "नमस्ते दुनिया")
        self.assertGreater(res["cost_inr"], 0.0)

    def test_synthesize_speech_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        # Create valid WAV bytes for mock response
        fake_wav = self.temp_dir / "fake.wav"
        self._create_synthetic_wav(fake_wav, duration_s=1.5)
        mock_resp.content = fake_wav.read_bytes()
        self.mock_client.post.return_value = mock_resp

        out_audio = self.temp_dir / "synth_output.wav"
        res = self.tools.synthesize_speech(
            text="नमस्ते भारत।",
            language="hi",
            voice="Amit",
            output_path=str(out_audio),
        )
        self.assertTrue(res["success"])
        self.assertTrue(out_audio.exists())
        self.assertAlmostEqual(res["duration_s"], 1.5, places=1)

    def test_extract_document_text_missing(self):
        res = self.tools.extract_document_text("missing.png")
        self.assertFalse(res["success"])
        self.assertIn("not found", res["error"])

    def test_extract_document_text_image(self):
        img_path = self.temp_dir / "sample.png"
        img_path.write_bytes(b"\x89PNG\r\n\x1a\nfakeimagebytes")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "सूचना फलक"}}],
            "blocks": [{"box": [0, 0, 10, 10]}],
        }
        self.mock_client.post.return_value = mock_resp

        res = self.tools.extract_document_text(str(img_path))
        self.assertTrue(res["success"])
        self.assertIn("सूचना फलक", res["text"])
        self.assertEqual(res["pages_count"], 1)

    def test_inspect_media(self):
        wav_path = self.temp_dir / "media.wav"
        self._create_synthetic_wav(wav_path, duration_s=3.0)

        res = self.tools.inspect_media(str(wav_path))
        self.assertTrue(res["success"])
        self.assertEqual(res["media_type"], "audio")
        self.assertAlmostEqual(res["duration_s"], 3.0, places=1)
        self.assertIn("estimated_transcribe_cost_inr", res)

    def test_detect_language(self):
        res = self.tools.detect_language(text="வணக்கம்")
        self.assertTrue(res["success"])
        self.assertEqual(res["language_code"], "ta")


if __name__ == "__main__":
    unittest.main()
