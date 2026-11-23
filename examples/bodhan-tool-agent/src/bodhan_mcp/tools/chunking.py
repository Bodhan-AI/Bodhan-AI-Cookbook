from __future__ import annotations

import io
import math
import re
import wave
from pathlib import Path

from ..models import ErrorCode, ToolError, failure, success
from ..workspace import Workspace

MAX_AUDIO_WINDOW_SECONDS = 30.0
MAX_TEXT_CHUNK_CHARACTERS = 5_000
_TEXT_SUFFIXES = {".md", ".markdown", ".srt", ".txt", ".vtt"}
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?।॥])(?=\s|$)")


def chunk_audio_manifest(
    workspace: Workspace,
    file_path: str,
    max_duration_s: float = 30.0,
) -> dict:
    try:
        if (
            isinstance(max_duration_s, bool)
            or not isinstance(max_duration_s, int | float)
            or not math.isfinite(max_duration_s)
            or max_duration_s <= 0
            or max_duration_s > MAX_AUDIO_WINDOW_SECONDS
        ):
            raise ToolError(
                ErrorCode.INVALID_ARGUMENT,
                f"`max_duration_s` must be greater than 0 and no more than "
                f"{MAX_AUDIO_WINDOW_SECONDS:g}.",
            )

        path = workspace.resolve_input(file_path)
        if path.suffix.lower() == ".wav":
            return _chunk_wav(workspace, path, float(max_duration_s))
        if path.suffix.lower() in _TEXT_SUFFIXES:
            return _chunk_text(workspace, path)
        raise ToolError(
            ErrorCode.UNSUPPORTED_FORMAT,
            f"Cannot safely chunk {path.suffix or 'this file type'!r} with standard-library tools.",
            hint="Use PCM WAV audio or a UTF-8 text, Markdown, SRT, or VTT file.",
        )
    except ToolError as err:
        return failure(err)
    except OSError as exc:
        return failure(
            ToolError(
                ErrorCode.INTERNAL,
                f"Could not chunk {file_path!r}: {exc.strerror or exc}.",
            )
        )


def _chunk_wav(workspace: Workspace, path: Path, max_duration_s: float) -> dict:
    artifacts = []
    chunks: list[dict[str, object]] = []
    try:
        with wave.open(str(path), "rb") as source:
            frame_rate = source.getframerate()
            frames_per_chunk = int(max_duration_s * frame_rate)
            if frame_rate <= 0 or frames_per_chunk < 1:
                raise ToolError(
                    ErrorCode.UNSUPPORTED_FORMAT,
                    f"{path.name!r} has an invalid sample rate for audio chunking.",
                )
            total_frames = source.getnframes()
            if total_frames == 0:
                raise ToolError(ErrorCode.INVALID_ARGUMENT, f"{path.name!r} contains no audio.")

            index = 0
            while True:
                frame_data = source.readframes(frames_per_chunk)
                if not frame_data:
                    break
                index += 1
                chunk_frames = len(frame_data) // (source.getnchannels() * source.getsampwidth())
                buffer = io.BytesIO()
                with wave.open(buffer, "wb") as target:
                    target.setnchannels(source.getnchannels())
                    target.setsampwidth(source.getsampwidth())
                    target.setframerate(frame_rate)
                    target.setcomptype(source.getcomptype(), source.getcompname())
                    target.writeframes(frame_data)

                artifact = workspace.write_bytes(
                    f"{path.stem}-chunk-{index:03d}",
                    ".wav",
                    buffer.getvalue(),
                    kind="audio",
                )
                artifacts.append(artifact)
                chunks.append(
                    {
                        **artifact.to_dict(),
                        "index": index,
                        "duration_seconds": round(chunk_frames / frame_rate, 3),
                    }
                )
    except (EOFError, wave.Error) as exc:
        raise ToolError(
            ErrorCode.UNSUPPORTED_FORMAT,
            f"Could not read {path.name!r} as a supported PCM WAV file.",
            hint="Use an uncompressed PCM WAV file.",
        ) from exc

    return success(
        {
            "source_path": str(path),
            "chunk_count": len(chunks),
            "max_duration_seconds": max_duration_s,
            "chunks": chunks,
        },
        artifacts=artifacts,
    )


def _chunk_text(workspace: Workspace, path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ToolError(
            ErrorCode.UNSUPPORTED_FORMAT,
            f"Could not decode {path.name!r} as UTF-8 text.",
            hint="Save the file as UTF-8 and try again.",
        ) from exc
    if not text.strip():
        raise ToolError(ErrorCode.INVALID_ARGUMENT, f"{path.name!r} contains no text.")

    pieces: list[str] = []
    for sentence in _SENTENCE_BOUNDARY.split(text):
        pieces.extend(_split_long_text(sentence, MAX_TEXT_CHUNK_CHARACTERS))

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) > MAX_TEXT_CHUNK_CHARACTERS:
            chunks.append(current)
            current = ""
        if len(piece) > MAX_TEXT_CHUNK_CHARACTERS:
            raise ToolError(
                ErrorCode.INTERNAL,
                "Text chunking produced a chunk over the configured character limit.",
            )
        current += piece
    if current:
        chunks.append(current)

    artifacts = []
    manifest_chunks: list[dict[str, object]] = []
    for index, chunk in enumerate(chunks, start=1):
        artifact = workspace.write_text(
            f"{path.stem}-chunk-{index:03d}",
            ".txt",
            chunk,
            kind="text",
        )
        artifacts.append(artifact)
        manifest_chunks.append(
            {
                **artifact.to_dict(),
                "index": index,
                "characters": len(chunk),
            }
        )

    return success(
        {
            "source_path": str(path),
            "chunk_count": len(manifest_chunks),
            "max_characters_per_chunk": MAX_TEXT_CHUNK_CHARACTERS,
            "chunks": manifest_chunks,
        },
        artifacts=artifacts,
    )


def _split_long_text(text: str, limit: int) -> list[str]:
    parts: list[str] = []
    while len(text) > limit:
        boundary = max(text.rfind(" ", 0, limit + 1), text.rfind("\n", 0, limit + 1))
        split_at = boundary + 1 if boundary > 0 else limit
        parts.append(text[:split_at])
        text = text[split_at:]
    if text:
        parts.append(text)
    return parts
