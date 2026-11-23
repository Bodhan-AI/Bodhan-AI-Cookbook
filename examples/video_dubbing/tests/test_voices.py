from __future__ import annotations

import numpy as np
import pytest

from bodhan_dub import media
from bodhan_dub.voices import VoiceMap


def test_default_voice_is_the_language_female_voice() -> None:
    voice, warnings = VoiceMap().resolve("hi")

    assert voice == "Kavya"
    assert warnings == []


def test_voices_file_overrides_the_default(tmp_path) -> None:
    path = tmp_path / "voices.yaml"
    path.write_text("languages:\n  hi: Suhani\n  ta: Arun\n", encoding="utf-8")

    assert VoiceMap.load(path).resolve("hi")[0] == "Suhani"
    assert VoiceMap.load(path).resolve("ta")[0] == "Arun"


def test_an_unknown_voice_warns_and_falls_back(tmp_path) -> None:
    path = tmp_path / "voices.yaml"
    path.write_text("languages:\n  hi: NotAVoice\n", encoding="utf-8")

    voice, warnings = VoiceMap.load(path).resolve("hi")

    assert voice == "Kavya"
    assert warnings and "NotAVoice" in warnings[0]


def test_a_preferred_voice_wins(tmp_path) -> None:
    path = tmp_path / "voices.yaml"
    path.write_text("languages:\n  hi: Suhani\n", encoding="utf-8")

    voice, _ = VoiceMap.load(path).resolve("hi", preferred="Amit")

    assert voice == "Amit"


def test_missing_voices_file_warns(tmp_path) -> None:
    mapping = VoiceMap.load(tmp_path / "absent.yaml")

    assert mapping.warnings
    assert mapping.resolve("ta")[0] == "Anitha"


def test_trim_edges_removes_padding_silence() -> None:
    sr = 16_000
    samples = np.zeros(sr, dtype=np.float32)
    samples[sr // 4 : sr // 2] = 0.3

    trimmed = media.trim_edges(samples, sr, pad_s=0.0)

    assert len(trimmed) < len(samples)
    assert abs(trimmed[0]) > 0.1


def test_trim_edges_leaves_a_fully_silent_clip_alone() -> None:
    samples = np.zeros(1000, dtype=np.float32)
    assert len(media.trim_edges(samples, 16_000)) == 1000


def test_wav_bytes_roundtrip_and_duration() -> None:
    sr = 16_000
    samples = np.zeros(sr, dtype=np.float32)
    samples[:] = 0.1

    payload = media.to_wav_bytes(samples, sr)

    assert media.wav_duration_bytes(payload) == pytest.approx(1.0, abs=0.01)
    decoded, rate = media.read_wav_bytes(payload)
    assert rate == sr
    assert len(decoded) == sr


def test_concat_appends_clips() -> None:
    sr = 16_000
    a = media.to_wav_bytes(np.full(sr, 0.1, dtype=np.float32), sr)
    b = media.to_wav_bytes(np.full(sr // 2, 0.1, dtype=np.float32), sr)

    joined = media.concat_wav_bytes([a, b])

    assert media.wav_duration_bytes(joined) == pytest.approx(1.5, abs=0.02)


def _ffmpeg_available() -> bool:
    try:
        media.ensure_tools()
    except Exception:
        return False
    return True


@pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg not installed")
def test_time_stretch_shortens_without_changing_pitch() -> None:
    sr = 16_000
    t = np.arange(sr, dtype=np.float32) / sr
    tone = (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    payload = media.to_wav_bytes(tone, sr)

    stretched = media.time_stretch_wav(payload, 1.2)

    assert media.wav_duration_bytes(stretched) == pytest.approx(1.0 / 1.2, abs=0.05)


@pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg not installed")
def test_time_stretch_is_a_no_op_at_unity() -> None:
    payload = media.to_wav_bytes(np.zeros(1000, dtype=np.float32), 16_000)
    assert media.time_stretch_wav(payload, 1.0) == payload


def test_time_stretch_rejects_an_impossible_ratio() -> None:
    with pytest.raises(media.MediaError):
        media.time_stretch_wav(b"", 3.0)
