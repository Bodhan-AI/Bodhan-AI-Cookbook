"""Unit tests for input size and page-count guards."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from bodhan_lesson.limits import (
    MAX_INPUT_BYTES,
    MAX_PAGES,
    assert_input_size_ok,
    assert_page_count_ok,
    input_size_bytes,
)


def test_max_pages_constant() -> None:
    assert MAX_PAGES == 30


def test_max_input_bytes_is_30_mb() -> None:
    assert MAX_INPUT_BYTES == 30 * 1024 * 1024


def test_page_count_allows_exactly_30() -> None:
    assert_page_count_ok(30)  # should not raise


def test_page_count_rejects_above_30() -> None:
    with pytest.raises(ValueError, match="31 pages"):
        assert_page_count_ok(31)


def test_input_size_ok_for_small_file(tmp_path: Path) -> None:
    path = tmp_path / "small.pdf"
    path.write_bytes(b"%PDF-1.4 tiny")
    assert_input_size_ok(path)


def test_input_size_rejects_over_30_mb(tmp_path: Path) -> None:
    path = tmp_path / "big.pdf"
    path.write_bytes(b"x")
    with patch("bodhan_lesson.limits.input_size_bytes", return_value=MAX_INPUT_BYTES + 1):
        with pytest.raises(ValueError, match="max allowed is 30 MB"):
            assert_input_size_ok(path)


def test_input_size_bytes_sums_images_only(tmp_path: Path) -> None:
    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "a.png").write_bytes(b"12345")
    (folder / "b.jpg").write_bytes(b"abcd")
    (folder / "skip.txt").write_bytes(b"not counted" * 10)
    assert input_size_bytes(folder) == 5 + 4
