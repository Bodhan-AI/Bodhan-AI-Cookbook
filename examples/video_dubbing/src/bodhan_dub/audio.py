"""Audio helpers: probe, extract, find speech (Silero VAD), separate vocals from music (Demucs), cut, stretch."""

import hashlib
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import torch
from silero_vad import get_speech_timestamps, load_silero_vad

SAMPLE_RATE = 16000


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


def duration(path: Path) -> float:
    out = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)])
    return float(out.stdout.strip())


def extract_audio(video: Path, wav: Path) -> None:
    """Mono 16 kHz WAV: what Indic-Transcribe expects."""
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), str(wav)])


def speech_regions(wav: Path, max_len: float = 28.0) -> list[tuple[float, float]]:
    """Return (start, end) in seconds of every stretch of speech, using Silero VAD.

    Silero recognises voice rather than loudness, so music and noise are not
    mistaken for speech. Regions are capped at max_len because Indic-Transcribe
    rejects audio longer than 30 s; Silero splits long ones at its quietest point.
    """
    with wave.open(str(wav)) as w:
        pcm = torch.frombuffer(bytearray(w.readframes(w.getnframes())), dtype=torch.int16).float() / 32768
    stamps = get_speech_timestamps(
        pcm,
        load_silero_vad(),
        sampling_rate=SAMPLE_RATE,
        min_silence_duration_ms=300,
        max_speech_duration_s=max_len,
        return_seconds=True,
    )
    return [(s["start"], s["end"]) for s in stamps]


def cut(wav: Path, start: float, end: float, out: Path) -> None:
    _run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(wav), str(out)])


def wav_duration(path: Path) -> float:
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def median_pitch(wav: Path, regions: list[tuple[float, float]]) -> float:
    """Median voice pitch in Hz over the speech regions, by autocorrelation. Used to pick a voice gender."""
    with wave.open(str(wav)) as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float64)
    lo, hi, n = sr // 400, sr // 70, 1024  # search 70-400 Hz
    pitches = []
    for start, end in regions:
        for t in np.arange(start, end - 0.07, 0.05):
            frame = x[int(t * sr) : int(t * sr) + n]
            if np.sqrt((frame**2).mean()) < 1000:  # skip quiet frames
                continue
            ac = np.correlate(frame, frame, "full")[n - 1 :]
            pitches.append(sr / (lo + ac[lo:hi].argmax()))
    return float(np.median(pitches)) if pitches else 0.0


def speed_up(src: Path, dst: Path, factor: float) -> None:
    """Time-stretch without changing pitch (ffmpeg atempo)."""
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-filter:a", f"atempo={factor:.4f}", str(dst)])


def join(clips: list[Path], dst: Path, gap: float = 0.15) -> None:
    """Concatenate WAV clips (same format) with a short silence between them."""
    with wave.open(str(clips[0])) as w:
        params = w.getparams()
    silence = b"\0" * (int(gap * params.framerate) * params.sampwidth * params.nchannels)
    with wave.open(str(dst), "wb") as out:
        out.setparams(params)
        for i, clip in enumerate(clips):
            with wave.open(str(clip)) as w:
                out.writeframes((silence if i else b"") + w.readframes(w.getnframes()))


def has_background(wav: Path, regions: list[tuple[float, float]], threshold_db: float = -50) -> bool:
    """True if there is real sound (music, effects) away from speech.

    Looks at 50 ms frames at least 0.3 s from any speech region and takes their median level,
    so word tails and breaths near speech don't count. If speech is buried under music, VAD
    finds little speech and the "outside" is music: that counts as background too.
    """
    with wave.open(str(wav)) as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float64) / 32768
    outside = np.ones(len(x), bool)
    for start, end in regions:
        outside[max(0, int((start - 0.3) * sr)) : int((end + 0.3) * sr)] = False
    hop = sr // 20
    n = len(x) // hop * hop
    frames = x[:n].reshape(-1, hop)[outside[:n].reshape(-1, hop).all(axis=1)]
    if len(frames) == 0:
        return False
    levels = 20 * np.log10(np.sqrt((frames**2).mean(axis=1)) + 1e-9)
    return float(np.median(levels)) > threshold_db


def separate(video: Path, work: Path) -> tuple[Path, Path]:
    """Split the soundtrack into vocals and everything else (music, effects) with Demucs (htdemucs).

    Runs at roughly real time on CPU. Results are kept per input, so reruns skip it.
    """
    mix = work / "original_44k.wav"
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vn", "-ac", "2", "-ar", "44100", str(mix)])
    key = hashlib.sha256(mix.read_bytes()).hexdigest()[:16]
    stems = work / "separated" / "htdemucs" / key
    vocals, music = stems / "vocals.wav", stems / "no_vocals.wav"
    if not (vocals.exists() and music.exists()):
        named = mix.with_name(f"{key}.wav")
        mix.replace(named)
        _run([sys.executable, "-m", "demucs", "--two-stems", "vocals", "-n", "htdemucs", "-o", str(work / "separated"), str(named)])
    return vocals, music
