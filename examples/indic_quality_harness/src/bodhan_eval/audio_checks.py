import math
import struct
import wave
from pathlib import Path

# A 20 ms frame whose RMS is below 1 % of full scale (-40 dBFS) is silent.
FRAME_SECONDS = 0.02
FRAME_SILENCE_RMS = 0.01
# Seconds of audio per intended token outside this range is suspicious.
MIN_SECONDS_PER_TOKEN = 0.15
MAX_SECONDS_PER_TOKEN = 1.5


def _frame_silence(samples, sample_rate, channels, sample_width, max_amplitude) -> dict:
    """Frame-level silence: more robust than counting near-zero samples,
    which also counts every zero crossing inside speech."""
    if not samples or not sample_rate:
        return {
            "frame_silence_ratio": 0,
            "leading_silence_seconds": 0,
            "trailing_silence_seconds": 0,
        }

    frame_len = max(1, int(sample_rate * FRAME_SECONDS)) * max(1, channels)
    threshold = max_amplitude * FRAME_SILENCE_RMS
    silent = []

    for start in range(0, len(samples), frame_len):
        frame = samples[start:start + frame_len]
        if sample_width == 1:
            frame = [s - 128 for s in frame]
        rms = math.sqrt(sum(s * s for s in frame) / len(frame))
        silent.append(rms < threshold)

    leading = next((i for i, s in enumerate(silent) if not s), len(silent))
    trailing = next((i for i, s in enumerate(reversed(silent)) if not s), len(silent))

    return {
        "frame_silence_ratio": round(sum(silent) / len(silent), 4),
        "leading_silence_seconds": round(leading * FRAME_SECONDS, 3),
        "trailing_silence_seconds": round(trailing * FRAME_SECONDS, 3),
    }


def inspect_audio(audio_path: str) -> dict:
    path = Path(audio_path)

    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_rate = wav.getframerate()
        sample_width = wav.getsampwidth()
        frames = wav.getnframes()

        if wav.getcomptype() != "NONE":
            raise ValueError("Only uncompressed PCM WAV is supported.")

        raw = wav.readframes(frames)

    duration = frames / sample_rate if sample_rate else 0

    if sample_width not in (1, 2, 3, 4):
        raise ValueError(f"Unsupported sample width: {sample_width}")

    formats = {1: "B", 2: "h", 4: "i"}
    max_amplitude = (1 << (sample_width * 8 - 1)) - 1
    silence_threshold = max_amplitude * 0.01
    clipping_threshold = max_amplitude * 0.99

    silence_count = 0
    clipping_count = 0
    sample_count = 0

    if sample_width == 3:
        samples = []
        for i in range(0, len(raw), 3):
            value = int.from_bytes(raw[i:i + 3], "little", signed=True)
            samples.append(value)
    else:
        fmt = formats[sample_width]
        count = len(raw) // sample_width
        samples = struct.unpack("<" + fmt * count, raw)

    for sample in samples:
        # 8-bit PCM uses unsigned samples centred at 128.
        amplitude = abs(sample - 128) if sample_width == 1 else abs(sample)

        if amplitude <= silence_threshold:
            silence_count += 1

        if amplitude >= clipping_threshold:
            clipping_count += 1

        sample_count += 1

    frame_stats = _frame_silence(samples, sample_rate, channels, sample_width, max_amplitude)

    return {
        **frame_stats,
        "duration_seconds": round(duration, 3),
        "sample_rate": sample_rate,
        "channels": channels,
        "sample_width_bytes": sample_width,
        "silence_ratio": round(silence_count / sample_count, 4)
        if sample_count else 0,
        "clipping_ratio": round(clipping_count / sample_count, 4)
        if sample_count else 0,
        "total_samples": sample_count,
    }


def evaluate_audio_quality(metrics: dict) -> list[dict]:
    """Classify basic audio-quality metrics using configurable heuristics."""

    checks = []

    def add_check(category, passed, severity, evidence, status=None):
        result = {
            "category": category,
            "passed": passed,
            "severity": severity,
            "evidence": evidence,
        }
        result["status"] = status or {True: "pass", False: "fail"}.get(passed, "review")
        checks.append(result)

    duration = metrics["duration_seconds"]
    if duration < 0.3:
        add_check(
            "audio_duration",
            False,
            "high",
            {"duration_seconds": duration, "minimum_seconds": 0.3},
        )
    else:
        add_check(
            "audio_duration", True, "none",
            {"duration_seconds": duration},
        )

    # Prefer the frame-level ratio when available (threshold 0.6); fall back
    # to the original sample-level ratio (threshold 0.8).
    if "frame_silence_ratio" in metrics:
        silence_key, silence_threshold = "frame_silence_ratio", 0.60
    else:
        silence_key, silence_threshold = "silence_ratio", 0.80

    silence = metrics[silence_key]
    if silence > silence_threshold:
        add_check(
            "audio_silence",
            None,
            "review",
            {silence_key: silence, "threshold": silence_threshold},
            status="review",
        )
    else:
        add_check(
            "audio_silence", True, "none",
            {silence_key: silence},
        )

    clipping = metrics["clipping_ratio"]
    if clipping > 0.01:
        add_check(
            "audio_clipping",
            None,
            "review",
            {"clipping_ratio": clipping, "threshold": 0.01},
            status="review",
        )
    else:
        add_check(
            "audio_clipping", True, "none",
            {"clipping_ratio": clipping},
        )

    sample_rate = metrics["sample_rate"]
    if sample_rate < 16000:
        add_check(
            "audio_sample_rate",
            None,
            "review",
            {"sample_rate": sample_rate, "minimum_hz": 16000},
            status="review",
        )
    else:
        add_check(
            "audio_sample_rate", True, "none",
            {"sample_rate": sample_rate},
        )

    return checks


def evaluate_duration_vs_text(duration_seconds: float, token_count: int) -> dict | None:
    """Flag audio that is implausibly short or long for the text it speaks.

    Too short suggests truncated synthesis; too long suggests long pauses
    or garbled output. Both are ``review``: speaking rate varies by voice
    and language.
    """
    if token_count <= 0:
        return None

    per_token = duration_seconds / token_count
    evidence = {
        "duration_seconds": duration_seconds,
        "intended_tokens": token_count,
        "seconds_per_token": round(per_token, 3),
        "expected_range": [MIN_SECONDS_PER_TOKEN, MAX_SECONDS_PER_TOKEN],
    }

    if MIN_SECONDS_PER_TOKEN <= per_token <= MAX_SECONDS_PER_TOKEN:
        return {"category": "audio_duration_vs_text", "status": "pass",
                "passed": True, "severity": "none", "evidence": evidence}

    return {"category": "audio_duration_vs_text", "status": "review",
            "passed": None, "severity": "medium", "evidence": evidence}
