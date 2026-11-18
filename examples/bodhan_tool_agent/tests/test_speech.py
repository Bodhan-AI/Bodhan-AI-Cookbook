import unittest

from mcp.server.fastmcp.exceptions import ToolError

from bodhan_mcp.speech import MAX_CHUNK_CHARACTERS, _speech_chunks


class SpeechChunkTests(unittest.TestCase):
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
