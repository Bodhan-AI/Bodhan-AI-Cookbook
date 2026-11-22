import io
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from bodhan_mcp.speech import MAX_CHUNK_CHARACTERS, _speech_chunks, synthesize_speech
from mcp.server.fastmcp.exceptions import ToolError


class SpeechChunkTests(unittest.TestCase):
    def test_truncated_second_chunk_does_not_publish_a_wav(self):
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\x01\x02" * 20)
        valid = buffer.getvalue()

        with (
            TemporaryDirectory() as directory,
            patch.dict("os.environ", {"BODHAN_SPEAK_KEY": "test-key"}),
            patch("bodhan_mcp.speech._request_speech", side_effect=[valid, valid[:-2]]) as request,
        ):
            path = Path(directory) / "recap.wav"
            with self.assertRaisesRegex(ToolError, "Speech chunk 2 of 2:.*truncated WAV"):
                synthesize_speech("First line. Second line.", "en", "Arun", str(path))
            self.assertEqual(request.call_count, 2)
            self.assertFalse(path.exists())

    def test_merged_table_sends_cell_text_in_row_order(self):
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\x01\x02" * 20)
        table = (
            '<table><tr><th colspan="2">Schedule &amp; times</th></tr>'
            '<tr><td rowspan="2">Monday</td><td>Read<br>aloud</td></tr>'
            '<tr><td><em>Practice</em></td></tr></table>'
        )
        with (
            TemporaryDirectory() as directory,
            patch.dict("os.environ", {"BODHAN_SPEAK_KEY": "test-key"}),
            patch("bodhan_mcp.speech._request_speech", return_value=buffer.getvalue()) as request,
        ):
            path = Path(directory) / "recap.wav"
            result = synthesize_speech(table, "en", "Arun", str(path))
            self.assertEqual(result.chunks, 3)
            self.assertEqual([call.args[1] for call in request.call_args_list],
                             ["Schedule & times", "Monday, Read aloud", "Practice"])
            with wave.open(str(path)) as wav:
                self.assertEqual(wav.getnframes(), 60)

    def test_preserves_words_across_sentence_and_length_boundaries(self):
        long_sentence = " ".join(["ಮಾಹಿತಿ"] * 45) + "।"
        text = "ಮೊದಲ ವಾಕ್ಯ। " + long_sentence
        chunks = _speech_chunks(text)
        self.assertEqual(chunks[0], "ಮೊದಲ ವಾಕ್ಯ।")
        self.assertEqual(" ".join(chunks), text)
        self.assertTrue(all(len(chunk) <= MAX_CHUNK_CHARACTERS for chunk in chunks))

    def test_rejects_unbroken_oversized_input(self):
        with self.assertRaisesRegex(ToolError, "oversized_word"):
            _speech_chunks("a" * (MAX_CHUNK_CHARACTERS + 1))
