"""Unit tests for file resolution and security boundaries."""

from __future__ import annotations

import pytest
from pathlib import Path
from bodhan_mcp.errors import FileAccessError, InvalidInputError
from bodhan_mcp.tools.file_utils import resolve_safe_path, sanitize_filename


def test_resolve_safe_path_existing(tmp_path: Path):
    f = tmp_path / "test.txt"
    f.write_text("sample content")

    resolved = resolve_safe_path(f, base_dir=tmp_path, must_exist=True)
    assert resolved == f.resolve()


def test_resolve_safe_path_missing(tmp_path: Path):
    f = tmp_path / "non_existent.wav"
    with pytest.raises(FileAccessError):
        resolve_safe_path(f, must_exist=True)


def test_resolve_safe_path_empty_file(tmp_path: Path):
    f = tmp_path / "empty.wav"
    f.touch()
    with pytest.raises(InvalidInputError):
        resolve_safe_path(f, must_exist=True)


def test_resolve_safe_path_null_byte():
    with pytest.raises(FileAccessError):
        resolve_safe_path("evil\0path.txt", must_exist=False)


def test_sanitize_filename():
    assert sanitize_filename("my file / bad:name*.txt") == "my_file___bad_name_.txt"
