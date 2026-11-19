"""Audio manipulation, natural pause detection, chunking, and subtitle formatting.
Handles MP3 and WAV formats seamlessly.
"""

import os
import re
import subprocess
import tempfile
import textwrap
from pathlib import Path
from typing import List, Optional, Tuple
import soundfile as sf
import numpy as np
from bodhan_mcp.types import SubtitleSegment, ToolError


def get_audio_info(audio_path: str) -> dict:
    """Inspect audio file properties using soundfile or ffprobe."""
    if not os.path.exists(audio_path):
        raise ToolError("FILE_NOT_FOUND", f"Audio file not found: {audio_path}")

    try:
        info = sf.info(audio_path)
        return {
            "duration": float(info.duration),
            "samplerate": int(info.samplerate),
            "channels": int(info.channels),
            "format": str(info.format),
        }
    except Exception:
        # Fallback to ffprobe
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            audio_path,
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            duration = float(res.stdout.strip())
            return {"duration": duration, "samplerate": 16000, "channels": 1, "format": "UNKNOWN"}
        except Exception as e:
            raise ToolError("AUDIO_PROBE_ERROR", f"Could not inspect audio: {e}")


def format_srt_timestamp(seconds: float) -> str:
    """Format seconds into SRT timestamp: HH:MM:SS,mmm"""
    ms = int(round((seconds % 1.0) * 1000))
    total_seconds = int(seconds)
    s = total_seconds % 60
    m = (total_seconds // 60) % 60
    h = total_seconds // 3600
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def split_audio_at_pauses(
    audio_path: str,
    output_dir: Optional[str] = None,
    min_chunk_s: float = 8.0,
    max_chunk_s: float = 28.0,
) -> List[Tuple[float, float, str]]:
    """Cut audio at natural pauses (energy valleys), preferring 8-15s segments.
    Never exceeds max_chunk_s (28.0s).
    Returns list of (start_seconds, end_seconds, chunk_file_path).
    """
    info = get_audio_info(audio_path)
    total_duration = info["duration"]

    if total_duration <= max_chunk_s:
        # No splitting needed
        return [(0.0, total_duration, audio_path)]

    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="audio_chunks_")
    os.makedirs(output_dir, exist_ok=True)

    # Read audio using soundfile (convert to mono float)
    try:
        data, sr = sf.read(audio_path, always_2d=False)
        if len(data.shape) > 1:
            data = np.mean(data, axis=1)
    except Exception:
        # Convert to temp wav via ffmpeg first if soundfile cannot read direct mp3 on Windows
        temp_wav = os.path.join(output_dir, "temp_probe.wav")
        subprocess.run(["ffmpeg", "-y", "-i", audio_path, "-ac", "1", "-ar", "16000", temp_wav], check=True, capture_output=True)
        data, sr = sf.read(temp_wav, always_2d=False)
        if len(data.shape) > 1:
            data = np.mean(data, axis=1)

    total_samples = len(data)
    frame_ms = 100  # 100ms frames for RMS energy
    frame_len = int(sr * (frame_ms / 1000.0))
    num_frames = total_samples // frame_len

    if num_frames == 0:
        return [(0.0, total_duration, audio_path)]

    # Compute frame-level RMS energy
    padded = data[: num_frames * frame_len].reshape(num_frames, frame_len)
    rms = np.sqrt(np.mean(padded ** 2, axis=1))

    chunks: List[Tuple[float, float, str]] = []
    current_start_s = 0.0
    ext = os.path.splitext(audio_path)[1] or ".wav"

    while current_start_s < total_duration:
        remaining_s = total_duration - current_start_s
        if remaining_s <= max_chunk_s:
            # Last segment
            end_s = total_duration
            chunk_file = os.path.join(output_dir, f"chunk_{len(chunks):03d}{ext}")
            start_sample = int(current_start_s * sr)
            sf.write(chunk_file, data[start_sample:], sr)
            chunks.append((current_start_s, end_s, chunk_file))
            break

        # Search for lowest energy frame in window [current_start + min_chunk_s, current_start + max_chunk_s]
        search_start_s = current_start_s + min_chunk_s
        search_end_s = current_start_s + max_chunk_s

        start_frame = int(search_start_s * 1000.0 / frame_ms)
        end_frame = min(num_frames, int(search_end_s * 1000.0 / frame_ms))

        if start_frame >= end_frame:
            cut_frame = end_frame
        else:
            search_window = rms[start_frame:end_frame]
            min_idx = int(np.argmin(search_window))
            cut_frame = start_frame + min_idx

        cut_s = cut_frame * (frame_ms / 1000.0)
        # Ensure we make forward progress
        if cut_s <= current_start_s:
            cut_s = min(current_start_s + 15.0, total_duration)

        chunk_file = os.path.join(output_dir, f"chunk_{len(chunks):03d}{ext}")
        start_samp = int(current_start_s * sr)
        end_samp = int(cut_s * sr)
        sf.write(chunk_file, data[start_samp:end_samp], sr)

        chunks.append((current_start_s, cut_s, chunk_file))
        current_start_s = cut_s

    return chunks


def build_srt_content(segments: List[SubtitleSegment]) -> str:
    """Generate valid standard SRT file content from segments."""
    lines = []
    for seg in segments:
        lines.append(str(seg.index))
        lines.append(f"{seg.start_time} --> {seg.end_time}")
        lines.append(seg.text.strip())
        lines.append("")  # Blank line separator
    return "\n".join(lines).strip() + "\n"


def split_text_into_cues(
    text: str,
    start_seconds: float,
    end_seconds: float,
    max_chars_per_line: int = 42,
    max_lines: int = 2,
    starting_index: int = 1,
) -> List[SubtitleSegment]:
    """Split transcribed or translated text into professional SRT cues:
    - Splits at natural sentence boundaries (., !, ?, ।, ॥, \n).
    - Sub-splits long sentences at clause boundaries (comma, semicolon) or word boundaries
      so each cue fits within max_lines * max_chars_per_line.
    - Formats text into at most max_lines lines, each <= max_chars_per_line.
    - Distributes chunk duration [start_seconds, end_seconds] proportionally to cue length.
    - Guarantees strictly monotonic, non-overlapping timecodes.
    """
    clean_text = text.strip()
    if not clean_text:
        return []

    max_cue_chars = max_chars_per_line * max_lines

    # 1. Primary sentence splitting
    raw_sentences = [s.strip() for s in re.split(r'(?<=[.!?।॥\n])\s+', clean_text) if s.strip()]
    if not raw_sentences:
        raw_sentences = [clean_text]

    # 2. Refine into cue units <= max_cue_chars
    cue_units: List[str] = []
    for sent in raw_sentences:
        if len(sent) <= max_cue_chars:
            cue_units.append(sent)
        else:
            # Sub-split at commas/semicolons/dashes
            clauses = [c.strip() for c in re.split(r'(?<=[,;:\u2014])\s+', sent) if c.strip()]
            cur_unit = ""
            for c in clauses:
                if not cur_unit:
                    cur_unit = c
                elif len(cur_unit) + len(c) + 1 <= max_cue_chars:
                    cur_unit += " " + c
                else:
                    cue_units.append(cur_unit)
                    cur_unit = c
            if cur_unit:
                # If still longer than max_cue_chars, split on word boundaries
                if len(cur_unit) > max_cue_chars:
                    words = cur_unit.split()
                    sub_buf = ""
                    for w in words:
                        if not sub_buf:
                            sub_buf = w
                        elif len(sub_buf) + len(w) + 1 <= max_cue_chars:
                            sub_buf += " " + w
                        else:
                            cue_units.append(sub_buf)
                            sub_buf = w
                    if sub_buf:
                        cue_units.append(sub_buf)
                else:
                    cue_units.append(cur_unit)

    if not cue_units:
        cue_units = [clean_text]

    # 3. Format lines for each cue (wrap to max_chars_per_line, max max_lines per cue)
    formatted_cues: List[str] = []
    for unit in cue_units:
        wrapped = textwrap.wrap(unit, width=max_chars_per_line)
        if len(wrapped) <= max_lines:
            formatted_cues.append("\n".join(wrapped))
        else:
            # Group into chunks of at most max_lines
            for i in range(0, len(wrapped), max_lines):
                group = wrapped[i:i + max_lines]
                formatted_cues.append("\n".join(group))

    if not formatted_cues:
        formatted_cues = [clean_text]

    # 4. Distribute timing proportionally
    total_duration = max(0.2, end_seconds - start_seconds)
    weights = [max(1, len(c.replace("\n", " ").strip())) for c in formatted_cues]
    total_weight = sum(weights)

    segments: List[SubtitleSegment] = []
    accum_weight = 0

    for idx, (cue_str, w) in enumerate(zip(formatted_cues, weights)):
        c_start = start_seconds + total_duration * (accum_weight / total_weight)
        accum_weight += w
        c_end = start_seconds + total_duration * (accum_weight / total_weight)

        c_start = round(c_start, 3)
        c_end = round(c_end, 3)
        if c_end <= c_start:
            c_end = round(c_start + 0.1, 3)

        segments.append(
            SubtitleSegment(
                index=starting_index + idx,
                start_time=format_srt_timestamp(c_start),
                end_time=format_srt_timestamp(c_end),
                start_seconds=c_start,
                end_seconds=c_end,
                text=cue_str,
            )
        )

    return segments


def stitch_audio_files(chunk_paths: List[str], output_path: str) -> str:
    """Stitch multiple audio chunks (WAV or MP3) into a single cohesive output file via ffmpeg."""
    if not chunk_paths:
        raise ToolError("EMPTY_AUDIO_LIST", "No audio chunks provided to stitch.")

    if len(chunk_paths) == 1:
        # Just copy/link if single chunk
        with open(chunk_paths[0], "rb") as src, open(output_path, "wb") as dst:
            dst.write(src.read())
        return output_path

    # Use ffmpeg concat demuxer
    temp_list = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
    try:
        for p in chunk_paths:
            # Escape path for ffmpeg concat
            escaped = os.path.abspath(p).replace("\\", "/")
            temp_list.write(f"file '{escaped}'\n")
        temp_list.close()

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", temp_list.name,
            "-c", "copy" if output_path.lower().endswith(".mp3") else "pcm_s16le",
            output_path,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            # If copy failed (codec mismatch), re-encode
            cmd_reencode = [
                "ffmpeg", "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", temp_list.name,
                output_path,
            ]
            subprocess.run(cmd_reencode, capture_output=True, check=True)
        return output_path
    finally:
        if os.path.exists(temp_list.name):
            os.remove(temp_list.name)

