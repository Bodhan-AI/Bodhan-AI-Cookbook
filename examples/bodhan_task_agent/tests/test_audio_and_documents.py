from __future__ import annotations

import math
from pathlib import Path
from time import monotonic

import pytest
from conftest import make_audio, make_pdf, make_png, needs_ffmpeg, needs_poppler, speech_wav

from bodhan_mcp.exceptions import BodhanToolError
from bodhan_mcp.schemas.ocr import parse_pages
from bodhan_mcp.utils.audio import (
    detect_silences,
    extract_chunks,
    join_audio,
    plan_chunks,
    probe_duration,
    sniff_audio_format,
)
from bodhan_mcp.utils.documents import (
    check_pages,
    detect_document,
    prepare_image,
    probe_pdf_pages,
    render_pdf_pages,
    sniff_document,
)


def _deadline(seconds: float = 60.0) -> float:
    return monotonic() + seconds


@pytest.mark.parametrize("duration", [3.0, 25.0, 25.1, 31.3, 70.0, 600.0])
def test_plan_chunks_covers_audio_within_limits(duration: float) -> None:
    spans = plan_chunks(duration, [])
    assert spans[0][0] == 0 and math.isclose(spans[-1][1], duration)
    assert all(a[1] == b[0] for a, b in zip(spans, spans[1:], strict=False))
    assert all(end - start <= 25.0 + 1e-9 for start, end in spans)
    if len(spans) > 1:
        assert all(end - start >= 5.0 - 1e-9 for start, end in spans)


def test_plan_chunks_prefers_latest_silence_and_ignores_bad_ones() -> None:
    silences = [(9.8, 10.2), (19.8, 20.2), (float("nan"), 3.0), (60.0, 80.0)]
    assert plan_chunks(40.0, silences)[0] == (0.0, 20.0)
    with pytest.raises(ValueError):
        plan_chunks(10.0, [], max_chunk_s=8.0, min_chunk_s=5.0)


def test_sniffers() -> None:
    assert sniff_audio_format(speech_wav(0.1)[:16]) == "wav"
    assert sniff_audio_format(b'{"error":') is None
    assert sniff_document(b"%PDF-1.4") == "pdf"
    assert sniff_document(b"\x89PNG\r\n\x1a\n") == "png"
    assert sniff_document(b"\xff\xd8\xff\xe0") == "jpeg"
    assert sniff_document(b"<html>") is None


@pytest.mark.parametrize("spec", ["0", "3-1", "1,1", "1-", "a", "1;2", ""])
def test_parse_pages_rejects_bad_specs(spec: str) -> None:
    with pytest.raises(ValueError):
        parse_pages(spec)


def test_parse_and_check_pages() -> None:
    assert parse_pages(" 1, 3,5-7 ") == [1, 3, 5, 6, 7]
    check_pages([1, 3], 3, 20)
    with pytest.raises(BodhanToolError) as info:
        check_pages([4], 3, 20)
    assert info.value.code == "invalid_input"
    with pytest.raises(BodhanToolError) as info:
        check_pages([1, 2, 3], 3, 2)
    assert info.value.code == "input_too_large"


@needs_ffmpeg
async def test_silence_detection_and_chunk_extraction(tmp_path: Path) -> None:
    audio = make_audio(tmp_path / "gaps.wav", 40.0, quiet=[(18.0, 21.0)])
    assert math.isclose(await probe_duration(audio, deadline=_deadline()), 40.0, abs_tol=0.05)
    silences = await detect_silences(audio, deadline=_deadline())
    assert any(start <= 18.5 and end >= 20.5 for start, end in silences)
    spans = plan_chunks(40.0, silences)
    assert 18.0 <= spans[0][1] <= 21.0
    work = tmp_path / "work"
    work.mkdir()
    chunks = await extract_chunks(audio, spans, work, deadline=_deadline())
    lengths = [await probe_duration(chunk, deadline=_deadline()) for chunk in chunks]
    assert math.isclose(sum(lengths), 40.0, abs_tol=0.1)


@needs_ffmpeg
async def test_join_audio_writes_label_and_refuses_existing(tmp_path: Path) -> None:
    parts = []
    for index in range(3):
        part = tmp_path / f"p{index}.wav"
        part.write_bytes(speech_wav(1.0))
        parts.append(part)
    joined = await join_audio(parts, tmp_path / "out.mp3", deadline=_deadline(), fmt="mp3", comment="AI-generated")
    assert sniff_audio_format(joined.read_bytes()[:16]) == "mp3"
    assert math.isclose(await probe_duration(joined, deadline=_deadline()), 3.5, abs_tol=0.15)
    assert b"AI-generated" in joined.read_bytes()[:4096]
    with pytest.raises(BodhanToolError) as info:
        await join_audio(parts, joined, deadline=_deadline(), fmt="mp3")
    assert info.value.code == "file_exists"


@needs_ffmpeg
async def test_unreadable_audio_and_expired_deadline(tmp_path: Path) -> None:
    fake = tmp_path / "fake.mp3"
    fake.write_bytes(b"not audio at all" * 100)
    with pytest.raises(BodhanToolError) as info:
        await probe_duration(fake, deadline=_deadline())
    assert info.value.code == "unsupported_file"
    with pytest.raises(BodhanToolError) as info:
        await probe_duration(fake, deadline=monotonic() - 1)
    assert info.value.code == "tool_deadline_exceeded"


@needs_ffmpeg
async def test_prepare_image_caps_size_and_trusts_content(tmp_path: Path) -> None:
    big = make_png(tmp_path / "big.png", 4000, 3000)
    disguised = tmp_path / "photo.jpg"
    disguised.write_bytes(make_png(tmp_path / "small.png").read_bytes())
    assert detect_document(disguised) == "png"
    work = tmp_path / "work"
    work.mkdir()
    prepared = await prepare_image(big, "png", work, deadline=_deadline())
    from bodhan_mcp.utils.documents import _image_size

    assert await _image_size(prepared, deadline=_deadline()) == (2000, 1500)
    broken = tmp_path / "broken.png"
    broken.write_bytes(big.read_bytes()[:200])
    with pytest.raises(BodhanToolError) as info:
        await prepare_image(broken, "png", work, deadline=_deadline())
    assert info.value.code == "unsupported_file"


def test_detect_document_rejects_unknown(tmp_path: Path) -> None:
    page = tmp_path / "page.html"
    page.write_text("<html></html>")
    with pytest.raises(BodhanToolError):
        detect_document(page)
    text = tmp_path / "notes.png"
    text.write_text("plain text pretending to be an image")
    with pytest.raises(BodhanToolError):
        detect_document(text)


@needs_poppler
async def test_pdf_pages_render_in_requested_order(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "doc.pdf", 3)
    assert await probe_pdf_pages(pdf, deadline=_deadline()) == 3
    work = tmp_path / "work"
    work.mkdir()
    rendered = await render_pdf_pages(pdf, [1, 3], work, deadline=_deadline())
    assert [path.name for path in rendered] == ["page_0001.png", "page_0003.png"]
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(pdf.read_bytes()[:60])
    with pytest.raises(BodhanToolError) as info:
        await probe_pdf_pages(broken, deadline=_deadline())
    assert info.value.code == "unsupported_file"
