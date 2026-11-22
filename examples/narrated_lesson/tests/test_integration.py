"""Integration tests: real filesystem CLI runs, OCR HTTP mocked."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from bodhan_lesson.cli import main


def _fake_ocr_ok(pages_dir: Path, **kwargs):  # noqa: ANN003
    images = [
        p
        for p in sorted(pages_dir.glob("page_*.*"))
        if p.suffix.lower() in {".png", ".jpg", ".jpeg"}
    ]
    rows = []
    for img in images:
        md = pages_dir / f"{img.stem}.md"
        ocr_json = pages_dir / f"{img.stem}.ocr.json"
        md.write_text("mock markdown\n", encoding="utf-8")
        ocr_json.write_text(
            json.dumps({"page_image": img.name, "content": "mock markdown", "blocks": []}) + "\n",
            encoding="utf-8",
        )
        rows.append(
            {
                "page": img.name,
                "stem": img.stem,
                "status": "ok",
                "block_count": 0,
                "md_path": str(md),
                "ocr_json_path": str(ocr_json),
                "usage": {},
                "error": None,
                "latency_s": 0.01,
                "retries": 0,
            }
        )
    return rows


def test_cli_phase2_from_image_folder(image_folder: Path, tmp_path: Path) -> None:
    out = tmp_path / "out_images"
    with patch("bodhan_lesson.cli.run_ocr_on_pages", side_effect=_fake_ocr_ok):
        code = main(
            [
                "--input", str(image_folder),
                "--langs", "hi,ta",
                "--out", str(out),
            ]
        )
    assert code == 0
    pages = sorted(
        p
        for p in (out / "source" / "pages").glob("page_*")
        if p.suffix.lower() in {".png", ".jpg", ".jpeg"}
    )
    assert len(pages) == 2
    assert (out / "source" / "pages" / "page_001.md").is_file()
    assert (out / "hi" / "narration").is_dir()
    assert (out / "ta" / "narration").is_dir()
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["page_count"] == 2
    assert report["langs"] == ["hi", "ta"]
    assert report["phase"] == 2
    assert report["ocr"]["ok"] == 2


def test_cli_phase2_from_pdf(multi_pdf: Path, tmp_path: Path) -> None:
    out = tmp_path / "out_pdf"
    with patch("bodhan_lesson.cli.run_ocr_on_pages", side_effect=_fake_ocr_ok):
        code = main(
            [
                "--input", str(multi_pdf),
                "--langs", "bn",
                "--out", str(out),
            ]
        )
    assert code == 0
    pages = sorted((out / "source" / "pages").glob("page_*.png"))
    assert len(pages) == 3
    assert all(p.stat().st_size > 0 for p in pages)
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["page_count"] == 3
    assert report["phase"] == 2
    assert report["languages"]["bn"]["status"] == "pending"


def test_cli_output_layout_contract(tiny_pdf: Path, tmp_path: Path) -> None:
    out = tmp_path / "layout"
    with patch("bodhan_lesson.cli.run_ocr_on_pages", side_effect=_fake_ocr_ok):
        assert main(["--input", str(tiny_pdf), "--langs", "hi", "--out", str(out)]) == 0
    assert (out / "source" / "pages" / "page_001.png").is_file()
    assert (out / "source" / "pages" / "page_001.md").is_file()
    assert (out / "hi" / "narration").is_dir()
    assert (out / "report.json").is_file()
