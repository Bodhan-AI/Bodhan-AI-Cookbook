from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from mcp.server.fastmcp.exceptions import ToolError

from bodhan_mcp.ocr import _render_pdf


class OcrTests(unittest.TestCase):
    def test_render_sorts_double_digit_pages_numerically(self):
        with TemporaryDirectory() as directory:
            output = Path(directory)
            with patch("bodhan_mcp.ocr._poppler", side_effect=["Pages:  11\n", ""]):
                for page in reversed(range(1, 12)):
                    (output / f"page-{page}.png").touch()
                images = _render_pdf(Path("test.pdf"), output)
            self.assertEqual([p.name for p in images], [f"page-{n}.png" for n in range(1, 12)])

    def test_render_rejects_missing_page(self):
        with TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "page-1.png").touch()
            (output / "page-3.png").touch()
            with patch("bodhan_mcp.ocr._poppler", side_effect=["Pages:  3\n", ""]):
                with self.assertRaisesRegex(ToolError, "incomplete_render"):
                    _render_pdf(Path("test.pdf"), output)
