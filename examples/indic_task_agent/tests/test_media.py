"""media.py against real ffmpeg/ffprobe, generated fixtures only."""
from __future__ import annotations

import io
import shutil
import subprocess
from itertools import pairwise

import pymupdf
import pytest
from PIL import Image

from bodhan_mcp.core import media
from bodhan_mcp.core.errors import BodhanError

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe not installed"
)


@pytest.fixture(scope="module")
def work(tmp_path_factory):
    return tmp_path_factory.mktemp("media")


def _tone(path, seconds: float, tone_s: int = 6, gap_s: int = 1):
    expr = f"0.3*sin(2*PI*440*t)*lt(mod(t\\,{tone_s + gap_s})\\,{tone_s})"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"aevalsrc={expr}:s=16000:d={seconds}", str(path)],
        check=True,
    )
    return path


@pytest.fixture(scope="module")
def gap_wav(work):
    """61 s of 6 s tone + 1 s silence: plenty of pauses."""
    return _tone(work / "gap.wav", 61)


@pytest.fixture(scope="module")
def plain_wav(work):
    """61 s without a single pause."""
    path = work / "plain.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=61", str(path)],
        check=True,
    )
    return path


@pytest.fixture(scope="module")
def pdf_path(work):
    doc = pymupdf.open()
    for i in range(2):
        doc.new_page().insert_text((72, 72), f"page {i + 1}")
    path = work / "two-pages.pdf"
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture(scope="module")
def encrypted_pdf(work, pdf_path):
    doc = pymupdf.open(str(pdf_path))
    path = work / "locked.pdf"
    doc.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="pw")
    doc.close()
    return path


def test_probe_audio(gap_wav):
    probe = media.probe_audio(gap_wav)
    assert probe["has_audio"] is True
    assert probe["sample_rate"] == 16000
    assert probe["channels"] == 1
    assert probe["codec"] == "pcm_s16le"
    assert 60.5 < probe["duration_s"] < 61.5


def test_windows_break_at_pauses(gap_wav):
    windows = media.silence_cut_points(gap_wav)
    assert len(windows) >= 3
    assert windows[0][0] == 0.0
    assert abs(windows[-1][1] - 61.0) < 0.2
    for start, end, cut in windows:
        assert end > start
        assert end - start <= media.ASR_MAX_WINDOW_S
        assert cut in {"pause", "hard"}
    assert sum(1 for *_, cut in windows if cut == "pause") >= 2
    assert all(a[1] == b[0] for a, b in pairwise(windows)), "windows must tile the file"


def test_windows_without_pauses_are_hard_cut(plain_wav):
    windows = media.silence_cut_points(plain_wav)
    assert len(windows) >= 3
    assert all(cut == "hard" for *_, cut in windows)
    for start, end, _ in windows:
        assert 0 < end - start <= media.ASR_MAX_WINDOW_S
    assert sum(end - start for start, end, _ in windows) > 60.9


def test_to_wav_converts_m4a_it_made(work, tmp_path):
    short = _tone(work / "short.wav", 2, tone_s=2, gap_s=0)
    m4a = work / "short.m4a"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(short), "-c:a", "aac", str(m4a)], check=True
    )
    out = media.to_wav(m4a, tmp_path / "short-16k.wav")
    probe = media.probe_audio(out)
    assert probe["sample_rate"] == 16000
    assert probe["channels"] == 1
    assert probe["codec"] == "pcm_s16le"
    assert 1.8 < probe["duration_s"] < 2.3


def test_to_wav_rejects_undecodable(tmp_path):
    junk = tmp_path / "notes.txt"
    junk.write_text("not audio at all", encoding="utf-8")
    with pytest.raises(BodhanError) as err:
        media.to_wav(junk, tmp_path / "out.wav")
    assert err.value.code == "UNSUPPORTED_FORMAT"


def test_wav_slice_is_a_wav(gap_wav, tmp_path):
    blob = media.wav_slice(gap_wav, 1.0, 3.0)
    assert blob[:4] == b"RIFF"
    slice_path = tmp_path / "slice.wav"
    slice_path.write_bytes(blob)
    assert abs(media.wav_duration(slice_path) - 2.0) < 0.05


def test_concat_and_mp3(work, tmp_path):
    first = _tone(work / "one.wav", 1, tone_s=1, gap_s=0)
    second = _tone(work / "two.wav", 1, tone_s=1, gap_s=0)
    joined = media.concat_wavs([first, second], tmp_path / "joined.wav", gap_ms=120)
    assert 2.05 < media.wav_duration(joined) < 2.25
    mp3 = media.encode_mp3(joined, tmp_path / "joined.mp3")
    assert mp3.stat().st_size > 1000
    assert media.probe_audio(mp3)["codec"] == "mp3"


def test_is_silent(work):
    silent = work / "silence.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "aevalsrc=0:s=16000:d=1", str(silent)],
        check=True,
    )
    assert media.is_silent(silent) is True
    assert media.is_silent(_tone(work / "loud.wav", 1, tone_s=1, gap_s=0)) is False


def test_probe_video_and_its_audio(work, tmp_path):
    clip = work / "clip.mkv"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:v", "mpeg4", "-c:a", "aac",
         "-shortest", str(clip)],
        check=True,
    )
    probe = media.probe_video(clip)
    assert probe["has_audio"] is True
    assert probe["width"] == 64
    assert probe["height"] == 64
    assert probe["duration_s"] > 0
    assert media.probe_audio(media.to_wav(clip, tmp_path / "from-video.wav"))["has_audio"] is True


def test_pdf_pages_render(pdf_path):
    assert media.pdf_page_count(pdf_path) == 2
    rendered = list(media.render_pdf_pages(pdf_path))
    assert [index for index, _ in rendered] == [0, 1]
    for _, jpeg in rendered:
        assert jpeg.startswith(b"\xff\xd8")
        with Image.open(io.BytesIO(jpeg)) as img:
            assert img.format == "JPEG"
            assert max(img.size) <= 2400


def test_pdf_encrypted_and_missing(encrypted_pdf, tmp_path):
    with pytest.raises(BodhanError) as encrypted:
        media.pdf_page_count(encrypted_pdf)
    assert encrypted.value.code == "PDF_ENCRYPTED"
    with pytest.raises(BodhanError) as encrypted_render:
        list(media.render_pdf_pages(encrypted_pdf))
    assert encrypted_render.value.code == "PDF_ENCRYPTED"
    with pytest.raises(BodhanError) as missing:
        media.pdf_page_count(tmp_path / "absent.pdf")
    assert missing.value.code == "MISSING_FILE"


def test_image_to_jpeg_clamps_long_edge(tmp_path):
    source = tmp_path / "wide.png"
    Image.new("RGB", (3000, 600), "white").save(source)
    data = media.image_to_jpeg(source)
    assert data.startswith(b"\xff\xd8")
    with Image.open(io.BytesIO(data)) as img:
        assert img.format == "JPEG"
        assert max(img.size) == 2400
        assert img.size == (2400, 480)


def test_image_to_jpeg_rejects_other_formats(tmp_path):
    source = tmp_path / "anim.gif"
    Image.new("P", (10, 10)).save(source, format="GIF")
    with pytest.raises(BodhanError) as err:
        media.image_to_jpeg(source)
    assert err.value.code == "UNSUPPORTED_FORMAT"
