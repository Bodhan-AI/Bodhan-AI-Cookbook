"""Mixing and loudness normalisation.

Two tracks come together here: the dub (speech only) and the bed (everything
that was not speech). The bed sits 6 dB under the speech so page turns and music
do not compete with the narration.

Loudness is normalised to the brief's target of **-16 LUFS ±1**, measured with
ffmpeg's EBU R128 meter. It is a two-pass operation: measure, then apply. One-pass
`loudnorm` is fast but lands further from the target, and the brief asks for a
measured number rather than an approximate one.

The speech track is normalised *before* mixing, because the target describes
speech loudness. The mix is then measured as well and both figures are reported.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np

from . import media
from .errors import MediaError

TARGET_LUFS = -16.0
TRUE_PEAK = -1.5
LOUDNESS_RANGE = 11.0
BED_GAIN_DB = -6.0
LUFS_TOLERANCE = 1.0


def measure_loudness(path: Path | str) -> dict[str, Any]:
    """EBU R128 measurement via loudnorm's first pass."""
    result = _run_ffmpeg(
        [
            media.find_binary("ffmpeg"),
            "-nostdin",
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            f"loudnorm=I={TARGET_LUFS}:TP={TRUE_PEAK}:LRA={LOUDNESS_RANGE}:print_format=json",
            "-f",
            "null",
            "-",
        ]
    )
    payload = _extract_json(result.stderr.decode("utf-8", "replace"))
    if not payload:
        return {}
    return {
        "lufs": _to_float(payload.get("input_i")),
        "true_peak_db": _to_float(payload.get("input_tp")),
        "lra": _to_float(payload.get("input_lra")),
        "threshold": _to_float(payload.get("input_thresh")),
        "offset": _to_float(payload.get("target_offset")),
        "raw": payload,
    }


def normalize_loudness(
    src: Path | str,
    dst: Path | str,
    *,
    target_lufs: float = TARGET_LUFS,
    true_peak: float = TRUE_PEAK,
    sample_rate: int = media.TTS_SAMPLE_RATE,
) -> dict[str, Any]:
    """Two-pass normalisation. Falls back to a copy if the input is silent."""
    measured = measure_loudness(src)
    lufs = measured.get("lufs")
    if lufs is None or not math.isfinite(lufs) or lufs <= -70:
        shutil.copyfile(src, dst)
        return {"applied": False, "reason": "input is silent or unmeasurable", "before_lufs": lufs}

    chain = (
        f"loudnorm=I={target_lufs}:TP={true_peak}:LRA={LOUDNESS_RANGE}"
        f":measured_I={measured['raw']['input_i']}"
        f":measured_LRA={measured['raw']['input_lra']}"
        f":measured_TP={measured['raw']['input_tp']}"
        f":measured_thresh={measured['raw']['input_thresh']}"
        f":offset={measured['raw']['target_offset']}"
        ":linear=true:print_format=json"
    )
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        [
            media.find_binary("ffmpeg"),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(src),
            "-af",
            chain,
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-c:a",
            "pcm_s16le",
            str(dst),
        ]
    )

    after = measure_loudness(dst)
    after_lufs = after.get("lufs")
    return {
        "applied": True,
        "before_lufs": round(lufs, 2),
        "after_lufs": round(after_lufs, 2) if after_lufs is not None else None,
        "target_lufs": target_lufs,
        "within_target": bool(
            after_lufs is not None and abs(after_lufs - target_lufs) <= LUFS_TOLERANCE
        ),
    }


def mix(
    dub_path: Path | str,
    bed_path: Path | str,
    out_path: Path | str,
    *,
    bed_gain_db: float = BED_GAIN_DB,
) -> dict[str, Any]:
    """Add the bed under the dub, then protect against clipping."""
    dub, dub_rate = media.read_wav(dub_path)
    bed, bed_rate = media.read_wav(bed_path)
    if dub_rate != bed_rate:
        raise MediaError(
            f"cannot mix tracks with different sample rates ({dub_rate} and {bed_rate})"
        )

    length = max(len(dub), len(bed))
    speech = np.zeros(length, dtype=np.float32)
    music = np.zeros(length, dtype=np.float32)
    speech[: len(dub)] = dub
    music[: len(bed)] = bed

    bed_gain = 10.0 ** (bed_gain_db / 20.0)
    mixed = speech + music * bed_gain

    peak = float(np.max(np.abs(mixed))) if length else 0.0
    limited = peak > 0.99
    if limited:
        mixed *= 0.99 / peak

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    media.write_wav(out_path, mixed, dub_rate)
    return {
        "sample_rate": dub_rate,
        "duration_s": round(length / dub_rate, 3),
        "bed_gain_db": bed_gain_db,
        "peak": round(peak, 4),
        "limited": limited,
    }


def _run_ffmpeg(argv: list[str]) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(argv, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        tail = (exc.stderr or b"").decode("utf-8", "replace").strip()[-400:]
        raise MediaError(f"ffmpeg failed: {tail}") from exc


def _extract_json(stderr: str) -> dict[str, Any]:
    for match in reversed(re.findall(r"\{[^{}]*\}", stderr, re.S)):
        try:
            payload = json.loads(match)
        except ValueError:
            continue
        if "input_i" in payload:
            return payload
    return {}


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
