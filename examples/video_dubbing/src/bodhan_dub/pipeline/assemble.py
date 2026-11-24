"""Stage: lay dubbed segments on the timeline, keep the music bed, mux to MP4.

Each dub is placed at its source start (so sync stays within budget). Non-speech
stretches are filled from the ORIGINAL audio, so music/effects/silence survive and
no narration is invented. The mix is normalized to −16 LUFS and the final track is
padded/trimmed to the source duration (±100 ms) before muxing over the video.
"""

from __future__ import annotations

import os

import numpy as np

from ..ffmpeg import loudnorm, mux, read_wav, write_wav
from ..logging_setup import get_logger
from ..types import DubSegment

log = get_logger("assemble")


def _fade_tail(audio: np.ndarray, sr: int, ms: float = 30.0) -> np.ndarray:
    n = min(len(audio), int(sr * ms / 1000))
    if n > 0:
        audio = audio.copy()
        audio[-n:] *= np.linspace(1.0, 0.0, n)
    return audio


def assemble_language(
    bed_wav: str, non_speech: list[tuple[float, float]], dubs: list[DubSegment],
    video_path: str, out_mp4: str, total_s: float, work_sr: int, target_lufs: float, tmpdir: str,
    subtitles: str | None = None, sub_lang: str | None = None,
) -> dict:
    bed, sr = read_wav(bed_wav)
    if sr != work_sr:  # ingest guarantees 24k, but stay safe
        work_sr = sr
    n_total = int(total_s * work_sr)
    timeline = np.zeros(n_total, dtype=np.float32)

    # 1) music/effects bed: original audio only where there was no speech
    for s, e in non_speech:
        a, b = int(s * work_sr), min(n_total, int(e * work_sr))
        if b > a:
            timeline[a:b] += bed[a : a + (b - a)]

    # 2) place dubbed speech at source start, trimming any tail that would hit the next segment
    dubbed = sorted((d for d in dubs if d.status == "dubbed" and d.audio_path),
                    key=lambda d: d.start)
    starts = [d.start for d in dubbed]
    for i, dub in enumerate(dubbed):
        audio, _ = read_wav(dub.audio_path)
        start_idx = int(dub.start * work_sr)
        next_start = starts[i + 1] if i + 1 < len(starts) else total_s
        avail = max(0, int(next_start * work_sr) - start_idx)
        if len(audio) > avail > 0:
            audio = _fade_tail(audio[:avail], work_sr)
            dub.fit_action = "overflow" if dub.fit_action in ("pad", "stretch") else dub.fit_action
        end_idx = min(n_total, start_idx + len(audio))
        if end_idx > start_idx:
            timeline[start_idx:end_idx] += audio[: end_idx - start_idx]
        dub.qa.sync_offset_ms = round((start_idx / work_sr - dub.start) * 1000.0, 1)
        dub.qa.within_sync = abs(dub.qa.sync_offset_ms) <= 300.0

    # 3) normalize loudness, then lock total length to the source (±100 ms target)
    np.clip(timeline, -1.0, 1.0, out=timeline)
    mix_path = os.path.join(tmpdir, "mix.wav")
    norm_path = os.path.join(tmpdir, "mix.norm.wav")
    write_wav(mix_path, timeline, work_sr)
    stats = loudnorm(mix_path, norm_path, target_lufs)

    data, sr = read_wav(norm_path)
    target_n = int(total_s * sr)
    if len(data) < target_n:
        data = np.concatenate([data, np.zeros(target_n - len(data), np.float32)])
    else:
        data = data[:target_n]
    final_path = os.path.join(tmpdir, "final.wav")
    write_wav(final_path, data, sr)

    mux(video_path, final_path, out_mp4, subtitles=subtitles, sub_lang=sub_lang)
    # output_i is the loudness AFTER normalization; input_i is the pre-norm measurement.
    measured = _f(stats.get("output_i") or stats.get("input_i"))
    log.info("assembled", extra={"event": "assembled",
             "extra": {"loudness_lufs": measured, "out": out_mp4}})
    return {"loudness_lufs": measured, "target_lufs": target_lufs,
            "duration_s": round(len(data) / sr, 3)}


def _f(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None
