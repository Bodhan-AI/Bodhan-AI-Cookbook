"""Stage 9: build the dubbed audio track, mux it into the video, write subtitles."""

import logging
import re
import subprocess
import wave
from pathlib import Path

import numpy as np

from .bodhan import LANGUAGE_NAMES

log = logging.getLogger(__name__)

RATE = 24000  # Indic-Speak's output rate; the whole mix is built at this rate
MUSIC_UNDER_SPEECH = 10 ** (-6 / 20)  # separated music is lowered 6 dB under the dub, for clarity
MARGIN = 0.15  # seconds around each speech region where the original is also muted, so word edges don't leak
FADE = 0.05  # seconds of fade out of and back into the original audio
TARGET_LUFS = -16


def assemble(rows: list[dict], lang: str, video: Path, total: float, regions: list[tuple[float, float]], folder: Path,
             background: Path | None = None) -> dict:
    """Place each unit's clip at its start time on a silent track as long as the video, add the
    background, normalise speech loudness, and mux.

    Background: the separated music stem if there is one (kept everywhere, a little lower under
    the dub); otherwise the original audio, muted wherever the original speaker talks."""
    work = folder / ".clips"
    n = int(round(total * RATE))

    # 1. Dub track: every clip at its unit's start time.
    dub = np.zeros(n, np.float32)
    rows = [r for r in rows if "clip" in r]  # skipped units have no audio
    for row in rows:
        clip = _read(Path(row["clip"]))
        at = int(round(row["dub_start"] * RATE))
        end = min(n, at + len(clip))
        dub[at:end] += clip[: end - at]
    dub_wav = work / "dub.wav"
    _write(dub_wav, dub)

    # 2. Speech loudness to -16 LUFS: measure, apply that exact gain, and limit peaks to -1 dBFS.
    gain_db = TARGET_LUFS - _loudness(dub_wav)
    norm_wav = work / "dub_norm.wav"
    _ffmpeg("-i", dub_wav, "-af", f"volume={gain_db:.2f}dB,alimiter=limit=0.891:level=0", "-ar", str(RATE), norm_wav)
    dub = _read(norm_wav)[:n]
    dub = np.pad(dub, (0, n - len(dub)))

    # 3. Background.
    bed_wav = work / "background.wav"
    _ffmpeg("-i", background or video, "-vn", "-ac", "1", "-ar", str(RATE), bed_wav)
    bed = _read(bed_wav)[:n]
    bed = np.pad(bed, (0, n - len(bed)))
    if background:
        dubbed = [(r["dub_start"], r["dub_end"]) for r in rows]
        gain = 1 - (1 - MUSIC_UNDER_SPEECH) * (1 - _mute_envelope(dubbed, n))
    else:
        gain = _mute_envelope(regions, n)
    mix = np.clip(bed * gain + dub, -1, 1)
    mix_wav = work / "mix.wav"
    _write(mix_wav, mix)

    # 4. Subtitles, then mux: video copied untouched, new audio, both subtitle tracks, AI-dubbed label.
    srt = folder / f"subtitles.{lang}.srt"
    srt.write_text(_srt(rows, "translation", start_key="dub_start", end_key="dub_end"), encoding="utf-8")
    out = folder / f"video.{lang}.mp4"
    name = LANGUAGE_NAMES[lang]
    # Embed the subtitles as a selectable track when this ffmpeg can (some builds lack mov_text).
    subs = ["-i", srt, "-map", "2:s:0", "-c:s", "mov_text", "-metadata:s:s:0", f"language={lang}"] if _has_encoder("mov_text") else []
    _ffmpeg(
        "-i", video, "-i", mix_wav, *subs[:2],
        "-map", "0:v:0", "-map", "1:a:0", *subs[2:],
        "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
        "-metadata", f"comment=AI-dubbed into {name} with Bodhan Indic models",
        "-metadata:s:a:0", f"title={name} (AI-dubbed)", "-metadata:s:a:0", f"language={lang}",
        "-t", f"{total:.3f}", out,
    )

    duration = float(_probe(out, "format=duration"))
    loudness = _loudness(norm_wav)
    log.info("[%s] wrote %s: %.3fs (input %.3fs), speech %.1f LUFS", lang, out.name, duration, total, loudness)
    return {"video": str(out), "duration": round(duration, 3), "duration_error": round(duration - total, 3),
            "speech_lufs": round(loudness, 1)}


def keep_original(lang: str, video: Path, total: float, folder: Path) -> dict:
    """No speech to dub: the video as it is (original sound), an empty subtitle file, and a label saying so."""
    (folder / f"subtitles.{lang}.srt").write_text("", encoding="utf-8")
    out = folder / f"video.{lang}.mp4"
    _ffmpeg("-i", video, "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
            "-metadata", "comment=No speech found: not dubbed", "-t", f"{total:.3f}", out)
    duration = float(_probe(out, "format=duration"))
    log.info("[%s] no speech to dub: kept the video as it is", lang)
    return {"video": str(out), "duration": round(duration, 3), "duration_error": round(duration - total, 3),
            "speech_lufs": None}


def english_srt(units: list[dict], out: Path) -> None:
    out.write_text(_srt(units, "text", start_key="start", end_key="end"), encoding="utf-8")


def _mute_envelope(regions: list[tuple[float, float]], n: int) -> np.ndarray:
    """Gain that is 0 inside the regions (plus a margin), 1 elsewhere, with short fades.
    Used to mute the original speaker, or (scaled) to lower music under the dub."""
    gain = np.ones(n, np.float32)
    margin, fade = int(MARGIN * RATE), int(FADE * RATE)
    ramp = np.linspace(1, 0, fade, dtype=np.float32)
    for start, end in regions:
        a, b = max(0, int(start * RATE) - margin), min(n, int(end * RATE) + margin)
        gain[a:b] = 0
        fa, fb = max(0, a - fade), min(n, b + fade)
        gain[fa:a] = np.minimum(gain[fa:a], ramp[fade - (a - fa):])
        gain[b:fb] = np.minimum(gain[b:fb], ramp[::-1][: fb - b])
    return gain


def _srt(rows: list[dict], text_key: str, start_key: str, end_key: str) -> str:
    """One cue per sentence. A unit's time is shared among its sentences by length."""
    cues = []
    for row in rows:
        sentences = [s for s in re.split(r"(?<=[.?!।॥])\s+", row[text_key].strip()) if s] or [row[text_key]]
        start, end = row[start_key], row[end_key]
        total_chars = sum(len(s) for s in sentences)
        t = start
        for s in sentences:
            t_end = t + (end - start) * len(s) / total_chars
            cues.append((t, t_end, s))
            t = t_end
    return "\n".join(f"{i}\n{_ts(a)} --> {_ts(b)}\n{text}\n" for i, (a, b, text) in enumerate(cues, 1))


def _ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def _read(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        if w.getframerate() != RATE or w.getnchannels() != 1:
            raise ValueError(f"{path} is not {RATE} Hz mono")
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768


def _write(path: Path, samples: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())


def _ffmpeg(*args) -> None:
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", *map(str, args)], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"ffmpeg failed: {r.stderr.strip()[-500:]}")


def _has_encoder(name: str) -> bool:
    listing = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    return f" {name} " in listing


def _probe(path: Path, entry: str) -> str:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", entry, "-of", "csv=p=0", str(path)],
                         check=True, capture_output=True, text=True)
    return out.stdout.strip()


def _loudness(path: Path) -> float:
    log_text = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", "ebur128", "-f", "null", "-"],
                              capture_output=True, text=True).stderr
    found = re.findall(r"I:\s+(-?[\d.]+) LUFS", log_text)
    return float(found[-1]) if found else float("nan")

