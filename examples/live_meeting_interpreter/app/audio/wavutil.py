from __future__ import annotations

import io
import wave

import numpy as np

SAMPLE_RATE = 16000  # internal pipeline rate (mono float32)


def float_to_pcm16(audio: np.ndarray) -> np.ndarray:
    return (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)


def encode_wav(audio: np.ndarray, rate: int = SAMPLE_RATE) -> bytes:
    """Mono float32 [-1, 1] -> 16-bit PCM WAV bytes (the format Bodhan ASR documents)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(float_to_pcm16(audio).tobytes())
    return buf.getvalue()


def decode_wav(data: bytes) -> tuple[np.ndarray, int]:
    """16-bit PCM WAV bytes -> (mono float32, sample rate)."""
    with wave.open(io.BytesIO(data), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError(f"Expected 16-bit PCM, got {8 * w.getsampwidth()}-bit")
        channels, rate = w.getnchannels(), w.getframerate()
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    audio = pcm.astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return audio, rate
