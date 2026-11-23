"""
Unit tests for Bodhan Client using standard unittest:
- Language guardrails & honest failure
- Exact pricing & cost calculations
- Disk caching & cost savings
- Audio duration measurement
"""

import os
import tempfile
import unittest
import wave
from pathlib import Path

from bodhan_mcp.client import (
    BodhanClient,
    BodhanUnsupportedLanguageError,
)
from bodhan_mcp.config import PRICING_INR


class TestBodhanClient(unittest.TestCase):

    def test_unsupported_language_rejection(self):
        """Verify that unsupported languages (e.g. French, German) immediately halt."""
        client = BodhanClient(base_url="http://mock:8000/v1")

        with self.assertRaises(BodhanUnsupportedLanguageError) as ctx:
            client.translate("Hello world", target_language="fr")
        self.assertIn("fr", str(ctx.exception))
        self.assertIn("not supported", str(ctx.exception))

        with self.assertRaises(BodhanUnsupportedLanguageError) as ctx2:
            client.speak("Bonjour", language="french")
        self.assertIn("not supported", str(ctx2.exception))

    def test_pricing_formulas(self):
        """Verify that pricing exactly matches the official Bodhan spec."""
        # Transcribe: ₹0.10 / min
        one_min_cost = (60.0 / 60.0) * PRICING_INR["transcribe_per_minute"]
        self.assertAlmostEqual(one_min_cost, 0.10, places=6)

        # Translate: ₹0.20 / 10k tokens
        tokens_cost = (10000 / 10000.0) * PRICING_INR["translate_per_10k_tokens"]
        self.assertAlmostEqual(tokens_cost, 0.20, places=6)

        # Speak: ₹6.00 / 10k characters
        chars_cost = (10000 / 10000.0) * PRICING_INR["speak_per_10k_chars"]
        self.assertAlmostEqual(chars_cost, 6.00, places=6)

        # OCR: ₹0.20 / image
        self.assertEqual(PRICING_INR["ocr_per_image"], 0.20)

    def test_client_caching_and_cost_tracking(self):
        """Verify that cached calls incur ₹0.00 and track saved costs."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            client = BodhanClient(base_url="http://mock:8000/v1", cache_dir=tmp_dir)

            # Manually seed cache entry for translate
            cache_key = client._compute_cache_key("translate", {
                "text": "Hello",
                "src": "en",
                "tgt": "hi",
                "model": "indic-translate",
            })
            client._write_cache(cache_key, {
                "translated_text": "नमस्ते",
                "source_language": "en",
                "target_language": "hi",
                "tokens": 2,
                "cost_inr": 0.00004,
                "cached": False,
            })

            # Calling translate should hit cache
            res = client.translate("Hello", source_language="en", target_language="hi")
            self.assertTrue(res["cached"])
            self.assertEqual(res["translated_text"], "नमस्ते")
            self.assertEqual(client.get_total_cost(), 0.0)  # Not billed for cache hit
            self.assertGreater(client.get_saved_cost(), 0.0)   # Saved cost recorded

    def test_audio_duration_calculation(self):
        """Verify duration calculation from a synthetic WAV file."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            client = BodhanClient(cache_dir=tmp_dir)
            wav_path = Path(tmp_dir) / "test_1sec.wav"

            # Generate 1.0 second of silence at 16000Hz mono 16-bit (16000 samples * 2 bytes = 32000 bytes)
            with wave.open(str(wav_path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(b"\x00" * 32000)

            duration = client.get_audio_duration(wav_path)
            self.assertAlmostEqual(duration, 1.0, delta=0.05)


if __name__ == "__main__":
    unittest.main()
