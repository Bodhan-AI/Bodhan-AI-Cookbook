"""Unit tests for page image preparation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import fitz
import pytest
from PIL import Image

from bodhan_lesson.limits import MAX_INPUT_BYTES, MAX_PAGES
from bodhan_lesson.render_pages import (
    clear_old_pages,
    list_image_files,
    prepare_page_images,
)


def test_list_image_files_sorted_and_skips_non_images(image_folder: Path) -> None:
    files = list_image_files(image_folder)
    assert [p.name for p in files] == ["a.png", "b.png"]


def test_list_image_files_skips_unsupported_formats(tmp_path: Path) -> None:
    folder = tmp_path / "mixed"
    folder.mkdir()
    Image.new("RGB", (8, 8), color=(1, 2, 3)).save(folder / "ok.png")
    (folder / "scan.webp").write_bytes(b"not-really-webp")
    (folder / "scan.tif").write_bytes(b"not-really-tif")
    files = list_image_files(folder)
    assert [p.name for p in files] == ["ok.png"]


def test_prepare_from_image_folder(image_folder: Path, tmp_path: Path) -> None:
    pages_dir = tmp_path / "out_pages"
    paths = prepare_page_images(image_folder, pages_dir)
    assert len(paths) == 2
    assert paths[0].name == "page_001.png"
    assert paths[1].name == "page_002.png"
    assert all(p.exists() and p.stat().st_size > 0 for p in paths)


def test_empty_folder_raises(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError, match="No page images"):
        prepare_page_images(empty, tmp_path / "pages")


def test_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="does not exist"):
        prepare_page_images(tmp_path / "missing", tmp_path / "pages")


def test_non_pdf_file_raises(tmp_path: Path) -> None:
    bad = tmp_path / "notes.txt"
    bad.write_text("nope", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a PDF"):
        prepare_page_images(bad, tmp_path / "pages")


def test_prepare_from_tiny_pdf(tiny_pdf: Path, tmp_path: Path) -> None:
    pages = prepare_page_images(tiny_pdf, tmp_path / "pages")
    assert len(pages) == 1
    assert pages[0].name == "page_001.png"
    assert pages[0].stat().st_size > 0


def test_prepare_from_multi_pdf(multi_pdf: Path, tmp_path: Path) -> None:
    pages = prepare_page_images(multi_pdf, tmp_path / "pages")
    assert [p.name for p in pages] == ["page_001.png", "page_002.png", "page_003.png"]


def test_rerun_clears_old_pages(image_folder: Path, tmp_path: Path) -> None:
    pages_dir = tmp_path / "pages"
    prepare_page_images(image_folder, pages_dir)
    stale = pages_dir / "page_099.png"
    Image.new("RGB", (8, 8), color=(1, 2, 3)).save(stale)
    prepare_page_images(image_folder, pages_dir)
    assert not stale.exists()
    assert len(list(pages_dir.glob("page_*"))) == 2


def test_clear_old_pages_noop_if_missing(tmp_path: Path) -> None:
    clear_old_pages(tmp_path / "does_not_exist")  # should not raise


def test_rejects_folder_over_page_limit(tmp_path: Path) -> None:
    folder = tmp_path / "many"
    folder.mkdir()
    for i in range(MAX_PAGES + 1):
        Image.new("RGB", (4, 4), color=(i, i, i)).save(folder / f"p{i:03d}.png")
    with pytest.raises(ValueError, match=f"{MAX_PAGES + 1} pages"):
        prepare_page_images(folder, tmp_path / "pages")


def test_rejects_pdf_over_page_limit(tmp_path: Path) -> None:
    pdf = tmp_path / "too_many.pdf"
    doc = fitz.open()
    for i in range(MAX_PAGES + 1):
        page = doc.new_page(width=200, height=100)
        page.insert_text((20, 40), f"{i}")
    doc.save(str(pdf))
    doc.close()
    with pytest.raises(ValueError, match=f"{MAX_PAGES + 1} pages"):
        prepare_page_images(pdf, tmp_path / "pages")


def test_rejects_oversized_input(tiny_pdf: Path, tmp_path: Path) -> None:
    with patch(
        "bodhan_lesson.limits.input_size_bytes",
        return_value=MAX_INPUT_BYTES + 1,
    ):
        with pytest.raises(ValueError, match="max allowed is 30 MB"):
            prepare_page_images(tiny_pdf, tmp_path / "pages")
