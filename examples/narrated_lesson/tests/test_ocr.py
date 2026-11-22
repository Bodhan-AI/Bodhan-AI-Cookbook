"""Unit tests for Indic-OCR helpers (HTTP mocked)."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from bodhan_lesson.ocr import (
    build_ocr_request_body,
    extract_ocr_fields,
    list_page_images,
    run_ocr_on_pages,
    save_ocr_artifacts,
)


def _fake_ocr_response(text: str = "Hello page") -> dict:
    return {
        "id": "indic-ocr-test",
        "model": "indic-ocr",
        "choices": [{"finish_reason": "stop", "index": 0, "message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "blocks": [
            {
                "order": 0,
                "label": "Paragraph",
                "type": "Text",
                "bbox_xyxy": [1, 2, 3, 4],
                "conf": 0.9,
                "text": text,
            }
        ],
    }


@pytest.fixture
def two_pages(tmp_path: Path) -> Path:
    pages = tmp_path / "pages"
    pages.mkdir()
    for name in ("page_001.png", "page_002.jpg"):
        Image.new("RGB", (16, 16), color=(255, 255, 255)).save(pages / name)
    (pages / "notes.txt").write_text("ignore", encoding="utf-8")
    return pages


def test_list_page_images_skips_non_images(two_pages: Path) -> None:
    files = list_page_images(two_pages)
    assert [p.name for p in files] == ["page_001.png", "page_002.jpg"]


def test_build_ocr_request_body_has_image_only(two_pages: Path) -> None:
    image = two_pages / "page_001.png"
    body = build_ocr_request_body(image)
    assert body["model"] == "indic-ocr"
    assert body["table_format"] == "html"
    messages = body["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    content = messages[0]["content"]
    assert len(content) == 1
    assert content[0]["type"] == "image_url"
    url = content[0]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    # Round-trip a few bytes to prove it is real base64 of the file.
    b64 = url.split(",", 1)[1]
    assert base64.b64decode(b64) == image.read_bytes()


def test_extract_and_save_artifacts(two_pages: Path) -> None:
    image = two_pages / "page_001.png"
    summary = save_ocr_artifacts(image, _fake_ocr_response("Line one"))
    assert summary["status"] == "ok"
    assert summary["block_count"] == 1
    md = two_pages / "page_001.md"
    ocr_json = two_pages / "page_001.ocr.json"
    assert md.read_text(encoding="utf-8").startswith("Line one")
    data = json.loads(ocr_json.read_text(encoding="utf-8"))
    assert data["blocks"][0]["label"] == "Paragraph"


def test_extract_ocr_fields_requires_content() -> None:
    with pytest.raises(RuntimeError, match="no choices"):
        extract_ocr_fields({})


def test_run_ocr_continues_after_one_failure(two_pages: Path) -> None:
    responses = iter([_fake_ocr_response("ok"), RuntimeError("boom")])

    def fake_ocr(image_path: Path, **kwargs):  # noqa: ANN003
        item = next(responses)
        if isinstance(item, Exception):
            raise item
        return item

    with patch("bodhan_lesson.ocr.ocr_page", side_effect=fake_ocr):
        with patch("bodhan_lesson.ocr.get_ocr_api_key", return_value="test-key"):
            results = run_ocr_on_pages(two_pages)

    assert results[0]["status"] == "ok"
    assert results[1]["status"] == "error"
    assert "boom" in results[1]["error"]
    assert (two_pages / "page_001.md").exists()
    assert not (two_pages / "page_002.md").exists()
