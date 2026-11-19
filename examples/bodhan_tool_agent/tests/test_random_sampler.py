"""Unit tests for Random Multimodal Sampler & Statistical Profiling (src.common.random_sampler)."""

from __future__ import annotations

import math
from pathlib import Path
import pytest

from src.common.random_sampler import (
    compute_sample_statistics,
    extract_speech_sample_features,
    extract_image_sample_features,
    extract_document_sample_features,
    extract_text_sample_features,
    generate_random_multimodal_sample,
)


def test_compute_sample_statistics_analytical():
    """Verify statistical estimators against analytical values."""
    data = [10.0, 20.0, 30.0, 40.0, 50.0]
    stats = compute_sample_statistics(data)

    assert stats["n"] == 5
    assert stats["mean"] == 30.0
    # Sample variance: sum((x - 30)^2) / 4 = (400 + 100 + 0 + 100 + 400) / 4 = 1000 / 4 = 250.0
    assert stats["var"] == 250.0
    # Sample std = sqrt(250) = 15.811...
    assert stats["std"] == pytest.approx(15.81, abs=0.02)
    # SE = 15.811 / sqrt(5) = 7.071...
    assert stats["se"] == pytest.approx(7.071, abs=0.01)
    # Margin 95 = 1.96 * 7.071 = 13.859...
    assert stats["margin_95"] == pytest.approx(13.86, abs=0.02)
    assert stats["ci_95_low"] == pytest.approx(30.0 - 13.86, abs=0.02)
    assert stats["ci_95_high"] == pytest.approx(30.0 + 13.86, abs=0.02)
    assert stats["median"] == 30.0
    assert stats["skewness"] == pytest.approx(0.0, abs=0.01)


def test_compute_sample_statistics_empty():
    """Test empty input handling."""
    stats = compute_sample_statistics([])
    assert stats["n"] == 0
    assert stats["mean"] == 0.0
    assert stats["se"] == 0.0


def test_extract_text_sample_features():
    """Verify linguistic and lexical metrics extraction."""
    indic_text = (
        "भारत विविध भाषाओं का देश है जहाँ प्रत्येक राज्य की अपनी विशिष्ट पहचान है। "
        "यह भाषा अत्यंत समृद्ध और प्राचीन है।"
    )
    feats = extract_text_sample_features(indic_text, "test_hi.txt")

    assert feats["filename"] == "test_hi.txt"
    assert feats["total_tokens"] > 0
    assert 0.0 < feats["ttr"] <= 1.0
    assert 0.0 <= feats["hapax_ratio"] <= 1.0
    assert feats["primary_script"] == "Devanagari"
    assert feats["sentence_count"] >= 2


def test_generate_random_multimodal_sample(tmp_path: Path):
    """Verify generation of partitioned multimodal subfolders."""
    manifest = generate_random_multimodal_sample(tmp_path, seed=42)

    assert len(manifest["speech"]) == 20
    assert len(manifest["images"]) == 16
    assert len(manifest["documents"]) == 12
    assert len(manifest["text"]) == 25


def test_generate_random_multimodal_sample_preserves_existing_files(tmp_path: Path):
    existing_pdf = tmp_path / "documents" / "sample_doc_03_te_table_matrix.pdf"
    existing_pdf.parent.mkdir(parents=True)
    original_bytes = b"existing PDF bytes"
    existing_pdf.write_bytes(original_bytes)

    manifest = generate_random_multimodal_sample(tmp_path, seed=42, preserve_existing=True)

    assert existing_pdf in manifest["documents"]
    assert existing_pdf.read_bytes() == original_bytes

    # Verify subfolders exist and contain non-empty files
    for sub in ("speech", "images", "documents", "text"):
        sub_dir = tmp_path / sub
        assert sub_dir.is_dir()
        files = list(sub_dir.glob("*"))
        assert len(files) > 0
        for f in files:
            assert f.stat().st_size > 0

    # Test feature extraction on generated sample
    sample_wav = manifest["speech"][0]
    speech_stats = extract_speech_sample_features(sample_wav)
    assert speech_stats["duration_sec"] > 0
    assert speech_stats["sample_rate"] in (8000, 24000)

    sample_img = manifest["images"][0]
    img_stats = extract_image_sample_features(sample_img)
    assert img_stats["width_px"] > 0
    assert 0.0 <= img_stats["michelson_contrast"] <= 1.0

    sample_doc = manifest["documents"][0]
    doc_stats = extract_document_sample_features(sample_doc)
    assert doc_stats["page_count"] == 1
    assert doc_stats["text_words"] > 0
