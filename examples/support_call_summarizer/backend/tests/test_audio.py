import pytest

from app import audio
from app.config import ALLOWED_EXTENSIONS
from tests.conftest import encode, needs_ffmpeg, synth, write_wav

MB = 1_000_000


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        (b"RIFF\x00\x00\x00\x00WAVEfmt ", "wav"),
        (b"ID3\x04\x00\x00\x00\x00\x00\x00", "mp3"),
        (b"\xff\xfb\x90\x64\x00\x00\x00\x00", "mp3"),
        (b"\xff\xf1\x50\x80\x00\x00\x00\x00", "aac"),
        (b"\x00\x00\x00\x20ftypM4A \x00\x00", "mp4"),
        (b"#!AMR\n\x00\x00", "amr"),
        (b"OggS\x00\x02\x00\x00", "ogg"),
        (b"fLaC\x00\x00\x00\x22", "flac"),
        (b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81", "webm"),
        (b"hello, this is text", None),
        (b"%PDF-1.7\n", None),
    ],
)
def test_sniff_format(head, expected):
    assert audio.sniff_format(head) == expected


def validate(path, name=None, max_bytes=5 * MB):
    audio.validate_upload(path, name or path.name, allowed_extensions=ALLOWED_EXTENSIONS, max_bytes=max_bytes)


def test_rejects_unsupported_extension(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("hello")
    with pytest.raises(audio.AudioError) as e:
        validate(f)
    assert e.value.code == "unsupported_format"


def test_rejects_empty_file(tmp_path):
    f = tmp_path / "call.mp3"
    f.write_bytes(b"")
    with pytest.raises(audio.AudioError) as e:
        validate(f)
    assert e.value.code == "empty_file"


def test_rejects_too_large_file(call_wav):
    with pytest.raises(audio.AudioError) as e:
        validate(call_wav, max_bytes=1000)
    assert e.value.code == "file_too_large"
    assert e.value.status_code == 413


def test_rejects_renamed_non_audio_file(tmp_path):
    f = tmp_path / "call.mp3"
    f.write_bytes(b"%PDF-1.7 definitely not audio")
    with pytest.raises(audio.AudioError) as e:
        validate(f)
    assert e.value.code == "unsupported_format"


def test_accepts_wav(call_wav):
    validate(call_wav)


def test_duration_limits():
    audio.check_duration(60, 120)
    with pytest.raises(audio.AudioError) as e:
        audio.check_duration(121, 120)
    assert e.value.code == "call_too_long"
    with pytest.raises(audio.AudioError):
        audio.check_duration(0.0, 120)


def test_wav_bytes_slices_by_seconds():
    samples = synth([("speech", 2.0)])
    data = audio.wav_bytes(samples, 0.5, 1.5)
    assert data[:4] == b"RIFF"
    assert len(data) == 44 + 16_000 * 2  # header + one second of 16-bit mono


@needs_ffmpeg
@pytest.mark.parametrize("ext", [".mp3", ".m4a", ".ogg", ".flac", ".webm"])
def test_probe_and_convert_phone_formats(tmp_path, ext):
    src = encode(write_wav(tmp_path / "src.wav", synth([("speech", 3.0)])), tmp_path / f"call{ext}")
    validate(src)
    probed = audio.probe(src)
    assert probed.duration is None or probed.duration == pytest.approx(3.0, abs=0.2)
    out = tmp_path / "out.wav"
    audio.convert_to_wav(src, out, max_seconds=60)
    samples = audio.read_wav(out)
    assert len(samples) / audio.SAMPLE_RATE == pytest.approx(3.0, abs=0.2)


@needs_ffmpeg
def test_probe_rejects_damaged_file(tmp_path):
    f = tmp_path / "call.mp3"
    f.write_bytes(b"ID3" + b"\x00" * 200)
    with pytest.raises(audio.AudioError) as e:
        audio.probe(f)
    assert e.value.code == "invalid_audio"
