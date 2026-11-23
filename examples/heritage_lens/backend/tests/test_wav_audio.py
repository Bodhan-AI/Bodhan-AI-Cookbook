import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from app.services.audio import processing as ap
from app.services.audio.wav import WavFormatError, build_wav, concat_wavs, parse_wav, wav_duration_seconds, WavFormat
from tests.audio_helpers import make_wav_bytes, write_tone_wav

FFMPEG = shutil.which("ffmpeg")


def test_concat_preserves_order_and_length():
    a, b, c = make_wav_bytes(1.0, freq=200), make_wav_bytes(2.0, freq=400), make_wav_bytes(0.5, freq=800)
    joined = concat_wavs([a, b, c])
    fmt, frames = parse_wav(joined)
    assert fmt == WavFormat(1, 2, 24000)
    assert abs(wav_duration_seconds(joined) - 3.5) < 1e-6
    # order: frames of `a` come first
    assert frames[: len(parse_wav(a)[1])] == parse_wav(a)[1]
    assert frames[-len(parse_wav(c)[1]):] == parse_wav(c)[1]


def test_concat_with_gap_adds_silence():
    joined = concat_wavs([make_wav_bytes(1.0), make_wav_bytes(1.0)], gap_ms=500)
    assert abs(wav_duration_seconds(joined) - 2.5) < 1e-6


def test_concat_rejects_incompatible_formats():
    with pytest.raises(WavFormatError, match="incompatible"):
        concat_wavs([make_wav_bytes(1.0, rate=24000), make_wav_bytes(1.0, rate=16000)])


def test_parse_rejects_non_wav():
    with pytest.raises(WavFormatError):
        parse_wav(b'{"error": "this is JSON, not audio"}')
    with pytest.raises(WavFormatError):
        concat_wavs([])


def test_sniffing_known_formats():
    assert ap.sniff_audio_format(b"RIFF\x00\x00\x00\x00WAVEfmt ") == "wav"
    assert ap.sniff_audio_format(b"fLaC\x00\x00") == "flac"
    assert ap.sniff_audio_format(b"OggS\x00\x02") == "ogg"
    assert ap.sniff_audio_format(b"\x1a\x45\xdf\xa3\x01") == "webm"
    assert ap.sniff_audio_format(b"\x00\x00\x00\x18ftypM4A ") == "m4a"
    assert ap.sniff_audio_format(b"ID3\x04\x00") == "mp3"
    assert ap.sniff_audio_format(b"\xff\xfb\x90\x00") == "mp3"
    assert ap.sniff_audio_format(b"\xff\xf1\x50\x80") is None  # ADTS AAC is not claimed as MP3
    assert ap.sniff_audio_format(b"MZ\x90\x00") is None


def _check_segments(segments, total_seconds, limit):
    assert [s.index for s in segments] == list(range(len(segments)))
    assert segments[0].start_seconds == 0
    for prev, nxt in zip(segments, segments[1:]):
        assert abs(prev.end_seconds - nxt.start_seconds) < 1e-9, "segments must be contiguous"
    assert abs(segments[-1].end_seconds - total_seconds) < 0.01
    assert all(s.duration_seconds <= limit + 1e-9 for s in segments)
    # every sample is accounted for
    assert abs(sum(wav_duration_seconds(s.wav_bytes) for s in segments) - total_seconds) < 0.01


def test_split_long_audio_respects_limit_and_order():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "long.wav"
        write_tone_wav(p, 75.0)
        segs = ap.split_wav(p, 29.0)
        assert len(segs) == 3
        _check_segments(segs, 75.0, 29.0)


def test_30_second_boundaries():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "b.wav"
        write_tone_wav(p, 29.0)
        assert len(ap.split_wav(p, 29.0)) == 1            # exactly at the limit: one segment
        write_tone_wav(p, 29.5)
        segs = ap.split_wav(p, 29.0)
        assert len(segs) == 2                              # just over: two segments
        _check_segments(segs, 29.5, 29.0)
        write_tone_wav(p, 30.0)
        assert len(ap.split_wav(p, 30.0)) == 1             # documented hard limit is allowed
        write_tone_wav(p, 30.2)
        segs = ap.split_wav(p, 30.0)
        assert len(segs) == 2 and all(s.duration_seconds <= 30.0 for s in segs)


def test_cut_prefers_a_silent_moment():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "gap.wav"
        write_tone_wav(p, 50.0, gaps=[(27.2, 27.8)])
        segs = ap.split_wav(p, 29.0)
        assert 27.0 <= segs[0].end_seconds <= 27.9, segs[0].end_seconds


def test_empty_audio_rejected():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "empty.wav"
        p.write_bytes(build_wav(WavFormat(1, 2, 16000), b""))
        with pytest.raises(ap.AudioProcessingError):
            ap.split_wav(p, 29.0)


def test_limit_above_30_seconds_is_refused():
    with pytest.raises(ValueError):
        ap.prepare_segments(Path("whatever.wav"), max_segment_seconds=31)


def test_ffmpeg_path_misconfiguration_is_reported():
    with pytest.raises(ap.FFmpegNotAvailable):
        ap.find_ffmpeg("/definitely/not/here/ffmpeg")


def test_prepare_segments_normalises_stereo_44k_wav():
    pytest.importorskip("os")
    if not FFMPEG:
        return
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "stereo.wav"
        write_tone_wav(src, 61.0, rate=44100, channels=2)
        segs = ap.prepare_segments(src, ffmpeg_path=FFMPEG, max_segment_seconds=29.0)
        fmt, _ = parse_wav(segs[0].wav_bytes)
        assert (fmt.channels, fmt.sample_width, fmt.frame_rate) == (1, 2, 16000)
        _check_segments(segs, 61.0, 29.0)


def test_prepare_segments_accepts_mp3_ogg_flac_and_webm():
    if not FFMPEG:
        return
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "src.wav"
        write_tone_wav(src, 40.0)
        tried = []
        for ext, codec in (("mp3", "libmp3lame"), ("ogg", "libvorbis"), ("flac", "flac"), ("webm", "libopus")):
            out = Path(d) / f"out.{ext}"
            r = subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(src), "-c:a", codec, str(out)],
                               capture_output=True)
            if r.returncode != 0:
                continue  # encoder not compiled into this ffmpeg; skip that format
            tried.append(ext)
            segs = ap.prepare_segments(out, ffmpeg_path=FFMPEG, max_segment_seconds=29.0)
            assert len(segs) == 2
            assert abs(segs[-1].end_seconds - 40.0) < 0.15  # lossy codecs add small padding
            assert all(s.duration_seconds <= 29.0 for s in segs)
        assert tried, "no encoder available to exercise the conversion path"
        print("   formats exercised:", tried)


def test_corrupted_audio_is_reported_not_swallowed():
    if not FFMPEG:
        return
    with tempfile.TemporaryDirectory() as d:
        bad = Path(d) / "bad.mp3"
        bad.write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00garbage" * 50)
        with pytest.raises(ap.AudioProcessingError):
            ap.prepare_segments(bad, ffmpeg_path=FFMPEG)


def test_duration_limit_enforced():
    if not FFMPEG:
        return
    from app.core.errors import PayloadTooLarge
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "x.wav"
        write_tone_wav(src, 12.0)
        with pytest.raises(PayloadTooLarge):
            ap.prepare_segments(src, ffmpeg_path=FFMPEG, max_total_seconds=10)
