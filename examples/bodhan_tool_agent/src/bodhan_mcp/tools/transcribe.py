"""
transcribe_audio tool — speech-to-text via Bodhan Indic-Transcribe.

Handles arbitrarily long files by chunking to <30 s segments via ffmpeg.
Cost: ₹0.10 per audio minute.
"""
from __future__ import annotations

import logging
import re
import subprocess
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from typing import Literal

from bodhan_core.cost import estimate_transcribe
from bodhan_core.errors import BodhanError, ErrorCode, error_response, success_response
from bodhan_core.langs import TRANSCRIBE_LANGS, is_supported
from bodhan_core.media.audio import probe_audio, convert_to_wav, chunk_audio

log = logging.getLogger(__name__)

_CHUNK_SECONDS = 25  # leave 5 s headroom below the 30 s limit
_WAV_SAMPLE_RATE = 16000


# ── audio helpers ─────────────────────────────────────────────────────────────

def _get_duration(path: Path) -> float:
    """Return audio duration in seconds via probe_audio."""
    try:
        info = probe_audio(path)
        return float(info.get("duration_seconds", 0.0))
    except Exception:
        return 0.0


def _convert_to_wav(src: Path, dst: Path) -> None:
    """Convert *src* to mono 16-kHz WAV at *dst*."""
    convert_to_wav(src, dst)


def _split_wav(src: Path, chunk_dir: Path, chunk_seconds: int = _CHUNK_SECONDS) -> list[Path]:
    """Split *src* WAV into <chunk_seconds> segments, return sorted paths."""
    return chunk_audio(src, chunk_dir, max_secs=float(chunk_seconds), overlap_secs=0.0)


# ── SRT helpers ───────────────────────────────────────────────────────────────

_SRT_TIMESTAMP = re.compile(
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})"
)


def _srt_to_seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def _seconds_to_srt(total: float) -> str:
    ms = int((total % 1) * 1000)
    total = int(total)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _shift_and_renumber_srt(srt_text: str, offset_seconds: float, index_offset: int) -> tuple[str, int]:
    """Shift all timestamps in an SRT block by *offset_seconds* and renumber from *index_offset*."""
    lines = srt_text.strip().splitlines()
    out: list[str] = []
    block_count = index_offset

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # sequence number line
        if line.isdigit():
            block_count += 1
            out.append(str(block_count))
            i += 1
            # timestamp line
            if i < len(lines):
                ts_line = lines[i]
                shifted = _SRT_TIMESTAMP.sub(
                    lambda m: _seconds_to_srt(
                        _srt_to_seconds(m.group(1), m.group(2), m.group(3), m.group(4))
                        + offset_seconds
                    ),
                    ts_line,
                )
                out.append(shifted)
                i += 1
        else:
            out.append(line)
            i += 1

    return "\n".join(out), block_count


def _merge_srt_chunks(chunks: list[tuple[str, float]]) -> str:
    """Merge (srt_text, offset) tuples into a single valid SRT string."""
    merged_lines: list[str] = []
    block_index = 0
    for srt_text, offset in chunks:
        shifted, block_index = _shift_and_renumber_srt(srt_text, offset, block_index)
        merged_lines.append(shifted)
    return "\n\n".join(merged_lines)


# ── register ──────────────────────────────────────────────────────────────────

def register(mcp, client, workspace, cost_meter) -> None:  # noqa: ANN001
    @mcp.tool()
    async def transcribe_audio(
        file_path: str,
        language: str | None = None,
        output_format: Literal["text", "srt", "vtt"] = "text",
    ) -> dict:
        """Transcribe speech to text using Bodhan Indic-Transcribe. Accepts \
any audio or video file (WAV, MP3, FLAC, M4A, MP4, OGG). Handles files longer \
than 30 seconds automatically by chunking. Set language to an ISO code (e.g. \
'hi', 'ta', 'bn') if known; omit to auto-detect. Use output_format='srt' for \
subtitle files. Returns FILE_NOT_FOUND if the file is missing, \
UNSUPPORTED_LANGUAGE if the language is not supported (supported: \
as,bhb,bho,bn,brx,doi,en,gu,hi,kn,kok,ks,mai,ml,mni,mr,ne,or,pa,sa,sat,sd,\
ta,te,ur). Cost: ₹0.10 per audio minute."""
        t0 = time.monotonic()
        try:
            # 1. Resolve path
            try:
                resolved = workspace.resolve(file_path)
            except BodhanError as exc:
                return exc.to_response()
            except Exception as exc:  # noqa: BLE001
                return error_response(
                    ErrorCode.FILE_NOT_FOUND,
                    f"Cannot resolve path: {exc}",
                    "Check the file path.",
                )
            if not resolved.exists():
                return error_response(
                    ErrorCode.FILE_NOT_FOUND,
                    f"File not found: {file_path}",
                    "Provide a path to an existing audio or video file.",
                )

            # 2. Language validation
            if language and not is_supported(language, "transcribe"):
                return error_response(
                    ErrorCode.UNSUPPORTED_LANGUAGE,
                    f"Language '{language}' is not supported for transcription.",
                    f"Supported: {', '.join(sorted(TRANSCRIBE_LANGS))}.",
                )

            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)

                # 3. Convert to mono 16kHz WAV
                wav_path = tmp / "audio.wav"
                try:
                    _convert_to_wav(resolved, wav_path)
                except subprocess.CalledProcessError as exc:
                    return error_response(
                        ErrorCode.UNSUPPORTED_FORMAT,
                        f"ffmpeg could not convert the file: {exc.stderr.decode(errors='replace')}",
                        "Ensure ffmpeg is installed and the file is a valid audio/video file.",
                    )

                # 4. Duration
                duration = _get_duration(wav_path)
                cost_inr = estimate_transcribe(duration)

                # 5. Check budget
                if not cost_meter.check_budget(cost_inr, remaining=float("inf")):
                    return error_response(
                        ErrorCode.BUDGET_EXCEEDED,
                        "Estimated cost exceeds the configured budget.",
                        "Raise BODHAN_BUDGET_INR or use a shorter file.",
                    )

                # 6. Chunk
                chunk_dir = tmp / "chunks"
                chunk_dir.mkdir()
                if duration > _CHUNK_SECONDS:
                    chunk_paths = _split_wav(wav_path, chunk_dir)
                else:
                    chunk_paths = [wav_path]

                # 7. Transcribe each chunk
                text_parts: list[str] = []
                srt_parts: list[tuple[str, float]] = []  # (srt_text, offset_seconds)
                offset = 0.0
                chunks_processed = 0

                for chunk_path in chunk_paths:
                    chunk_duration = _get_duration(chunk_path)
                    result = await client.transcribe(
                        chunk_path,
                        language=language,
                        response_format=output_format,
                    )
                    chunks_processed += 1

                    if output_format == "text":
                        text_parts.append(result.get("text", ""))
                    elif output_format in {"srt", "vtt"}:
                        srt_parts.append((result.get("text", ""), offset))
                    offset += chunk_duration

                # 8. Merge
                output_file: str | None = None

                if output_format == "text":
                    final_text = " ".join(t.strip() for t in text_parts if t.strip())
                    word_count = len(final_text.split())
                else:
                    final_text = _merge_srt_chunks(srt_parts)
                    word_count = len(
                        [w for w in final_text.split() if not _SRT_TIMESTAMP.search(w)]
                    )
                    # Write subtitle file
                    ext = ".srt" if output_format == "srt" else ".vtt"
                    out_path = workspace.temp_path(
                        f"{resolved.stem}{ext}"
                    )
                    out_path.write_text(final_text, encoding="utf-8")
                    output_file = str(out_path)

                # 9. Record cost
                cost_meter.add("transcribe", cost_inr)

                duration_ms = int((time.monotonic() - t0) * 1000)
                data: dict = {
                    "text": final_text,
                    "word_count": word_count,
                    "duration_seconds": round(duration, 2),
                    "chunks_processed": chunks_processed,
                }
                if output_file:
                    data["output_file"] = output_file

                return success_response(
                    data,
                    cost_inr=cost_inr,
                    cached=False,
                    duration_ms=duration_ms,
                )

        except BodhanError as exc:
            return exc.to_response()
        except Exception as exc:  # noqa: BLE001
            log.error("transcribe_audio error: %s", exc, exc_info=True)
            return error_response(
                ErrorCode.UPSTREAM_ERROR,
                f"Unexpected error: {exc}",
                "Check logs and retry.",
                retryable=True,
            )
