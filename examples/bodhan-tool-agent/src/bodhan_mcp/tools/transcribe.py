
from __future__ import annotations

import re
import subprocess
import tempfile
import wave
from collections.abc import Iterator
from pathlib import Path

from ..cache import ComputeResult, DiskCache, cost_transcribe, hash_bytes, make_key
from ..client import BodhanClient
from ..models import ErrorCode, ToolError, Usage, failure, success
from ..workspace import Workspace

MAX_AUDIO_WINDOW_SECONDS = 30.0
_FFMPEG_SEGMENT_SECONDS = 29.5
_AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".aac"}
_VIDEO_SUFFIXES = {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".webm"}
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?।॥])\s+")


def _default_cache(workspace: Workspace, client: BodhanClient | None) -> DiskCache:
    if client is not None and type(client) is not BodhanClient:
        return DiskCache(workspace.out_dir / ".cache")
    return DiskCache.from_env()


def _ffmpeg_executable() -> str:
    try:
        import imageio_ffmpeg
    except ImportError:
        raise ToolError(
            ErrorCode.UNSUPPORTED_FORMAT,
            "Media segmentation requires the bundled FFmpeg utility.",
            hint="Install the project dependencies again, including imageio-ffmpeg.",
        ) from None
    return imageio_ffmpeg.get_ffmpeg_exe()


def _format_srt_timestamp(seconds: float) -> str:
    total_ms = max(0, round(seconds * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _build_srt_cues(segments: list[dict[str, object]]) -> str:
    cues: list[str] = []
    cue_index = 1
    for seg in segments:
        text = str(seg.get("text", "")).strip()
        if not text:
            continue
        start = float(seg["start_seconds"])  # type: ignore[arg-type]
        end = float(seg["end_seconds"])  # type: ignore[arg-type]
        if end <= start:
            end = start + 0.5
        sentences = [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]
        if len(sentences) <= 1:
            cues.append(
                f"{cue_index}\n"
                f"{_format_srt_timestamp(start)} --> {_format_srt_timestamp(end)}\n"
                f"{text}"
            )
            cue_index += 1
            continue
        total_chars = sum(len(s) for s in sentences) or 1
        cursor = start
        span = end - start
        for idx, sentence in enumerate(sentences):
            if idx == len(sentences) - 1:
                seg_end = end
            else:
                seg_end = min(end, cursor + span * (len(sentence) / total_chars))
            if seg_end <= cursor:
                seg_end = min(end, cursor + 0.1)
            cues.append(
                f"{cue_index}\n"
                f"{_format_srt_timestamp(cursor)} --> {_format_srt_timestamp(seg_end)}\n"
                f"{sentence}"
            )
            cue_index += 1
            cursor = seg_end
    return "\n\n".join(cues) + ("\n" if cues else "")


def _audio_chunks(path: Path) -> Iterator[tuple[str, bytes, float]]:
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as source:
                frame_rate = source.getframerate()
                frame_width = source.getnchannels() * source.getsampwidth()
                frames_per_chunk = int(_FFMPEG_SEGMENT_SECONDS * frame_rate)
                if frame_rate <= 0 or frame_width <= 0 or frames_per_chunk <= 0:
                    raise ToolError(
                        ErrorCode.UNSUPPORTED_FORMAT,
                        f"{path.name!r} has invalid WAV audio metadata.",
                    )
                index = 0
                while True:
                    frames = source.readframes(frames_per_chunk)
                    if not frames:
                        break
                    index += 1
                    duration = len(frames) / frame_width / frame_rate
                    chunk = tempfile.SpooledTemporaryFile()
                    with wave.open(chunk, "wb") as target:
                        target.setnchannels(source.getnchannels())
                        target.setsampwidth(source.getsampwidth())
                        target.setframerate(frame_rate)
                        target.setcomptype(source.getcomptype(), source.getcompname())
                        target.writeframes(frames)
                    chunk.seek(0)
                    yield f"{path.stem}-part-{index:03d}.wav", chunk.read(), duration
                    chunk.close()
        except (EOFError, wave.Error) as exc:
            raise ToolError(
                ErrorCode.UNSUPPORTED_FORMAT,
                f"Could not read {path.name!r} as a supported WAV file.",
                hint="Use an uncompressed PCM WAV file.",
            ) from exc
        return

    ffmpeg = _ffmpeg_executable()
    with tempfile.TemporaryDirectory(prefix="bodhan-audio-") as directory:
        output_pattern = str(Path(directory) / "part-%04d.wav")
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-vn",
            "-map",
            "0:a:0",
            "-f",
            "segment",
            "-segment_time",
            str(_FFMPEG_SEGMENT_SECONDS),
            "-reset_timestamps",
            "1",
            "-c:a",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            output_pattern,
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, timeout=300)
        except subprocess.TimeoutExpired as exc:
            raise ToolError(
                ErrorCode.UPSTREAM_TIMEOUT,
                f"Preparing {path.name!r} for transcription exceeded 300 seconds.",
                hint="Use a smaller media file or split it before retrying.",
            ) from exc
        except (OSError, subprocess.CalledProcessError) as exc:
            detail = ""
            if isinstance(exc, subprocess.CalledProcessError):
                detail = exc.stderr.decode("utf-8", errors="replace")[-300:]
            raise ToolError(
                ErrorCode.UNSUPPORTED_FORMAT,
                f"Could not decode audio stream from {path.name!r}."
                + (f" {detail}" if detail else ""),
                hint="Check that the file contains a supported audio stream.",
            ) from exc

        parts = sorted(Path(directory).glob("part-*.wav"))
        if not parts:
            raise ToolError(
                ErrorCode.INVALID_ARGUMENT,
                f"{path.name!r} contains no decodable audio.",
            )
        for index, part in enumerate(parts, start=1):
            try:
                with wave.open(str(part), "rb") as source:
                    duration = source.getnframes() / source.getframerate()
                if duration > MAX_AUDIO_WINDOW_SECONDS:
                    raise ToolError(
                        ErrorCode.INTERNAL,
                        "The audio decoder produced a window over 30 seconds.",
                    )
                yield f"{path.stem}-part-{index:03d}.wav", part.read_bytes(), duration
            except (EOFError, wave.Error, ZeroDivisionError) as exc:
                raise ToolError(
                    ErrorCode.UNSUPPORTED_FORMAT,
                    f"Could not read a prepared audio segment from {path.name!r}.",
                ) from exc


def transcribe_audio(
    workspace: Workspace,
    file_path: str,
    language: str | None = None,
    client: BodhanClient | None = None,
    *,
    output_format: str = "text",
    cache: DiskCache | None = None,
) -> dict:
    total_duration = 0.0
    charged_cost = 0.0
    avoided_cost = 0.0
    total_latency_ms = 0
    transcribed_chunks = 0
    try:
        if output_format not in {"text", "srt"}:
            raise ToolError(
                ErrorCode.INVALID_ARGUMENT,
                f"`output_format` must be 'text' or 'srt', got {output_format!r}.",
            )
        path = workspace.resolve_input(file_path, suffixes=_AUDIO_SUFFIXES | _VIDEO_SUFFIXES)
        api_client = client or BodhanClient()
        disk_cache = cache or _default_cache(workspace, client)
        transcripts: list[str] = []
        segments: list[dict[str, object]] = []
        warnings: list[str] = []
        detected_language = language
        all_cached = True

        for filename, audio_bytes, duration in _audio_chunks(path):
            start_s = round(total_duration, 3)
            end_s = round(total_duration + duration, 3)
            cache_key = make_key(
                "transcribe",
                "chunk",
                {"model": "indic-transcribe", "language": language},
                [hash_bytes(audio_bytes)],
            )

            def _compute(
                bytes_val: bytes = audio_bytes,
                fname: str = filename,
                dur: float = duration,
            ) -> ComputeResult:
                resp = api_client.transcribe(
                    audio_bytes=bytes_val,
                    filename=fname,
                    language=language,
                )
                text_val = resp.get("text", "")
                if not isinstance(text_val, str):
                    raise ToolError(
                        ErrorCode.PARTIAL_FAILURE,
                        f"Transcription returned invalid text for {fname!r}.",
                    )
                resp_usage = resp.get("usage")
                chunk_cost = (
                    resp_usage.cost_inr
                    if isinstance(resp_usage, Usage) and resp_usage.cost_inr > 0
                    else cost_transcribe(dur)
                )
                latency = resp_usage.latency_ms if isinstance(resp_usage, Usage) else 0
                return ComputeResult(
                    value={
                        "text": text_val,
                        "language": resp.get("language", language),
                        "latency_ms": latency,
                    },
                    cost_inr=chunk_cost,
                )

            cached_res = disk_cache.get_or_compute_sync("transcribe", cache_key, _compute)
            total_duration += duration
            transcribed_chunks += 1
            charged_cost += cached_res.charged_inr
            avoided_cost += cached_res.avoided_inr
            total_latency_ms += (
                0 if cached_res.cached else int(cached_res.entry.value.get("latency_ms", 0))
            )
            if not cached_res.cached:
                all_cached = False

            text = str(cached_res.entry.value.get("text", "")).strip()
            if not text:
                warnings.append(
                    f"Segment {filename!r} ({start_s:.1f}s-{end_s:.1f}s) returned no speech."
                )
            else:
                transcripts.append(text)
            segments.append(
                {
                    "index": transcribed_chunks,
                    "start_seconds": start_s,
                    "end_seconds": end_s,
                    "text": text,
                }
            )
            detected = cached_res.entry.value.get("language")
            if isinstance(detected, str) and detected:
                detected_language = detected

        if transcribed_chunks == 0:
            raise ToolError(
                ErrorCode.INVALID_ARGUMENT,
                f"{path.name!r} contains no audio to transcribe.",
            )
        if not transcripts:
            raise ToolError(
                ErrorCode.PARTIAL_FAILURE,
                f"Transcription returned no speech for {path.name!r}.",
                hint="Check audio quality or retry with a different supported format.",
            )

        if output_format == "srt":
            transcript_text = _build_srt_cues(segments)
            artifact = workspace.write_text(
                f"{path.stem}-subtitles",
                ".srt",
                transcript_text,
                kind="subtitles",
            )
        else:
            transcript_text = "\n".join(transcripts)
            artifact = workspace.write_text(
                f"{path.stem}-transcript",
                ".txt",
                transcript_text,
                kind="transcript",
            )

        usage = Usage(
            model="indic-transcribe",
            cost_inr=round(charged_cost, 6),
            latency_ms=total_latency_ms,
            cached=all_cached,
            avoided_inr=round(avoided_cost, 6),
            units={"audio_seconds": total_duration, "chunks": float(transcribed_chunks)},
        )

        return success(
            {
                "source_path": str(path),
                "transcript": transcript_text,
                "language_detected": detected_language,
                "duration_seconds": round(total_duration, 3),
                "chunk_count": transcribed_chunks,
                "segments": segments,
                "output_format": output_format,
                "coverage_complete": True,
            },
            artifacts=[artifact],
            usage=usage,
            warnings=warnings,
        )
    except ToolError as err:
        if transcribed_chunks > 0:
            err.details = {
                **(err.details or {}),
                "partial_usage": Usage(
                    model="indic-transcribe",
                    cost_inr=round(charged_cost, 6),
                    latency_ms=total_latency_ms,
                    units={"audio_seconds": total_duration, "chunks": float(transcribed_chunks)},
                ).to_dict(),
            }
        return failure(err)
    except OSError as exc:
        return failure(
            ToolError(
                ErrorCode.INTERNAL,
                f"Transcription could not read {file_path!r}: {exc.strerror or exc}",
                details={"file_path": file_path},
            )
        )
    except Exception as exc:
        return failure(
            ToolError(
                ErrorCode.INTERNAL,
                f"Transcription failed: {type(exc).__name__}: {exc}",
                details={"file_path": file_path},
            )
        )