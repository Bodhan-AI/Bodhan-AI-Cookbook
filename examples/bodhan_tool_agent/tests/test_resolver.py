"""Tests for file resolution, matching logic, and tie-breaking (Slice 9)."""

from pathlib import Path

import pymupdf
import pytest
import soundfile as sf

from bodhan_agent.resolver import resolve


@pytest.fixture
def sample_inventory_dir(tmp_path: Path) -> Path:
    """Create a temporary directory with multiple typed files."""
    files_dir = tmp_path / "inputs"
    files_dir.mkdir(parents=True)

    # 1. audio1.wav
    sr = 16000
    sf.write(str(files_dir / "alpha_audio.wav"), [0.0] * sr, sr)

    # 2. audio2.wav
    sf.write(str(files_dir / "beta_audio.wav"), [0.0] * (sr * 2), sr)

    # 3. doc.pdf
    doc = pymupdf.open()
    doc.new_page(width=100, height=100)
    doc.save(str(files_dir / "gamma_doc.pdf"))
    doc.close()

    # 4. image.png
    (files_dir / "delta_image.png").write_bytes(
        bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de0000000c4944415408d763f8cfc000000301010018dd8d100000000049454e44ae426082")
    )
    return files_dir


def test_resolve_exact_filename(sample_inventory_dir: Path):
    """Verify exact filename match (case-insensitive)."""
    res = resolve(sample_inventory_dir, "Please transcribe ALPHA_AUDIO.WAV for me")
    assert len(res.selected) == 1
    selected_ref = next(f for f in res.inventory if f.id == res.selected[0])
    assert selected_ref.filename == "alpha_audio.wav"
    assert "exact filename" in res.rationale.lower()


def test_resolve_missing_explicit_filename(sample_inventory_dir: Path):
    """Verify task explicitly naming a non-existent file flags it without fabricating."""
    res = resolve(sample_inventory_dir, "Transcribe non_existent.wav please")
    assert len(res.selected) == 0
    assert res.missing_file_requested == "non_existent.wav"
    assert "does not exist" in res.rationale


def test_resolve_ordinal(sample_inventory_dir: Path):
    """Verify ordinal matching: first, second, and last."""
    # First
    res_first = resolve(sample_inventory_dir, "Transcribe the first file")
    assert res_first.selected[0] == "A"
    assert "first" in res_first.rationale.lower()

    # Second
    res_sec = resolve(sample_inventory_dir, "Transcribe the 2nd file")
    assert res_sec.selected[0] == "B"
    assert "second" in res_sec.rationale.lower()

    # Last
    res_last = resolve(sample_inventory_dir, "Transcribe the last file")
    assert res_last.selected[0] == "D"
    assert "last" in res_last.rationale.lower()


def test_resolve_plural(sample_inventory_dir: Path):
    """Verify plural matching selects all candidates or all matching a type."""
    # All files
    res_all = resolve(sample_inventory_dir, "Process all files in the folder")
    assert len(res_all.selected) == 4

    # Plural typed: clips
    res_clips = resolve(sample_inventory_dir, "Transcribe these audio clips")
    assert len(res_clips.selected) == 2  # alpha_audio.wav and beta_audio.wav
    assert all(
        next(f for f in res_clips.inventory if f.id == sel_id).kind == "audio"
        for sel_id in res_clips.selected
    )


def test_resolve_ambiguity_verb_consistency(sample_inventory_dir: Path):
    """Verify ambiguity resolves by task verb consistency (e.g. OCR selects PDF/image)."""
    # "read document" matches gamma_doc.pdf
    res = resolve(sample_inventory_dir, "Please summarize the scanned document")
    assert len(res.selected) == 1
    selected_ref = next(f for f in res.inventory if f.id == res.selected[0])
    assert selected_ref.kind == "pdf"


def test_resolve_ambiguity_llm_metadata_tiebreak(sample_inventory_dir: Path):
    """Verify LLM tie-breaker receives metadata only and selects candidate."""
    called_with_meta = []

    def mock_tiebreaker(task: str, meta_list: list[dict]):
        called_with_meta.extend(meta_list)
        return "B"  # Tie-breaker chooses ID B

    res = resolve(sample_inventory_dir, "Transcribe the audio recording", llm_tiebreaker=mock_tiebreaker)
    assert res.selected == ["B"]
    assert len(called_with_meta) == 2  # Both audio files inspected
    assert "LLM tie-break" in res.rationale
