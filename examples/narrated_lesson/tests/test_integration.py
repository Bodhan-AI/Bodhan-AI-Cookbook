"""Integration tests: real filesystem CLI runs, no live APIs."""

from __future__ import annotations

import json
from pathlib import Path

from bodhan_lesson.cli import main


def test_cli_phase1_from_image_folder(image_folder: Path, tmp_path: Path) -> None:
    out = tmp_path / "out_images"
    code = main(
        [
            "--input", str(image_folder),
            "--langs", "hi,ta",
            "--out", str(out),
        ]
    )
    assert code == 0
    pages = sorted((out / "source" / "pages").glob("page_*"))
    assert len(pages) == 2
    assert (out / "hi" / "narration").is_dir()
    assert (out / "ta" / "narration").is_dir()
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["page_count"] == 2
    assert report["langs"] == ["hi", "ta"]
    assert report["phase"] == 1


def test_cli_phase1_from_pdf(multi_pdf: Path, tmp_path: Path) -> None:
    out = tmp_path / "out_pdf"
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
    assert report["languages"]["bn"]["status"] == "pending"


def test_cli_output_layout_contract(tiny_pdf: Path, tmp_path: Path) -> None:
    out = tmp_path / "layout"
    assert main(["--input", str(tiny_pdf), "--langs", "hi", "--out", str(out)]) == 0
    assert (out / "source" / "pages" / "page_001.png").is_file()
    assert (out / "hi" / "narration").is_dir()
    assert (out / "report.json").is_file()
