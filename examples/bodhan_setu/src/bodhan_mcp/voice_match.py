"""Small, dependency-light DSP features for approximate voice matching."""

from __future__ import annotations

import io
import json
import math
import statistics
import struct
import wave
from pathlib import Path
from typing import Any

FEATURE_NAMES = (
    "median_pitch_hz",
    "pitch_iqr_hz",
    "voiced_fraction",
    "mean_rms_dbfs",
    "zero_crossing_rate",
    "spectral_tilt_proxy",
)


def audio_features(path: Path) -> dict[str, float]:
    try:
        with wave.open(str(path), "rb") as source:
            channels = source.getnchannels()
            width = source.getsampwidth()
            sample_rate = source.getframerate()
            if source.getcomptype() != "NONE" or width != 2:
                raise ValueError("Voice matching requires uncompressed PCM16 WAV audio")
            raw = source.readframes(source.getnframes())
    except (wave.Error, EOFError) as exc:
        raise ValueError(f"Could not read WAV for voice matching: {exc}") from exc
    return _features_from_pcm(raw, channels, sample_rate)


def audio_features_bytes(audio_bytes: bytes) -> dict[str, float]:
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as source:
            channels = source.getnchannels()
            width = source.getsampwidth()
            sample_rate = source.getframerate()
            if source.getcomptype() != "NONE" or width != 2:
                raise ValueError("Expected uncompressed PCM16 WAV samples")
            raw = source.readframes(source.getnframes())
    except (wave.Error, EOFError) as exc:
        raise ValueError(f"Invalid WAV sample: {exc}") from exc
    return _features_from_pcm(raw, channels, sample_rate)


def _features_from_pcm(raw: bytes, channels: int, sample_rate: int) -> dict[str, float]:
    packed_count = len(raw) // 2
    packed = struct.unpack("<" + "h" * packed_count, raw[:packed_count * 2])
    mono = [sum(packed[i:i + channels]) / channels for i in range(0, len(packed), channels)]
    frame_size = max(1, round(sample_rate * 0.03))
    decimation = max(1, round(sample_rate / 4000))
    reduced_rate = sample_rate / decimation
    pitch_values: list[float] = []
    rms_values: list[float] = []
    crossings: list[float] = []
    tilt_values: list[float] = []
    analyzed_frames = 0
    voiced_frames = 0

    for start in range(0, len(mono), frame_size):
        frame = mono[start:start + frame_size]
        if len(frame) < frame_size // 2:
            continue
        rms = math.sqrt(sum(value * value for value in frame) / len(frame))
        if rms < 20:
            continue
        rms_values.append(20 * math.log10(max(rms / 32768.0, 1e-8)))
        crossings.append(sum((a < 0) != (b < 0) for a, b in zip(frame, frame[1:])) / max(1, len(frame) - 1))
        differences = [b - a for a, b in zip(frame, frame[1:])]
        tilt_values.append(math.sqrt(sum(x * x for x in differences) / max(1, len(differences))) / max(rms, 1.0))
        analyzed_frames += 1

        if analyzed_frames % 2 == 0:
            reduced = frame[::decimation]
            mean = sum(reduced) / len(reduced)
            centered = [value - mean for value in reduced]
            energy = sum(value * value for value in centered)
            if energy > 0:
                min_lag = max(2, int(reduced_rate / 350))
                max_lag = min(len(centered) // 2, int(reduced_rate / 70))
                best_lag, best_correlation = 0, 0.0
                for lag in range(min_lag, max_lag + 1):
                    correlation = sum(centered[i] * centered[i + lag] for i in range(len(centered) - lag)) / energy
                    if correlation > best_correlation:
                        best_lag, best_correlation = lag, correlation
                if best_lag and best_correlation >= 0.28:
                    pitch_values.append(reduced_rate / best_lag)
                    voiced_frames += 1
    if not rms_values:
        raise ValueError("Audio contains no audible PCM16 frames")
    sorted_pitch = sorted(pitch_values)
    pitch_median = statistics.median(sorted_pitch) if sorted_pitch else 0.0
    q1 = sorted_pitch[len(sorted_pitch) // 4] if sorted_pitch else 0.0
    q3 = sorted_pitch[(3 * len(sorted_pitch)) // 4] if sorted_pitch else 0.0
    return {
        "median_pitch_hz": round(pitch_median, 3),
        "pitch_iqr_hz": round(q3 - q1, 3),
        "voiced_fraction": round(voiced_frames / max(1, math.ceil(analyzed_frames / 2)), 5),
        "mean_rms_dbfs": round(statistics.mean(rms_values), 3),
        "zero_crossing_rate": round(statistics.mean(crossings), 6),
        "spectral_tilt_proxy": round(statistics.mean(tilt_values), 6),
    }


def closest_voice(query: dict[str, float], voices: list[dict[str, Any]]) -> dict[str, Any]:
    if not voices:
        raise ValueError("Voice embedding library contains no voices")
    means: dict[str, float] = {}
    scales: dict[str, float] = {}
    for feature in FEATURE_NAMES:
        values = [float(row["features"][feature]) for row in voices]
        means[feature] = statistics.mean(values)
        scales[feature] = max(statistics.pstdev(values), 1e-6)
    ranked = []
    for row in voices:
        distance = math.sqrt(sum(((query[key] - row["features"][key]) / scales[key]) ** 2 for key in FEATURE_NAMES) / len(FEATURE_NAMES))
        ranked.append((distance, row))
    distance, match = min(ranked, key=lambda item: (item[0], item[1]["voice"]))
    return {**match, "distance": round(distance, 6), "similarity_score": round(1 / (1 + distance), 6), "method": "standardized_dsp_euclidean"}


def load_voice_library(path: Path) -> dict[str, Any]:
    try:
        library = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Voice reference library is missing: {path}. Build it once with scripts/build_voice_embeddings.py") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Voice reference library is invalid JSON: {exc}") from exc
    if library.get("schema_version") != 1 or not library.get("voices"):
        raise ValueError("Voice reference library has an unsupported schema or no voice embeddings")
    if not library.get("complete", True):
        raise ValueError(f"Voice reference library is incomplete ({len(library['voices'])} embeddings recorded); rerun scripts/build_voice_embeddings.py to resume")
    return library
