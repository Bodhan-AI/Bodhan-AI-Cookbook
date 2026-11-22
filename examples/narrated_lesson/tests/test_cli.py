"""Unit tests for CLI helpers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from bodhan_lesson.cli import (
    ensure_output_dirs,
    main,
    parse_args,
    write_report,
)
from bodhan_lesson.limits import MAX_PAGES


def _fake_ocr_ok(pages_dir: Path, **kwargs):  # noqa: ANN003
    images = sorted(pages_dir.glob("page_*.*"))
    images = [p for p in images if p.suffix.lower() in {".png", ".jpg", ".jpeg"}]
    return [
        {
            "page": img.name,
            "stem": img.stem,
            "status": "ok",
            "block_count": 1,
            "md_path": str(pages_dir / f"{img.stem}.md"),
            "ocr_json_path": str(pages_dir / f"{img.stem}.ocr.json"),
            "usage": {},
            "error": None,
            "latency_s": 0.01,
            "retries": 0,
        }
        for img in images
    ]


def test_parse_args_required_flags() -> None:
    args = parse_args(["--input", "in", "--langs", "hi,ta", "--out", "out"])
    assert args.input == "in"
    assert args.langs == "hi,ta"
    assert args.out == "out"
    assert args.code_mixed is False


def test_parse_args_optional_flags() -> None:
    args = parse_args(
        [
            "--input", "in",
            "--langs", "hi",
            "--out", "out",
            "--source-lang", "en",
            "--glossary", "g.yml",
            "--voices", "v.yml",
            "--code-mixed",
        ]
    )
    assert args.source_lang == "en"
    assert args.glossary == "g.yml"
    assert args.voices == "v.yml"
    assert args.code_mixed is True


def test_langs_split_and_trim(image_folder: Path, tmp_path: Path) -> None:
    out = tmp_path / "run"
    with patch("bodhan_lesson.cli.run_ocr_on_pages", side_effect=_fake_ocr_ok):
        code = main(
            [
                "--input", str(image_folder),
                "--langs", "hi, ta, bn",
                "--out", str(out),
            ]
        )
    assert code == 0
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["langs"] == ["hi", "ta", "bn"]
    assert report["phase"] == 2


def test_empty_langs_returns_2(tmp_path: Path) -> None:
    code = main(["--input", str(tmp_path), "--langs", ",,", "--out", str(tmp_path / "o")])
    assert code == 2


def test_unsupported_langs_returns_2(tmp_path: Path) -> None:
    code = main(
        [
            "--input", str(tmp_path),
            "--langs", "hi,xx",
            "--out", str(tmp_path / "o"),
        ]
    )
    assert code == 2


def test_unsupported_source_lang_returns_2(tmp_path: Path) -> None:
    code = main(
        [
            "--input", str(tmp_path),
            "--langs", "hi",
            "--source-lang", "zz",
            "--out", str(tmp_path / "o"),
        ]
    )
    assert code == 2


def test_ensure_output_dirs(tmp_path: Path) -> None:
    out = tmp_path / "out"
    ensure_output_dirs(out, ["hi", "ta"])
    assert (out / "source" / "pages").is_dir()
    assert (out / "hi" / "narration").is_dir()
    assert (out / "ta" / "narration").is_dir()


def test_write_report(tmp_path: Path) -> None:
    args = parse_args(["--input", "sample", "--langs", "hi", "--out", str(tmp_path)])
    ocr_pages = [
        {"page": "page_001.png", "status": "ok", "block_count": 2, "error": None},
        {"page": "page_002.png", "status": "error", "block_count": 0, "error": "boom"},
    ]
    path = write_report(tmp_path, args, ["hi"], page_count=2, ocr_pages=ocr_pages)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["phase"] == 2
    assert data["status"] == "ocr_partial"
    assert data["ocr"]["ok"] == 1
    assert data["ocr"]["failed"] == 1
    assert data["languages"]["hi"]["status"] == "pending"


def test_main_missing_input_returns_2(tmp_path: Path) -> None:
    code = main(
        [
            "--input", str(tmp_path / "missing"),
            "--langs", "hi",
            "--out", str(tmp_path / "out"),
        ]
    )
    assert code == 2


def test_main_over_page_limit_returns_2(tmp_path: Path) -> None:
    from PIL import Image

    folder = tmp_path / "many"
    folder.mkdir()
    for i in range(MAX_PAGES + 1):
        Image.new("RGB", (4, 4), color=(1, 1, 1)).save(folder / f"{i}.png")
    code = main(
        [
            "--input", str(folder),
            "--langs", "hi",
            "--out", str(tmp_path / "out"),
        ]
    )
    assert code == 2
