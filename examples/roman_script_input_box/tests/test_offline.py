"""Offline tests: no key, no network.   python -m unittest discover -s tests"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

RECIPE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RECIPE))
sys.path.insert(0, str(RECIPE / "eval"))

from compare_modes import cer, normalize  # noqa: E402

from bodhan_client import Client, Result, chat_instruction, parse_chat  # noqa: E402
from converter import Converter, tokenize  # noqa: E402


class FakeClient(Client):
    """Answers from a dict and counts calls, so word mode can be tested without the API."""

    def __init__(self, table: dict[str, str]):
        self.table, self.calls, self.route = table, [], "fake"

    def transliterate(self, text, language, script="native"):
        self.calls.append(text)
        return Result(self.table.get(text, f"<{text}>"), 1.0, "fake", False)


class Tokenize(unittest.TestCase):
    def test_round_trip_keeps_punctuation_and_numbers(self):
        text = "kal 10 baje, meeting hai!  don't forget"
        self.assertEqual("".join(p for p, _ in tokenize(text)), text)

    def test_only_roman_letters_are_words(self):
        self.assertEqual([p for p, w in tokenize("kal मीटिंग hai 5") if w], ["kal", "hai"])

    def test_apostrophe_stays_inside_a_word(self):
        self.assertIn(("don't", True), tokenize("don't go"))


class Instruction(unittest.TestCase):
    def test_names_the_target_script(self):
        self.assertEqual(chat_instruction("tame kem cho", "gu"), "Transliterate into Gujarati script: tame kem cho")
        self.assertEqual(chat_instruction("नमस्ते", "hi", "roman"), "Transliterate into roman script: नमस्ते")

    def test_parse_chat_reads_truncation(self):
        body = {"choices": [{"finish_reason": "length", "message": {"content": " कल \n"}}]}
        self.assertEqual(parse_chat(body), ("कल", True))


class WordMode(unittest.TestCase):
    def test_reassembles_and_caches(self):
        fake = FakeClient({"kal": "कल", "meeting": "मीटिंग", "hai": "है"})
        conv = Converter(fake)
        first = conv.word("Kal meeting hai, kal!", "hi")
        self.assertEqual(first.text, "कल मीटिंग है, कल!")
        self.assertEqual(sorted(fake.calls), ["hai", "kal", "meeting"])   # "Kal"/"kal" asked once
        second = conv.word("kal hai", "hi")
        self.assertEqual(second.calls, 0)
        self.assertEqual(second.cached, 2)

    def test_cache_is_per_language(self):
        fake = FakeClient({})
        conv = Converter(fake)
        conv.word("meeting", "hi")
        conv.word("meeting", "bn")
        self.assertEqual(len(fake.calls), 2)

    def test_draft_makes_no_calls_and_keeps_unknown_words(self):
        fake = FakeClient({"kal": "कल"})
        conv = Converter(fake)
        conv.word("kal", "hi")
        draft = conv.convert("kal meeting", "hi", "draft")
        self.assertEqual(draft.text, "कल meeting")
        self.assertEqual(len(fake.calls), 1)

    def test_sentence_seeds_word_cache_when_words_line_up(self):
        fake = FakeClient({"kal meeting hai": "कल मीटिंग है", "a, b": "x y"})
        conv = Converter(fake)
        conv.sentence("kal meeting hai", "hi")
        self.assertEqual(conv.convert("meeting hai", "hi", "draft").text, "मीटिंग है")
        conv.sentence("a, b", "hi")                     # punctuation: no seeding
        self.assertEqual(conv.convert("a", "hi", "draft").text, "a")


class Scoring(unittest.TestCase):
    def test_normalize_drops_punctuation(self):
        self.assertEqual(normalize("कल  मीटिंग है।"), "कल मीटिंग है")

    def test_cer_uses_closest_reference(self):
        self.assertEqual(cer("मैं घर जा रहा हूं", ["मैं घर जा रहा हूँ", "मैं घर जा रहा हूं"]), 0)
        self.assertGreater(cer("तुमसे क्या है", ["તમે કેમ છો"]), 0.9)


if __name__ == "__main__":
    unittest.main()
