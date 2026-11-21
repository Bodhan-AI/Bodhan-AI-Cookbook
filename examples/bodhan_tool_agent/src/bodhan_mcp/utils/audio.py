"""Audio manipulation utilities: probing, silence detection, chunking, and timestamp remapping."""

from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from bodhan_mcp import config
from bodhan_mcp.schemas import AudioChunk, Segment


def probe(path: str | Path) -> dict[str, Any]:
    """Probe audio file metadata using soundfile.

    Returns dict with keys: duration_s, sample_rate, channels, format.
    Raises FileNotFoundError if file is missing.
    Raises ValueError if media is unreadable.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Audio file not found: {file_path}")

    try:
        info = sf.info(str(file_path))
        return {
            "duration_s": float(info.duration),
            "sample_rate": int(info.samplerate),
            "channels": int(info.channels),
            "format": str(info.format).lower(),
            "subtype": str(info.subtype),
        }
    except Exception as exc:
        raise ValueError(f"Unreadable audio media at {file_path}: {exc}") from exc


def find_pauses(
    path: str | Path,
    min_silence_ms: int = config.SILENCE_MIN_MS,
    silence_thresh_db: float = -35.0,
) -> list[float]:
    """Detect midpoints of silent intervals (pauses) in an audio file.

    Returns a list of timestamps (seconds) where silence occurs.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Audio file not found: {file_path}")

    try:
        data, samplerate = sf.read(str(file_path), dtype="float32")
    except Exception as exc:
        raise ValueError(f"Unreadable audio media at {file_path}: {exc}") from exc

    if data.ndim > 1:
        # Convert stereo to mono for pause detection
        data = np.mean(data, axis=1)

    # Frame-based energy computation (20ms frames)
    frame_ms = 20
    frame_len = max(1, int(samplerate * (frame_ms / 1000.0)))
    min_silence_frames = max(1, int(min_silence_ms / frame_ms))

    num_frames = len(data) // frame_len
    if num_frames == 0:
        return []

    frames = data[: num_frames * frame_len].reshape((num_frames, frame_len))
    rms = np.sqrt(np.mean(frames**2, axis=1) + 1e-12)
    db = 20.0 * np.log10(rms)

    silent_mask = db < silence_thresh_db
    pauses: list[float] = []

    in_silence = False
    start_frame = 0

    for idx, is_silent in enumerate(silent_mask):
        if is_silent and not in_silence:
            in_silence = True
            start_frame = idx
        elif not is_silent and in_silence:
            in_silence = False
            duration_frames = idx - start_frame
            if duration_frames >= min_silence_frames:
                mid_frame = (start_frame + idx) / 2.0
                pauses.append(float(mid_frame * frame_ms / 1000.0))

    if in_silence and (len(silent_mask) - start_frame) >= min_silence_frames:
        mid_frame = (start_frame + len(silent_mask)) / 2.0
        pauses.append(float(mid_frame * frame_ms / 1000.0))

    return pauses


def chunk_at_pauses(
    path: str | Path,
    out_dir: str | Path,
    target_s: float = config.AUDIO_CHUNK_TARGET_S,
    hard_s: float = config.AUDIO_CHUNK_HARD_S,
    min_silence_ms: int = config.SILENCE_MIN_MS,
) -> list[AudioChunk]:
    """Split an audio file into chunks <= hard_s, preferring silence boundaries near target_s.

    Writes sliced chunk files to out_dir and returns list of AudioChunk records.
    """
    file_path = Path(path)
    output_directory = Path(out_dir)
    try:
        output_directory.mkdir(parents=True, exist_ok=True)
        test_file = output_directory / ".write_test"
        test_file.touch()
        test_file.unlink()
    except (PermissionError, OSError):
        import hashlib
        import tempfile
        path_hash = hashlib.sha256(str(file_path.resolve()).encode("utf-8")).hexdigest()[:8]
        output_directory = Path(tempfile.gettempdir()) / f"bodhan_chunks_{file_path.stem}_{path_hash}"
        output_directory.mkdir(parents=True, exist_ok=True)

    meta = probe(file_path)
    total_duration = meta["duration_s"]

    # Short audio fits in a single chunk without slicing
    if total_duration <= hard_s:
        chunk_file = output_directory / f"{file_path.stem}_chunk_000.wav"
        data, sr = sf.read(str(file_path))
        sf.write(str(chunk_file), data, sr)
        return [AudioChunk(path=str(chunk_file), start_s=0.0, end_s=total_duration)]

    pauses = find_pauses(file_path, min_silence_ms=min_silence_ms)
    data, sr = sf.read(str(file_path))

    chunks: list[AudioChunk] = []
    current_start = 0.0
    chunk_idx = 0

    while current_start < total_duration:
        remaining = total_duration - current_start
        if remaining <= hard_s:
            cut_end = total_duration
        else:
            # Look for pause candidates between target_s - 5s and hard_s
            min_window = current_start + max(5.0, target_s - 5.0)
            max_window = current_start + hard_s
            candidates = [p for p in pauses if min_window <= p <= max_window]

            if candidates:
                # Pick the pause closest to target_s
                ideal = current_start + target_s
                cut_end = min(candidates, key=lambda p: abs(p - ideal))
            else:
                # No pause in window: hard cut at hard_s
                cut_end = current_start + hard_s

        start_sample = int(current_start * sr)
        end_sample = min(len(data), int(cut_end * sr))
        chunk_data = data[start_sample:end_sample]

        chunk_path = output_directory / f"{file_path.stem}_chunk_{chunk_idx:03d}.wav"
        sf.write(str(chunk_path), chunk_data, sr)

        actual_end = round(cut_end, 3)
        actual_start = round(current_start, 3)
        chunks.append(
            AudioChunk(
                path=str(chunk_path),
                start_s=actual_start,
                end_s=actual_end,
            )
        )

        current_start = cut_end
        chunk_idx += 1

    return chunks


def remap_timestamps(
    chunks: list[AudioChunk],
    per_chunk_segments: list[list[Segment]],
) -> list[Segment]:
    """Remap chunk-relative segment timestamps to global audio timeline.

    Critical for generating correct SRT subtitles and global transcripts.
    """
    global_segments: list[Segment] = []

    for chunk, seg_list in zip(chunks, per_chunk_segments, strict=False):
        offset = chunk.start_s
        for seg in seg_list:
            remapped_start = round(offset + seg.start_s, 3)
            remapped_end = round(offset + seg.end_s, 3)
            global_segments.append(
                Segment(
                    start_s=remapped_start,
                    end_s=remapped_end,
                    text=seg.text,
                )
            )

    return global_segments


def to_pcm_wav(file_path: str | Path, target_sr: int = 16000) -> Path:
    """Ensure audio media is 16kHz mono PCM WAV, converting via ffmpeg or soundfile if needed.

    Absorbs format conversions (MP3, M4A, FLAC, OGG, video tracks) per SPEC §3 Golden Rule 2.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {path}")

    # If already 16kHz mono WAV, return as-is
    try:
        meta = probe(path)
        if meta.get("format") == "wav" and meta.get("sample_rate") == target_sr and meta.get("channels") == 1:
            return path
    except Exception:
        pass

    import hashlib
    import subprocess
    import tempfile

    path_hash = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:8]
    wav_path = Path(tempfile.gettempdir()) / f"bodhan_pcm_{path.stem}_{path_hash}.wav"

    # Convert using ffmpeg if available
    cmd = [
        "ffmpeg", "-y", "-i", str(path),
        "-vn", "-ar", str(target_sr),
        "-ac", "1",
        "-c:a", "pcm_s16le",
        str(wav_path),
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return wav_path
    except Exception:
        # Fallback to soundfile if ffmpeg fails
        try:
            data, sr = sf.read(str(path), dtype="float32")
            if data.ndim > 1:
                data = np.mean(data, axis=1)
            sf.write(str(wav_path), data, target_sr)
            return wav_path
        except Exception as exc:
            raise ValueError(f"Failed to transcode audio {path} to PCM WAV: {exc}") from exc

