import json
import unittest

import httpx
from bodhan_mcp.document_chunks import CHUNK_BYTES, document_parts, markdown_for_export, text_for_speech
from bodhan_mcp.server import _translate_document


class DocumentTranslationTests(unittest.TestCase):
    def test_speech_reads_table_headers_and_cells_in_order(self):
        text = "# Schedule\n\n| Route | Time |\n| --- | --- |\n| A17 | 08:15 |\n| B24 | 09:40 |"
        self.assertEqual(text_for_speech(text), "Schedule\n\nRoute, Time\nA17, 08:15\nB24, 09:40")

    def test_standalone_identifiers_stay_literal(self):
        source = "Record Q47 is open.\n\n| Route | Time |\n| --- | --- |\n| B24 | 09:40 |"
        parts = document_parts(source)
        self.assertEqual("".join(part.value for part in parts), source)
        self.assertEqual([part.value for part in parts if part.value in {"Q47", "B24"}], ["Q47", "B24"])
        self.assertFalse(any(
            part.mode and ("Q47" in part.for_request() or "B24" in part.for_request()) for part in parts
        ))

    def test_long_unicode_input_is_bounded_and_keeps_every_source_character(self):
        source = ("ತರಗತಿ ನಾಳೆ ಪ್ರಾರಂಭವಾಗುತ್ತದೆ. " * 120) + "\n\nLast paragraph."
        parts = document_parts(source)
        self.assertEqual("".join(part.value for part in parts), source)
        translated = [part.value for part in parts if part.mode]
        self.assertGreater(len(translated), 2)
        self.assertTrue(all(len(piece.encode("utf-8")) <= CHUNK_BYTES for piece in translated))

    def test_markdown_and_html_table_structures_and_offsets_survive_translation(self):
        source = (
            "# Notice\n\n"
            "| Item | Time |\n| :--- | ---: |\n| Class `a|b` | 9 |\n\n"
            '<table><tr><th colspan="2">Notice</th></tr>'
            "<tr><td>Class</td><td>9</td></tr></table>\n\n"
            "```python\nprint('unchanged')\n```"
        )
        dictionary = {"Notice": "ಸೂಚನೆ", "Item": "ವಿಷಯ", "Time": "ಸಮಯ", "Class": "ತರಗತಿ"}
        calls = []

        def reply(request):
            data = json.loads(request.content)
            text = data["messages"][0]["content"]
            calls.append(text)
            return httpx.Response(200, json={
                "choices": [{"finish_reason": "stop", "message": {"content": dictionary[text]}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            })

        with httpx.Client(transport=httpx.MockTransport(reply)) as client:
            result = _translate_document(client, source, "kn", "test-key")
        self.assertEqual(result.translation, (
            "# ಸೂಚನೆ\n\n"
            "| ವಿಷಯ | ಸಮಯ |\n| :--- | ---: |\n| ತರಗತಿ `a|b` | 9 |\n\n"
            '<table><tr><th colspan="2">ಸೂಚನೆ</th></tr>'
            "<tr><td>ತರಗತಿ</td><td>9</td></tr></table>\n\n"
            "```python\nprint('unchanged')\n```"
        ))
        self.assertCountEqual(calls, dictionary.keys())
        self.assertEqual(result.usage.completion_tokens, 8)
        for segment in result.segments:
            self.assertEqual(result.translation[segment.output_start:segment.output_end],
                             dictionary[source[segment.source_start:segment.source_end]])


    def test_pdf_table_exports_as_markdown_and_keeps_table_data(self):
        source = (
            "Contents\n\n<table><thead><tr><th>Activity</th><th>Time</th></tr></thead>"
            "<tbody><tr><td>Reading | art</td><td>10:00</td></tr></tbody></table>"
        )
        self.assertEqual(markdown_for_export(source), (
            "Contents\n\n| Activity | Time |\n| --- | --- |\n| Reading \\| art | 10:00 |"
        ))

    def test_blank_line_between_ocr_pages_is_literal_even_inside_an_html_cell(self):
        text = "<table><tr><td>First page\n\nSecond page</td></tr></table>"
        parts = document_parts(text)
        self.assertEqual("".join(part.value for part in parts), text)
        self.assertEqual([part.for_request() for part in parts if part.mode], ["First page", "Second page"])

    def test_merged_cells_keep_the_original_html(self):
        source = '<table><tr><th colspan="2">Notice</th></tr><tr><td>Reading</td><td>10:00</td></tr></table>'
        self.assertEqual(markdown_for_export(source), source)
