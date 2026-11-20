import subprocess

import pytest
from bodhan_mcp import media
from conftest import needs_ffmpeg
from PIL import Image


def test_plan_windows_never_exceeds_limit_and_prefers_silence():
    windows = media.plan_windows(70.0, cuts=[20.0, 41.5, 60.0])
    assert all(end - start <= media.MAX_WINDOW_S + 1e-9 for start, end in windows)
    assert windows[0][1] == 20.0  # latest silence in the preferred band, not the hard limit
    assert windows[-1][1] == 70.0
    assert [round(s, 3) for s, _ in windows][1:] == [round(e, 3) for _, e in windows][:-1]


def test_plan_windows_short_audio_is_one_window():
    assert media.plan_windows(10.0, cuts=[]) == [(0.0, 10.0)]


def test_plan_windows_hard_cut_without_silence():
    windows = media.plan_windows(60.0, cuts=[])
    assert all(end - start <= media.MAX_WINDOW_S + 1e-9 for start, end in windows)
    assert windows[-1][1] == 60.0


@needs_ffmpeg
def test_real_audio_is_cut_into_windows_under_30_seconds(tmp_path):
    src = tmp_path / "long.wav"
    # 45 s tone, then 0.6 s silence, then 40 s tone: must produce windows of at most 28 s with the cut in the silence.
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=45", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
         "-f", "lavfi", "-i", "sine=frequency=330:duration=40", "-filter_complex",
         "[0]aformat=sample_fmts=s16:channel_layouts=mono[a];[1]atrim=0:0.6[s];[2]aformat=sample_fmts=s16:channel_layouts=mono[b];[a][s][b]concat=n=3:v=0:a=1",
         "-ar", "16000", str(src)],
        check=True,
    )
    wav16 = tmp_path / "16k.wav"
    media.to_wav16k(src, wav16)
    duration = media.wav_duration(wav16)
    assert 84 < duration < 86
    windows = media.plan_windows(duration, media.silence_midpoints(wav16))
    assert all(end - start <= 28.0 + 0.01 for start, end in windows)
    pieces = []
    for i, (s, e) in enumerate(windows):
        piece = tmp_path / f"w{i}.wav"
        media.cut_wav(wav16, s, e, piece)
        pieces.append(piece)
    assert abs(sum(media.wav_duration(p) for p in pieces) - duration) < 0.1


@needs_ffmpeg
def test_inspect_detects_audio_and_missing_file(tmp_path):
    src = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=2", str(src)], check=True)
    info = media.inspect(str(src))
    assert info["kind"] == "audio" and abs(info["duration_s"] - 2.0) < 0.1
    with pytest.raises(media.MediaError) as err:
        media.inspect(str(tmp_path / "nope.wav"))
    assert err.value.code == "file_not_found"


def test_inspect_pdf_and_image_without_ffmpeg(tmp_path):
    pdf = tmp_path / "doc.pdf"
    Image.new("RGB", (200, 300), "white").save(pdf, "PDF", resolution=72.0)
    assert media.inspect(str(pdf))["kind"] == "pdf"
    png = tmp_path / "page.png"
    Image.new("RGB", (50, 50), "white").save(png)
    assert media.inspect(str(png))["kind"] == "image"


def test_render_pdf_pages_gives_one_png_per_page(tmp_path):
    pdf = tmp_path / "two.pdf"
    Image.new("RGB", (100, 100), "white").save(pdf, "PDF", save_all=True, append_images=[Image.new("RGB", (100, 100), "white")])
    pages = media.render_pdf_pages(pdf, tmp_path / "pages")
    assert [p.name for p in pages] == ["page-001.png", "page-002.png"]


def test_image_for_ocr_converts_unsupported_formats(tmp_path):
    bmp = tmp_path / "scan.bmp"
    Image.new("RGB", (20, 20), "white").save(bmp)
    data, mime = media.image_for_ocr(bmp, tmp_path)
    assert mime == "image/png" and data[:4] == b"\x89PNG"


def test_concat_wavs_joins_with_pauses(tmp_path):
    from conftest import wav_bytes

    a, b = tmp_path / "a.wav", tmp_path / "b.wav"
    a.write_bytes(wav_bytes(1.0))
    b.write_bytes(wav_bytes(2.0))
    total = media.concat_wavs([a, b], tmp_path / "out.wav", gap_s=0.25)
    assert abs(total - 3.25) < 0.01
