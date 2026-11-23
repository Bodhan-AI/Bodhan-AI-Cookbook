"""Speech-to-text tool with automatic chunking for files > 30 seconds."""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import List, Optional, Tuple

from ..client.client import BodhanClient
from ..models import TranscribeResult

CHUNK_SECONDS = 25.0  # Safe window well under Bodhan's 30-second limit


def _chunk_wav_data(file_path: Path, seconds: float = CHUNK_SECONDS) -> List[Tuple[int, bytes, float, float]]:
    """Yield chunks (index, wav_bytes, start_sec, end_sec) for a WAV file using standard library."""
    chunks = []
    with contextlib.closing(wave.open(str(file_path), "rb")) as wf:
        params = wf.getparams()
        framerate = params.framerate
        frames_per_chunk = int(framerate * seconds)
        total_frames = wf.getnframes()
        total_duration = total_frames / float(framerate) if framerate > 0 else 0.0

        idx = 0
        current_frame = 0
        while current_frame < total_frames:
            frames = wf.readframes(frames_per_chunk)
            if not frames:
                break
            num_frames_read = len(frames) // (params.nchannels * params.sampwidth)
            start_sec = current_frame / float(framerate)
            current_frame += num_frames_read
            end_sec = current_frame / float(framerate)

            buf = io.BytesIO()
            with contextlib.closing(wave.open(buf, "wb")) as out:
                out.setnchannels(params.nchannels)
                out.setsampwidth(params.sampwidth)
                out.setframerate(framerate)
                out.writeframes(frames)

            chunks.append((idx, buf.getvalue(), start_sec, end_sec))
            idx += 1

    return chunks


def _format_srt_time(seconds: float) -> str:
    """Format seconds into SRT timestamp 00:00:00,000."""
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"


def bodhan_transcribe(
    audio_file: str,
    language: Optional[str] = None,
    client: Optional[BodhanClient] = None,
) -> TranscribeResult:
    """Transcribe an audio file of any length using Bodhan Indic-Transcribe.
    
    Handles files exceeding the 30-second limit by chunking, transcribing segments,
    and recombining transcripts with time offsets for subtitles.
    """
    if not audio_file or not isinstance(audio_file, str):
        raise ValueError("'audio_file' must be a non-empty file path string.")
    path = Path(audio_file).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_file}")
    if path.is_dir():
        raise ValueError(f"Expected an audio file, got directory: {audio_file}")

    b_client = client or BodhanClient()

    # Handle non-WAV containers (including .mp3, .mp4, .ogg, .m4a, .flac, .webm) via ffmpeg, else fail fast
    tmp_wav_path: Optional[Path] = None
    work_path = path
    suffix = path.suffix.lower()
    non_wav_containers = (".mp3", ".mp4", ".mov", ".mkv", ".avi", ".ogg", ".opus", ".m4a", ".flac", ".aac", ".webm")

    def _convert_with_ffmpeg(src: Path) -> Path:
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            raise ValueError(
                f"Audio container '{src.suffix.lower()}' ({src.name}) is not directly accepted by the ASR backend and "
                f"ffmpeg is not installed for automatic conversion. Convert to 16kHz mono WAV first "
                f"(e.g. `ffmpeg -i {src.name} -ar 16000 -ac 1 output.wav`)."
            )
        fd, tmp_name = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        dest = Path(tmp_name)
        proc = subprocess.run(
            [ffmpeg_bin, "-y", "-i", str(src), "-ar", "16000", "-ac", "1", str(dest)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            dest.unlink(missing_ok=True)
            raise ValueError(f"Failed to decode {src.name} with ffmpeg: {proc.stderr[:200]}")
        return dest

    if suffix in non_wav_containers and not b_client.mock_mode:
        tmp_wav_path = _convert_with_ffmpeg(path)
        work_path = tmp_wav_path

    try:
        # Inspect WAV duration & validate header integrity
        try:
            with contextlib.closing(wave.open(str(work_path), "rb")) as wf:
                framerate = wf.getframerate()
                duration = wf.getnframes() / float(framerate) if framerate > 0 else 0.0
        except Exception as wav_err:
            if not b_client.mock_mode:
                # Try ffmpeg fallback for non-PCM WAVs (e.g. IEEE float) before failing
                if work_path.suffix.lower() == ".wav" and shutil.which("ffmpeg"):
                    tmp_wav_path = _convert_with_ffmpeg(work_path)
                    work_path = tmp_wav_path
                    with contextlib.closing(wave.open(str(work_path), "rb")) as wf:
                        framerate = wf.getframerate()
                        duration = wf.getnframes() / float(framerate) if framerate > 0 else 0.0
                else:
                    kind = "WAV audio" if work_path.suffix.lower() == ".wav" else "audio"
                    raise ValueError(
                        f"Invalid or corrupted {kind} file '{path.name}': {wav_err}. "
                        f"File cannot be decoded for transcription."
                    ) from wav_err
            else:
                raw_bytes = work_path.read_bytes()
                duration = len(raw_bytes) / (24000 * 2)  # heuristic estimation in mock mode

        if duration <= CHUNK_SECONDS + 2.0 or (b_client.mock_mode and suffix != ".wav"):
            # Single chunk invocation
            raw_bytes = work_path.read_bytes()
            res = b_client.transcribe(
                audio_bytes=raw_bytes,
                duration_seconds=duration,
                language=language,
                file_name=work_path.name,
            )
            res.chunk_count = 1
            res.srt_subtitles = f"1\n00:00:00,000 --> {_format_srt_time(duration)}\n{res.text}\n"
            return res

        # Multi-chunk invocation for longer audio
        chunks = _chunk_wav_data(work_path, seconds=CHUNK_SECONDS)
        transcripts = []
        srt_entries = []
        total_cost = 0.0
        detected_lang = language

        for idx, chunk_bytes, start_sec, end_sec in chunks:
            chunk_duration = end_sec - start_sec
            chunk_res = b_client.transcribe(
                audio_bytes=chunk_bytes,
                duration_seconds=chunk_duration,
                language=language,
                file_name=f"{work_path.stem}_part{idx}.wav",
            )
            if chunk_res.text:
                transcripts.append(chunk_res.text)
                srt_entries.append(
                    f"{idx + 1}\n{_format_srt_time(start_sec)} --> {_format_srt_time(end_sec)}\n{chunk_res.text}\n"
                )
            total_cost += chunk_res.cost_inr
            if not detected_lang and chunk_res.language:
                detected_lang = chunk_res.language

        combined_text = " ".join(transcripts).strip()
        return TranscribeResult(
            text=combined_text,
            language=detected_lang,
            duration_seconds=round(duration, 2),
            chunk_count=len(chunks),
            srt_subtitles="\n".join(srt_entries),
            cost_inr=round(total_cost, 4),
            cached=False,
        )
    finally:
        if tmp_wav_path and tmp_wav_path.exists():
            tmp_wav_path.unlink(missing_ok=True)
