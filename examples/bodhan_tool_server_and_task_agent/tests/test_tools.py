import tempfile
from pathlib import Path

from bodhan_mcp.tools import chunk_audio, inspect_media


def test_missing_file_is_structured_error():
    result = inspect_media("missing.png")
    assert result["ok"] is False
    assert result["error"]["code"] == "file_not_found"


def test_missing_audio_chunk_is_structured_error():
    result = chunk_audio("missing.wav", tempfile.mkdtemp())
    assert result["ok"] is False
    assert result["error"]["code"] == "file_not_found"


def test_png_inspection_reports_dimensions():
    from PIL import Image
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "page.png"
        Image.new("RGB", (320, 200), "white").save(p)
        result = inspect_media(str(p))
        assert result["ok"]
        assert result["result"]["kind"] == "image"
        assert result["result"]["width"] == 320
