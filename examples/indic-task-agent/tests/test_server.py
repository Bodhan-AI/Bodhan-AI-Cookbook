"""
Unit tests for Bodhan MCP Server (Protocol, Tools, Limits, and Errors).
"""

import asyncio
import tempfile
import unittest
from pathlib import Path
from PIL import Image

from bodhan_mcp.server import server, translate_text_tool, inspect_media_tool


class TestMCPServer(unittest.TestCase):

    def test_registered_tools_count_and_names(self):
        """Verify the MCP server exposes exactly the 5 planned tools."""
        async def _check():
            tools = await server.list_tools()
            tool_names = {t.name for t in tools}
            expected = {
                "inspect_media",
                "speech_to_text",
                "translate_text",
                "synthesize_speech",
                "extract_text_ocr",
            }
            self.assertEqual(tool_names, expected)

        asyncio.run(_check())

    def test_voices_resource(self):
        """Verify the MCP voices resource provides the voice library."""
        async def _check():
            resources = await server.list_resources()
            uris = [r.uri for r in resources]
            self.assertIn("bodhan://voices", uris)

        asyncio.run(_check())

    def test_unsupported_language_graceful_handling(self):
        """Verify translation tool returns structured honest failure for unsupported language."""
        result = translate_text_tool("Translate this text", target_language="fr")
        self.assertEqual(result["status"], "impossible")
        self.assertIn("fr", result["unsupported_language"])
        self.assertIn("error", result)

    def test_inspect_media_tool_execution(self):
        """Verify inspect_media tool returns image dimensions and zero cost."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            img_path = Path(tmp_dir) / "tool_test.png"
            img = Image.new("RGB", (100, 100), color="green")
            img.save(img_path)

            meta = inspect_media_tool(str(img_path))
            self.assertEqual(meta["media_type"], "image")
            self.assertEqual(meta["width"], 100)
            self.assertEqual(meta["height"], 100)

    def test_extract_docx_and_xlsx_handling(self):
        """Verify extract_text_ocr tool handles .docx and .xlsx documents directly."""
        import zipfile
        from bodhan_mcp.server import extract_text_ocr_tool

        with tempfile.TemporaryDirectory() as tmp_dir:
            # 1. Test docx
            docx_p = Path(tmp_dir) / "test.docx"
            with zipfile.ZipFile(docx_p, "w") as z:
                z.writestr("word/document.xml", """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>Hello from DOCX</w:t></w:r></w:p></w:body>
</w:document>""")
            res_docx = extract_text_ocr_tool(str(docx_p))
            self.assertEqual(res_docx["status"], "success")
            self.assertIn("Hello from DOCX", res_docx["extracted_text"])

            # 2. Test xlsx
            xlsx_p = Path(tmp_dir) / "test.xlsx"
            with zipfile.ZipFile(xlsx_p, "w") as z:
                z.writestr("xl/sharedStrings.xml", """<?xml version="1.0" encoding="UTF-8"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="1" uniqueCount="1">
  <si><t>Header Cell</t></si>
</sst>""")
                z.writestr("xl/worksheets/sheet1.xml", """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData><row><c t="s"><v>0</v></c></row></sheetData>
</worksheet>""")
            res_xlsx = extract_text_ocr_tool(str(xlsx_p))
            self.assertEqual(res_xlsx["status"], "success")
            self.assertIn("Header Cell", res_xlsx["extracted_text"])


if __name__ == "__main__":
    unittest.main()
