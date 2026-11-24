"""Unit tests for multi-column heuristic and LLM repair fallback."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from bodhan_lesson.layout_repair import (
    apply_repair,
    repair_page_blocks,
    run_layout_on_pages,
    suggests_multi_column,
    validate_repair_schema,
)


def test_suggests_multi_column_side_by_side() -> None:
    blocks = [
        {
            "id": "a",
            "label": "Paragraph",
            "bbox_xyxy": [50, 100, 300, 400],
            "text": "left",
        },
        {
            "id": "b",
            "label": "Paragraph",
            "bbox_xyxy": [400, 120, 700, 380],
            "text": "right",
        },
    ]
    assert suggests_multi_column(blocks) is True


def test_no_multi_column_when_stacked() -> None:
    blocks = [
        {
            "id": "a",
            "label": "Paragraph",
            "bbox_xyxy": [50, 100, 700, 200],
            "text": "top",
        },
        {
            "id": "b",
            "label": "Paragraph",
            "bbox_xyxy": [50, 250, 700, 400],
            "text": "bottom",
        },
    ]
    assert suggests_multi_column(blocks) is False


def test_text_beside_diagram_does_not_trigger() -> None:
    """Only textish-vs-textish counts — figure beside prose is normal."""
    blocks = [
        {
            "id": "a",
            "label": "Paragraph",
            "bbox_xyxy": [50, 100, 300, 400],
            "text": "left prose",
        },
        {
            "id": "b",
            "label": "Diagram",
            "bbox_xyxy": [400, 120, 700, 380],
            "text": "",
        },
    ]
    assert suggests_multi_column(blocks) is False


def test_validate_and_apply_repair() -> None:
    kept = [
        {"id": "0", "text": "A", "flags": []},
        {"id": "1", "text": "B", "flags": []},
        {"id": "2", "text": "junk", "flags": []},
    ]
    parsed = validate_repair_schema(
        {
            "ordered_ids": ["1", "0"],
            "drop_ids": ["2"],
            "doubtful_ids": ["0"],
        },
        {"0", "1", "2"},
    )
    out = apply_repair(
        kept,
        ordered_ids=parsed["ordered_ids"],
        drop_ids=parsed["drop_ids"],
        doubtful_ids=parsed["doubtful_ids"],
    )
    assert [b["id"] for b in out] == ["1", "0"]
    assert "doubtful" in out[1]["flags"]


def test_invalid_schema_raises() -> None:
    with pytest.raises(ValueError):
        validate_repair_schema({"ordered_ids": []}, {"0"})


def test_repair_skips_llm_when_single_column() -> None:
    blocks = [
        {
            "order": 0,
            "label": "Paragraph",
            "type": "Text",
            "bbox_xyxy": [10, 10, 500, 80],
            "conf": 0.9,
            "text": "Only one column.",
        }
    ]
    with patch("bodhan_lesson.layout_repair.chat_json") as mock_chat:
        result = repair_page_blocks(blocks, page_index=0, use_llm=True)
    mock_chat.assert_not_called()
    assert result["multi_column"] is False
    assert result["repair"] == "skipped"
    assert result["blocks"][0]["text"] == "Only one column."


def test_repair_fallback_on_bad_llm() -> None:
    blocks = [
        {
            "order": 0,
            "label": "Paragraph",
            "type": "Text",
            "bbox_xyxy": [50, 100, 300, 400],
            "conf": 0.9,
            "text": "Left",
        },
        {
            "order": 1,
            "label": "Paragraph",
            "type": "Text",
            "bbox_xyxy": [400, 120, 700, 380],
            "conf": 0.9,
            "text": "Right",
        },
    ]
    with patch(
        "bodhan_lesson.layout_repair.chat_json",
        return_value=(
            {"ordered_ids": ["999"], "drop_ids": [], "doubtful_ids": []},
            {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        ),
    ):
        result = repair_page_blocks(blocks, page_index=0, use_llm=True)
    assert result["multi_column"] is True
    assert result["repair"] == "fallback"
    assert [b["id"] for b in result["blocks"]] == ["0", "1"]


def test_repair_llm_success_reorders() -> None:
    blocks = [
        {
            "order": 0,
            "label": "Paragraph",
            "type": "Text",
            "bbox_xyxy": [50, 100, 300, 400],
            "conf": 0.9,
            "text": "Left",
        },
        {
            "order": 1,
            "label": "Paragraph",
            "type": "Text",
            "bbox_xyxy": [400, 120, 700, 380],
            "conf": 0.9,
            "text": "Right",
        },
    ]
    usage = {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28}
    with patch(
        "bodhan_lesson.layout_repair.chat_json",
        return_value=(
            {
                "ordered_ids": ["1", "0"],
                "drop_ids": [],
                "doubtful_ids": [],
            },
            usage,
        ),
    ):
        result = repair_page_blocks(blocks, page_index=0, use_llm=True)
    assert result["repair"] == "llm"
    assert [b["text"] for b in result["blocks"]] == ["Right", "Left"]
    assert result["content"].startswith("Right")
    assert result["llm_usage"] == usage


def test_run_layout_on_pages_writes_artifacts(tmp_path: Path) -> None:
    pages = tmp_path / "pages"
    pages.mkdir()
    ocr_path = pages / "page_001.ocr.json"
    ocr_path.write_text(
        json.dumps(
            {
                "page_image": "page_001.png",
                "content": "Hello",
                "blocks": [
                    {
                        "order": 0,
                        "label": "Header",
                        "type": "PageHeader",
                        "bbox_xyxy": [1, 1, 10, 10],
                        "conf": 0.5,
                        "text": "",
                    },
                    {
                        "order": 1,
                        "label": "Paragraph",
                        "type": "Text",
                        "bbox_xyxy": [10, 50, 400, 100],
                        "conf": 0.95,
                        "text": "Hello body",
                    },
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    ocr_pages = [
        {
            "page": "page_001.png",
            "stem": "page_001",
            "status": "ok",
            "ocr_json_path": str(ocr_path),
        }
    ]
    rows = run_layout_on_pages(pages, ocr_pages, use_llm=False)
    assert rows[0]["status"] == "ok"
    assert rows[0]["repair"] == "skipped"
    clean = json.loads((pages / "page_001.clean.json").read_text(encoding="utf-8"))
    assert [b["id"] for b in clean["blocks"]] == ["1"]
    assert (pages / "page_001.md").read_text(encoding="utf-8").strip() == "Hello body"
