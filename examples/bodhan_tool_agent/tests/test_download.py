"""Unit tests for Curated Indic Dataset Downloader (src.common.download)."""

from __future__ import annotations

from pathlib import Path
import sys
from unittest.mock import MagicMock, patch

from src.common.download import (
    CATALOG,
    download_file,
    fetch_sample_dataset,
    main,
    purge_sample_dataset,
)


def test_catalog_completeness():
    """Verify that catalog covers all demo tasks with valid public URLs."""
    assert "demo1_pdf" in CATALOG
    assert "demo2_audio" in CATALOG
    assert "demo3_image" in CATALOG
    assert "demo4_clip1_te" in CATALOG
    assert "demo4_clip2_ta" in CATALOG
    assert "demo4_clip3_bn" in CATALOG
    assert "demo5_text" in CATALOG
    assert "fail_refusal_video" in CATALOG
    assert "fail_corrupt_audio" in CATALOG
    assert "fail_empty_file" in CATALOG

    for key, spec in CATALOG.items():
        assert spec.key == key
        assert spec.filename
        assert spec.url
        assert spec.demo_task
        assert isinstance(spec.is_synthetic, bool)
        assert spec.provenance_type in ("synthetic", "non-synthetic")
        if spec.is_synthetic:
            assert spec.provenance_type == "synthetic"
        else:
            assert spec.provenance_type == "non-synthetic"

    # Non-synthetic authentic assets
    for key in ("demo1_pdf", "demo2_audio", "demo3_image", "demo4_clip1_te", "demo4_clip2_ta", "demo4_clip3_bn", "demo5_text", "fail_refusal_video"):
        assert not CATALOG[key].is_synthetic
        assert CATALOG[key].provenance_type == "non-synthetic"

    # Synthetic boundary fixtures
    for key in ("fail_corrupt_audio", "fail_empty_file", "edge_audio_boundary_29s", "edge_doc_dual_column_hindi"):
        assert CATALOG[key].is_synthetic
        assert CATALOG[key].provenance_type == "synthetic"


def test_download_file_mocked(tmp_path: Path):
    """Test streaming chunk download with mocked urllib response."""
    dest = tmp_path / "sample.txt"
    mock_response = MagicMock()
    mock_response.read.side_effect = [b"Chunk 1: Indic text. ", b"Chunk 2: Bengali danda \xe0\xa7\xa4", b""]
    mock_response.__enter__.return_value = mock_response

    with patch("urllib.request.urlopen", return_value=mock_response):
        out = download_file("https://example.org/sample.txt", dest)

    assert out.exists()
    assert dest.read_bytes() == b"Chunk 1: Indic text. Chunk 2: Bengali danda \xe0\xa7\xa4"


def test_fetch_sample_dataset_cached(tmp_path: Path):
    """Test fetch_sample_dataset idempotency with existing cached files."""
    dest_dir = tmp_path / "sample_data"
    dest_dir.mkdir(parents=True, exist_ok=True)
    # Pre-populate a fake file for demo1_pdf
    pdf_path = dest_dir / CATALOG["demo1_pdf"].filename
    pdf_path.write_bytes(b"%PDF-1.4 mock content")

    manifest = fetch_sample_dataset(dest_dir, keys=["demo1_pdf"], force=False, use_network=False)
    assert "demo1_pdf" in manifest
    assert manifest["demo1_pdf"] == pdf_path
    assert manifest["demo1_pdf"].read_bytes() == b"%PDF-1.4 mock content"


def test_purge_sample_dataset(tmp_path: Path):
    """Test purge_sample_dataset removes media files while keeping .gitkeep and DATASETS.md."""
    dest_dir = tmp_path / "sample_data"
    dest_dir.mkdir(parents=True, exist_ok=True)
    gitkeep = dest_dir / ".gitkeep"
    gitkeep.write_text("")
    catalog_doc = dest_dir / "DATASETS.md"
    catalog_doc.write_text("# Catalog")
    media1 = dest_dir / "demo1.pdf"
    media1.write_bytes(b"%PDF dummy")
    media2 = dest_dir / "audio.wav"
    media2.write_bytes(b"RIFF dummy")

    purged = purge_sample_dataset(dest_dir)
    assert purged == 2
    assert gitkeep.exists()
    assert catalog_doc.exists()
    assert not media1.exists()
    assert not media2.exists()


def test_main_generates_random_samples_by_default(tmp_path: Path, capsys):
    with (
        patch.object(sys, "argv", ["download", "--dest-dir", str(tmp_path)]),
        patch("src.common.download.fetch_sample_dataset", return_value={}),
        patch(
            "src.common.random_sampler.generate_random_multimodal_sample",
            return_value={
                "speech": [tmp_path / "speech.wav"],
                "images": [tmp_path / "image.png"],
                "documents": [tmp_path / "documents" / "sample_doc_03_te_table_matrix.pdf"],
                "text": [tmp_path / "text.txt"],
            },
        ) as generate_samples,
    ):
        main()

    generate_samples.assert_called_once_with(tmp_path, preserve_existing=True)
    assert "documents: 1" in capsys.readouterr().out


def test_main_with_selected_keys_skips_random_samples(tmp_path: Path):
    with (
        patch.object(sys, "argv", ["download", "--dest-dir", str(tmp_path), "--keys", "demo1_pdf"]),
        patch("src.common.download.fetch_sample_dataset", return_value={}),
        patch("src.common.random_sampler.generate_random_multimodal_sample") as generate_samples,
    ):
        main()

    generate_samples.assert_not_called()

