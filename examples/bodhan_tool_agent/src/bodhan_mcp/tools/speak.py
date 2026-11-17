"""
synthesize_speech tool — text-to-speech via Bodhan Indic-Speak.

Splits long text into sentence-level chunks, synthesises each, then stitches
the WAV files together. Cost: ₹6 per 10,000 input characters.
"""
from __future__ import annotations

import logging
import re
import struct
import tempfile
import time
from pathlib import Path
from typing import Literal

from bodhan_core.cost import estimate_speak
from bodhan_core.errors import BodhanError, ErrorCode, error_response, success_response
from bodhan_core.langs import SPEAK_LANGS, is_supported, pick_voice

log = logging.getLogger(__name__)

_MAX_CHUNK_CHARS = 180  # keep sentence chunks short


# ── sentence splitter ─────────────────────────────────────────────────────────

_SENTENCE_END = re.compile(r"(?<=[.!?।])\s+")


def _split_sentences(text: str, max_chars: int = _MAX_CHUNK_CHARS) -> list[str]:
    """Split *text* on sentence boundaries; re-split over-long pieces."""
    raw = _SENTENCE_END.split(text.strip())
    result: list[str] = []
    for piece in raw:
        piece = piece.strip()
        if not piece:
            continue
        if len(piece) <= max_chars:
            result.append(piece)
        else:
            # Hard-split on newlines first, then on max_chars boundary
            for sub in piece.splitlines():
                sub = sub.strip()
                while len(sub) > max_chars:
                    result.append(sub[:max_chars])
                    sub = sub[max_chars:]
                if sub:
                    result.append(sub)
    return [s for s in result if s]


# ── WAV stitching ─────────────────────────────────────────────────────────────

def _read_wav_data(path: Path) -> tuple[bytes, int, int, int]:
    """
    Parse a WAV file and return (pcm_data, sample_rate, num_channels, bit_depth).
    Raises ValueError on malformed files.
    """
    raw = path.read_bytes()
    if raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise ValueError(f"Not a valid WAV file: {path}")
    offset = 12
    sample_rate = num_channels = bit_depth = 0
    pcm: bytes = b""
    while offset < len(raw):
        chunk_id = raw[offset:offset + 4]
        chunk_size = struct.unpack_from("<I", raw, offset + 4)[0]
        chunk_data = raw[offset + 8: offset + 8 + chunk_size]
        if chunk_id == b"fmt ":
            num_channels, sample_rate, _, _, bit_depth = struct.unpack_from("<HHIIHH", chunk_data)
        elif chunk_id == b"data":
            pcm = chunk_data
        offset += 8 + chunk_size
    return pcm, sample_rate, num_channels, bit_depth


def _write_wav(pcm: bytes, sample_rate: int, channels: int, bit_depth: int, path: Path) -> None:
    """Write a minimal valid WAV file."""
    byte_rate = sample_rate * channels * (bit_depth // 8)
    block_align = channels * (bit_depth // 8)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(pcm),
        b"WAVE",
        b"fmt ",
        16,                  # PCM fmt chunk size
        1,                   # PCM format
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bit_depth,
        b"data",
        len(pcm),
    )
    path.write_bytes(header + pcm)


def _stitch_wavs(chunk_paths: list[Path], output_path: Path) -> float:
    """Concatenate WAV chunks into a single WAV; return total duration in seconds."""
    all_pcm = bytearray()
    sample_rate = channels = bit_depth = 0

    for cp in chunk_paths:
        pcm, sr, ch, bd = _read_wav_data(cp)
        if not sample_rate:
            sample_rate, channels, bit_depth = sr, ch, bd
        all_pcm.extend(pcm)

    if not all_pcm:
        raise ValueError("No audio data to stitch.")

    _write_wav(bytes(all_pcm), sample_rate, channels, bit_depth, output_path)
    bytes_per_sample = channels * (bit_depth // 8)
    total_samples = len(all_pcm) // bytes_per_sample
    duration = total_samples / sample_rate if sample_rate else 0.0
    return duration


def _add_wav_comment(path: Path, comment: str) -> None:
    """
    Append a simple LIST/INFO chunk with ICMT comment to a WAV file.
    This is the closest native WAV equivalent to ID3 metadata.
    """
    try:
        raw = path.read_bytes()
        icmt = comment.encode("utf-8") + b"\x00"
        if len(icmt) % 2:
            icmt += b"\x00"
        list_data = b"INFO" + b"ICMT" + struct.pack("<I", len(icmt)) + icmt
        list_chunk = b"LIST" + struct.pack("<I", len(list_data)) + list_data
        # Patch RIFF size
        new_size = struct.pack("<I", len(raw) - 8 + len(list_chunk))
        new_raw = raw[:4] + new_size + raw[8:] + list_chunk
        path.write_bytes(new_raw)
    except Exception as exc:  # noqa: BLE001
        log.debug("Could not add WAV comment: %s", exc)


# ── register ──────────────────────────────────────────────────────────────────

def register(mcp, client, workspace, cost_meter) -> None:  # noqa: ANN001
    @mcp.tool()
    async def synthesize_speech(
        text: str,
        language: str,
        voice: str | None = None,
        gender: Literal["female", "male"] = "female",
        style: str | None = None,
        output_path: str | None = None,
    ) -> dict:
        """Convert text to speech using Bodhan Indic-Speak. The language must \
be an ISO code (e.g. 'hi', 'ta', 'bn'). If voice is omitted, the first \
available voice for the requested gender is used. Use list_voices to see \
available voices and styles. Splits long text into sentence-level chunks and \
stitches the audio. Generated audio is labeled as AI-generated in file \
metadata. Returns UNSUPPORTED_LANGUAGE if the language is not supported \
(supported: as,bn,brx,doi,en,gu,hi,kn,kok,ks,mai,ml,mni,mr,ne,or,pa,sa,sat,\
sd,ta,te,ur). Cost: ₹6 per 10,000 input characters."""
        t0 = time.monotonic()
        try:
            # 1. Validate language
            if not is_supported(language, "speak"):
                return error_response(
                    ErrorCode.UNSUPPORTED_LANGUAGE,
                    f"Language '{language}' is not supported for TTS.",
                    f"Supported: {', '.join(sorted(SPEAK_LANGS))}.",
                )

            # 2. Resolve voice
            voice_used = voice or pick_voice(language, gender)
            if not voice_used:
                return error_response(
                    ErrorCode.INVALID_ARGUMENT,
                    f"No {gender} voice found for language '{language}'.",
                    "Use list_voices to check available voices.",
                )

            # 3. Cost check
            char_count = len(text)
            cost_inr = estimate_speak(char_count)
            if not cost_meter.check_budget(cost_inr, remaining=float("inf")):
                return error_response(
                    ErrorCode.BUDGET_EXCEEDED,
                    "Estimated cost exceeds the configured budget.",
                    "Raise BODHAN_BUDGET_INR or use shorter text.",
                )

            # 4. Split into sentence chunks
            chunks = _split_sentences(text)
            if not chunks:
                return error_response(
                    ErrorCode.INVALID_ARGUMENT,
                    "Text produced no speakable chunks after splitting.",
                    "Provide non-empty text.",
                )

            # 5. Determine final output path
            if output_path:
                try:
                    final_path = workspace.resolve(output_path)
                except Exception:  # noqa: BLE001
                    final_path = Path(output_path)
            else:
                final_path = workspace.artifact_path("speech.wav")

            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                chunk_paths: list[Path] = []

                for idx, chunk_text in enumerate(chunks):
                    audio_bytes: bytes = await client.speak(
                        chunk_text,
                        language=language,
                        voice=voice_used,
                        style=style,
                    )
                    cp = tmp / f"chunk_{idx:04d}.wav"
                    cp.write_bytes(audio_bytes)
                    chunk_paths.append(cp)

                # 6. Stitch
                duration_seconds = _stitch_wavs(chunk_paths, final_path)

            # 7. Add metadata comment
            _add_wav_comment(final_path, "AI-generated by Indic-Speak")

            # 8. Record cost
            cost_meter.add("speak", cost_inr)

            duration_ms = int((time.monotonic() - t0) * 1000)
            return success_response(
                {
                    "output_file": str(final_path),
                    "duration_seconds": round(duration_seconds, 2),
                    "voice_used": voice_used,
                    "char_count": char_count,
                },
                cost_inr=cost_inr,
                cached=False,
                duration_ms=duration_ms,
            )

        except BodhanError as exc:
            return exc.to_response()
        except Exception as exc:  # noqa: BLE001
            log.error("synthesize_speech error: %s", exc, exc_info=True)
            return error_response(
                ErrorCode.UPSTREAM_ERROR,
                f"Unexpected error: {exc}",
                "Check logs and retry.",
                retryable=True,
            )
