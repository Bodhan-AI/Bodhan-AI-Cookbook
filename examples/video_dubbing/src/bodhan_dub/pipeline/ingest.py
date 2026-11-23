"""Stage 0: decode the input into the two audio renditions the pipeline needs."""

from __future__ import annotations

import os

from ..ffmpeg import extract_audio, probe_duration
from ..logging_setup import get_logger

log = get_logger("ingest")

ASR_SR = 16000      # Indic-Transcribe works on 16k mono
WORK_SR = 24000     # Indic-Speak output rate; used for the final mix/bed


def ingest(video_path: str, workdir: str) -> dict:
    """Return paths + duration for the 16k ASR audio and 24k mix/bed audio."""
    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"input not found: {video_path}")
    os.makedirs(workdir, exist_ok=True)
    asr_wav = os.path.join(workdir, "audio.16k.wav")
    bed_wav = os.path.join(workdir, "audio.24k.wav")
    extract_audio(video_path, asr_wav, sr=ASR_SR, mono=True)
    extract_audio(video_path, bed_wav, sr=WORK_SR, mono=True)
    duration = probe_duration(video_path)
    log.info("ingested", extra={"event": "ingested",
             "extra": {"duration_s": round(duration, 2)}})
    return {"asr_wav": asr_wav, "bed_wav": bed_wav, "duration": duration,
            "asr_sr": ASR_SR, "work_sr": WORK_SR}
