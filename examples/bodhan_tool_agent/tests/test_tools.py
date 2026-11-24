"""Unit tests for individual Bodhan MCP tools."""

import os
os.environ["BODHAN_MOCK"] = "1"

import unittest
import tempfile
from pathlib import Path

from bodhan_mcp.tools import (
    bodhan_detect_language,
    bodhan_inspect_media,
    bodhan_list_voices,
    bodhan_ocr,
    bodhan_speak,
    bodhan_transcribe,
    bodhan_translate,
)


class TestBodhanTools(unittest.TestCase):
    def test_inspect_media(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("Hello Indic World")
            temp_path = f.name
        try:
            info = bodhan_inspect_media(temp_path)
            self.assertEqual(info.media_type, "text")
            self.assertGreater(info.size_bytes, 0)
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_detect_language(self):
        hindi_text = "यह हिंदी भाषा का पाठ है।"
        info = bodhan_detect_language(text=hindi_text)
        self.assertEqual(info.code, "hi")
        self.assertEqual(info.script, "Devanagari")
        self.assertTrue(info.is_indic)

        tamil_text = "இது தமிழ் உரை."
        info_ta = bodhan_detect_language(text=tamil_text)
        self.assertEqual(info_ta.code, "ta")
        self.assertEqual(info_ta.script, "Tamil")

    def test_list_voices(self):
        voices = bodhan_list_voices(language="hi")
        self.assertGreaterEqual(len(voices), 2)
        voice_names = [v.name for v in voices]
        self.assertTrue("Kavya" in voice_names or "Amit" in voice_names)

    def test_translate_valid(self):
        res = bodhan_translate(text="The meeting is tomorrow.", target_language="ta")
        self.assertEqual(res.target_language, "ta")
        self.assertGreater(len(res.translation), 0)
        self.assertGreaterEqual(res.cost_inr, 0.0)

    def test_translate_unsupported_language(self):
        with self.assertRaises(ValueError) as ctx:
            bodhan_translate(text="Hello", target_language="fr")
        self.assertIn("not supported", str(ctx.exception))

    def test_speak_tool(self):
        with tempfile.TemporaryDirectory() as td:
            out_wav = Path(td) / "speech.wav"
            res = bodhan_speak(
                text="विद्यालय सूचना।",
                language="hi",
                voice="Kavya",
                output_file=str(out_wav),
            )
            self.assertTrue(out_wav.exists())
            self.assertGreater(out_wav.stat().st_size, 0)
            self.assertGreater(res.character_count, 0)
            self.assertGreaterEqual(res.cost_inr, 0.0)


if __name__ == "__main__":
    unittest.main()
