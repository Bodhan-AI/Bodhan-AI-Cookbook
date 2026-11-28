"""WAV helpers: split long clips for transcription, join short clips after synthesis."""

from __future__ import annotations

import contextlib
import io
import wave


def is_wav(blob: bytes) -> bool:
    return blob[:4] == b"RIFF" and blob[8:12] == b"WAVE"


def wav_seconds(blob: bytes) -> float:
    with contextlib.closing(wave.open(io.BytesIO(blob), "rb")) as wf:
        return wf.getnframes() / float(wf.getframerate())


def split_wav(blob: bytes, seconds: float) -> list[bytes]:
    chunks: list[bytes] = []
    with contextlib.closing(wave.open(io.BytesIO(blob), "rb")) as wf:
        params = wf.getparams()
        frames_per_chunk = max(1, int(params.framerate * seconds))
        while True:
            frames = wf.readframes(frames_per_chunk)
            if not frames:
                break
            buf = io.BytesIO()
            with contextlib.closing(wave.open(buf, "wb")) as out:
                out.setparams(params)
                out.writeframes(frames)
            chunks.append(buf.getvalue())
    return chunks


def join_wavs(blobs: list[bytes]) -> bytes:
    if len(blobs) == 1:
        return blobs[0]
    params = None
    frames: list[bytes] = []
    for blob in blobs:
        with contextlib.closing(wave.open(io.BytesIO(blob), "rb")) as wf:
            current = wf.getparams()
            if params is None:
                params = current
            elif (current.nchannels, current.sampwidth, current.framerate) != (params.nchannels, params.sampwidth, params.framerate):
                raise ValueError("cannot join WAV clips with different formats")
            frames.append(wf.readframes(wf.getnframes()))
    buf = io.BytesIO()
    with contextlib.closing(wave.open(buf, "wb")) as out:
        out.setparams(params)
        for chunk in frames:
            out.writeframes(chunk)
    return buf.getvalue()
