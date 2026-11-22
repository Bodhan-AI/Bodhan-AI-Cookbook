"""Unit tests for --from stage resume helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bodhan_lesson.resume import (
    load_translations,
    reuse_plan,
    should_run,
    stage_index,
)


def test_should_run_full_pipeline() -> None:
    for stage in ("pages", "ocr", "layout", "speak", "qa"):
        assert should_run(None, stage) is True


def test_should_run_from_layout() -> None:
    assert should_run("layout", "pages") is False
    assert should_run("layout", "ocr") is False
    assert should_run("layout", "layout") is True
    assert should_run("layout", "translate") is True
    assert should_run("layout", "qa") is True


def test_should_run_from_speak() -> None:
    assert should_run("speak", "narrate") is False
    assert should_run("speak", "speak") is True
    assert should_run("speak", "qa") is True


def test_reuse_plan_from_ocr() -> None:
    plan = reuse_plan("ocr")
    assert plan["pages"] == "reuse"
    assert plan["ocr"] == "run"
    assert plan["qa"] == "run"


def test_stage_index_unknown() -> None:
    with pytest.raises(ValueError, match="Unknown stage"):
        stage_index("nope")


def test_load_translations(tmp_path: Path) -> None:
    path = tmp_path / "hi" / "lesson.hi.md"
    path.parent.mkdir(parents=True)
    path.write_text("hello\n", encoding="utf-8")
    rows = load_translations(tmp_path, ["hi"])
    assert rows[0]["status"] == "ok"
    assert rows[0]["reused"] is True


def test_load_translations_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="lesson"):
        load_translations(tmp_path, ["hi"])
