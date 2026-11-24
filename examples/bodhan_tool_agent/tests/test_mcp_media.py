from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
from fakes import tone, write_tone_wav

from bodhan_mcp import media

SR = media.ASR_SAMPLE_RATE


def _speech_frames(pcm: np.ndarray) -> np.ndarray:
    return np.flatnonzero(media.speech_mask(media.frame_db(pcm, SR))) * media.FRAME_S


def _covered(t: float, windows: list[media.Window]) -> bool:
    return any(w.start <= t + 1e-6 and t + media.FRAME_S <= w.end + 1e-6 for w in windows)


def test_vad_windows_bounded_cover_speech_and_skip_silence() -> None:
    # 10 s silence, then bursts of speech with short and long pauses, 70 s in total.
    parts = [np.zeros(10 * SR, dtype=np.int16)]
    for on, off in [(4, 0.5)] * 6 + [(3, 6.0)] + [(5, 0.4)] * 4:
        parts += [tone(300, on, SR), np.zeros(int(off * SR), dtype=np.int16)]
    pcm = np.concatenate(parts)
    windows = media.vad_windows(pcm, SR)

    assert windows and all(w.duration <= 28.0 + 1e-6 for w in windows)
    assert windows[0].start >= 9.5  # leading silence is never sent
    assert all(_covered(t, windows) for t in _speech_frames(pcm))
    sent = sum(w.duration for w in windows)
    speech = sum(on for on, _ in [(4, 0.5)] * 6 + [(3, 6.0)] + [(5, 0.4)] * 4)
    assert sent < speech + 8  # only short pauses ride along; the 6 s gap and lead-in are dropped
    for a, b in zip(windows, windows[1:], strict=False):
        assert a.end <= b.start + 1e-6


def test_vad_splits_continuous_speech_at_quietest_point() -> None:
    # 70 s of "speech" without a real pause; a slight dip every 9 s is the best place to cut.
    pieces = []
    for _ in range(8):
        pieces += [tone(300, 8.8, SR, amplitude=0.4), tone(300, 0.2, SR, amplitude=0.05)]
    pcm = np.concatenate(pieces)[: 70 * SR]
    windows = media.vad_windows(pcm, SR)
    assert len(windows) >= 3 and all(w.duration <= 28.0 + 1e-6 for w in windows)
    assert all(_covered(t, windows) for t in _speech_frames(pcm))
    for w in windows[:-1]:
        assert abs((w.end - 0.15) % 9.0 - 8.9) < 0.3  # cut inside the quiet dip, not mid-"word"


def test_vad_pure_silence_yields_no_windows() -> None:
    assert media.vad_windows(np.zeros(20 * SR, dtype=np.int16), SR) == []
    noise = (np.random.default_rng(0).normal(0, 10, 20 * SR)).astype(np.int16)  # about -70 dBFS hiss
    assert media.vad_windows(noise, SR) == []


def test_decode_probe_and_export_roundtrip(tmp_path: Path) -> None:
    wav = write_tone_wav(tmp_path / "in.wav", [(1.0, 0.5), (1.0, 0.5)], sr=44_100)
    info = media.probe(wav)
    assert info.has_audio and not info.has_video and abs(info.duration_s - 3.0) < 0.05
    pcm = media.decode_audio(wav)
    assert abs(len(pcm) / SR - 3.0) < 0.05

    out = media.export_audio(pcm, SR, tmp_path / "out.mp3", {"comment": "AI-generated speech (Bodhan Indic-Speak)"})
    assert out.exists() and out.stat().st_size > 1000
    meta = subprocess.run([media.ffmpeg_exe(), "-hide_banner", "-i", str(out)], capture_output=True)
    assert "AI-generated" in meta.stderr.decode("utf-8", "replace")


def test_classify_by_extension_and_magic(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "noext").write_bytes(b"%PDF-1.4 rest")
    assert media.classify(tmp_path / "a.txt")[0] == "text"
    assert media.classify(tmp_path / "noext")[0] == "pdf"
    assert media.classify(write_tone_wav(tmp_path / "b.wav", [(0.1, 0.0)]))[0] == "audio"
