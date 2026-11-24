"""Audio windowing under the 30 s model limit, and multi-page document handling."""

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from bodhan_mcp import audio, documents, errors
from fakes import tone_wav


def _pcm(wav: bytes) -> tuple[np.ndarray, int]:
    x, sr = audio.wav_bytes_to_pcm(wav)
    return audio._resample(x, sr, audio.TRANSCRIBE_SR), audio.TRANSCRIBE_SR


def test_segments_never_exceed_model_window_and_cut_at_pauses():
    bursts = [(i * 4.0, i * 4.0 + 3.2) for i in range(20)]  # 80 s of speech with 0.8 s pauses
    x, sr = _pcm(tone_wav(80.0, bursts=bursts))
    segs = audio.segment_speech(x, sr)
    assert len(segs) >= 3
    assert all(s.duration <= 30.0 for s in segs)
    for s in segs[:-1]:  # every cut lands in a pause, not inside a burst
        assert any(b[1] - 0.2 <= s.end <= b[1] + 1.0 for b in bursts)


def test_continuous_speech_longer_than_window_is_still_split():
    x, sr = _pcm(tone_wav(75.0))
    segs = audio.segment_speech(x, sr)
    assert len(segs) >= 3 and all(s.duration <= 30.0 for s in segs)
    assert segs[-1].end == pytest.approx(75.0, abs=0.5)


def test_silence_produces_no_segments():
    x, sr = _pcm(tone_wav(10.0, bursts=[]))
    assert audio.segment_speech(x, sr) == []


def test_encode_mp3_and_probe(tmp_path: Path):
    if not audio.have_ffmpeg():
        pytest.skip("ffmpeg not installed")
    x, sr = audio.wav_bytes_to_pcm(tone_wav(2.0))
    out = audio.encode(x, sr, tmp_path / "a.mp3", title="t")
    meta = audio.probe(out)
    assert meta["has_audio"] and 1.8 < meta["duration_s"] < 2.3


def _pdf(tmp: Path, pages: int) -> Path:
    imgs = [Image.new("RGB", (600, 800), (255, 255, 255 - i)) for i in range(pages)]
    p = tmp / "doc.pdf"
    imgs[0].save(p, save_all=True, append_images=imgs[1:])
    return p


def test_pdf_pages_render_and_page_spec(tmp_path: Path):
    p = _pdf(tmp_path, 5)
    assert documents.pdf_page_count(p) == 5
    assert documents.parse_page_spec("1-2,5,9", 5) == [1, 2, 5]
    assert documents.parse_page_spec(None, 3) == [1, 2, 3]
    rendered = documents.render_pdf_pages(p, [1, 3])
    assert [r[0] for r in rendered] == [1, 3] and all(r[2] in {"image/png", "image/jpeg"} for r in rendered)
    with pytest.raises(errors.BodhanError):
        documents.parse_page_spec("7-9", 5)
    with pytest.raises(errors.BodhanError):
        documents.parse_page_spec("abc", 5)


def test_large_images_are_downscaled(tmp_path: Path):
    p = tmp_path / "big.png"
    Image.new("RGB", (5000, 3000), "white").save(p)
    data, _ = documents.load_image_page(p)
    with Image.open(__import__("io").BytesIO(data)) as img:
        assert max(img.size) <= documents.MAX_LONG_SIDE
