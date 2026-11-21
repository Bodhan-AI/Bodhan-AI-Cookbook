"""Unit tests for CLI helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bodhan_lesson.cli import (
    ensure_output_dirs,
    main,
    parse_args,
    write_stub_report,
)
from bodhan_lesson.limits import MAX_PAGES


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


def test_empty_langs_returns_2(tmp_path: Path) -> None:
    code = main(["--input", str(tmp_path), "--langs", ",,", "--out", str(tmp_path / "o")])
    assert code == 2


def test_ensure_output_dirs(tmp_path: Path) -> None:
    out = tmp_path / "out"
    ensure_output_dirs(out, ["hi", "ta"])
    assert (out / "source" / "pages").is_dir()
    assert (out / "hi" / "narration").is_dir()
    assert (out / "ta" / "narration").is_dir()


def test_write_stub_report(tmp_path: Path) -> None:
    args = parse_args(["--input", "sample", "--langs", "hi", "--out", str(tmp_path)])
    path = write_stub_report(tmp_path, args, ["hi"], page_count=3)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["phase"] == 1
    assert data["status"] == "pages_ready"
    assert data["page_count"] == 3
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
