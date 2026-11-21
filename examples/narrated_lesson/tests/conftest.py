"""Shared fixtures for narrated-lesson tests."""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest
from PIL import Image


@pytest.fixture
def tiny_png(tmp_path: Path) -> Path:
    """Create a small valid PNG for image-folder tests."""
    path = tmp_path / "img_a.png"
    Image.new("RGB", (32, 32), color=(200, 100, 50)).save(path)
    return path


@pytest.fixture
def image_folder(tmp_path: Path) -> Path:
    """Folder with two PNGs plus a non-image file that should be ignored."""
    folder = tmp_path / "pages_in"
    folder.mkdir()
    Image.new("RGB", (24, 24), color=(10, 20, 30)).save(folder / "b.png")
    Image.new("RGB", (24, 24), color=(40, 50, 60)).save(folder / "a.png")
    (folder / "notes.txt").write_text("ignore me", encoding="utf-8")
    return folder


@pytest.fixture
def tiny_pdf(tmp_path: Path) -> Path:
    """One-page PDF built with PyMuPDF."""
    path = tmp_path / "one.pdf"
    doc = fitz.open()
    page = doc.new_page(width=300, height=200)
    page.insert_text((40, 80), "page one")
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def multi_pdf(tmp_path: Path) -> Path:
    """Three-page PDF for count/naming checks."""
    path = tmp_path / "three.pdf"
    doc = fitz.open()
    for i in range(3):
        page = doc.new_page(width=300, height=200)
        page.insert_text((40, 80), f"page {i + 1}")
    doc.save(str(path))
    doc.close()
    return path
