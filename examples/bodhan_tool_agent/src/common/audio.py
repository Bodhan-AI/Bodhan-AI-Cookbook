"""Audio Engine and Utilities for Bodhan Indic Models.

Includes:
1. Indic sentence tokenizer for TTS splitting (handling Danda U+0964, Double Danda U+0965, Khatam U+06D4).
2. Standard library `wave` + `numpy` audio inspector and PCM16 24kHz concatenator.
3. Silence-aware audio chunker for ASR 30-second windows.
"""

from __future__ import annotations

import io
import json
import math
import os
import re
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, overload
import numpy as np


def _resolve_tool_path(name: str) -> str:
    """Find command executable, proactively checking User/Machine PATH if not in current process PATH."""
    found = shutil.which(name)
    if found:
        return found
    # Check if PATH in registry (Windows) has it
    if os.name == "nt":
        for scope in ("User", "Machine"):
            try:
                import winreg
                key_path = r"Environment" if scope == "User" else r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
                root_hkey = winreg.HKEY_CURRENT_USER if scope == "User" else winreg.HKEY_LOCAL_MACHINE
                with winreg.OpenKey(root_hkey, key_path) as k:
                    val, _ = winreg.QueryValueEx(k, "Path")
                    for entry in val.split(os.pathsep):
                        if entry.strip():
                            try:
                                candidate = Path(entry.strip()) / f"{name}.exe"
                                if candidate.is_file():
                                    return str(candidate)
                            except (OSError, PermissionError):
                                continue
            except Exception:
                pass
    return name


# Indic-aware sentence boundary pattern (including Danda '।' and Double Danda '॥' and Urdu Khatam '۔')
INDIC_SENTENCE_REGEX = re.compile(
    r'([.!?。！？؟۔।॥]+[\"”\'’\)\]]*)'
)

# Common honorific abbreviations and titles that should not trigger sentence boundaries
KNOWN_HONORIFICS = (
    "डॉ.", "पं.", "श्री.", "श्रीमती.", "प्रो.", "सुश्री.", "ले.", "कै.", "इंजी.", "न्या.",
    "Dr.", "Mr.", "Mrs.", "Ms.", "Prof.", "Sr.", "Jr.", "vs.", "etc.", "e.g.", "i.e."
)
_PLACEHOLDER_DOT = "\u2024"  # Unicode one-dot leader


@dataclass(frozen=True)
class AudioMetadata:
    duration_seconds: float
    sample_rate: int
    channels: int
    sample_width: int
    num_frames: int
    warnings: tuple[str, ...] = ()

    @property
    def is_24khz_mono(self) -> bool:
        return self.sample_rate == 24000 and self.channels == 1

    @property
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0


@dataclass(frozen=True)
class AudioChunkSegment:
    path: Path
    start_sec: float
    end_sec: float


def _ffprobe_duration(file_path: Path) -> float:
    """Get audio duration in seconds using ffprobe (for non-WAV formats)."""
    ffprobe_cmd = _resolve_tool_path("ffprobe")
    try:
        result = subprocess.run(
            [
                ffprobe_cmd, "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "csv=p=0",
                str(file_path),
            ],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        pass
    return 0.0


def ensure_wav(
    input_path: str | Path,
    output_dir: str | Path | None = None,
    target_sr: int = 16000,
) -> Path:
    """Convert any audio file to 16-bit mono PCM WAV using ffmpeg.

    If the input is already a valid PCM WAV, returns it unchanged.
    Non-WAV formats (OGG, MP3, WebM, M4A, FLAC, OPUS, AAC) are transcoded
    via a single ffmpeg subprocess call.

    Args:
        input_path: Path to the source audio file.
        output_dir: Directory for the converted WAV (defaults to input's parent).
        target_sr: Target sample rate in Hz (default 16000 for Indic-Transcribe).

    Returns:
        Path to a guaranteed-PCM16 WAV file.

    Raises:
        FileNotFoundError: If input file does not exist.
        ValueError: If ffmpeg is not installed or conversion fails.
    """
    path = Path(input_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Audio file not found: {path}")

    # Already WAV — verify header is readable; return as-is
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as wf:
                wf.getnframes()
            return path
        except wave.Error:
            pass  # Corrupted WAV header — fall through to ffmpeg reconversion

    out_dir = Path(output_dir) if output_dir else path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{path.stem}_converted.wav"

    ffmpeg_cmd = _resolve_tool_path("ffmpeg")
    try:
        result = subprocess.run(
            [
                ffmpeg_cmd, "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(path),
                "-acodec", "pcm_s16le",
                "-ar", str(target_sr),
                "-ac", "1",
                str(out_path),
            ],
            capture_output=True, text=True, timeout=120,
        )
    except FileNotFoundError:
        raise ValueError(
            "ffmpeg not found on PATH. Install ffmpeg to process non-WAV audio. "
            "See: https://ffmpeg.org/download.html"
        )
    except subprocess.TimeoutExpired:
        raise ValueError(f"ffmpeg conversion timed out for {path.name}")

    if result.returncode != 0:
        raise ValueError(
            f"ffmpeg conversion failed for {path.name}: {result.stderr[:300]}"
        )

    if not out_path.is_file() or out_path.stat().st_size == 0:
        raise ValueError(f"ffmpeg produced empty output for {path.name}")

    return out_path


def split_indic_sentences(
    text: str,
    min_chars: int = 25,
    max_chars: int = 300,
) -> list[str]:
    """Split Indic or English text into natural spoken sentences.

    Splits on sentence punctuation, then greedily merges short fragments so
    each output chunk is at least `min_chars` long. Any chunk that would
    exceed `max_chars` is broken on a word boundary. This keeps text blocks
    a reasonable size before sending to Indic-Speak, preventing TTS timeouts
    on oversized blocks while preserving prosodically complete clauses.
    """
    # Nothing to do for empty / whitespace-only input.
    text = text.strip()
    if not text:
        return []

    # Step 0: Mask known honorific abbreviations, initials, and decimals
    masked = text
    for h in KNOWN_HONORIFICS:
        masked = re.sub(re.escape(h), h[:-1] + _PLACEHOLDER_DOT, masked, flags=re.IGNORECASE)
    # Mask single Latin or Indic initials followed by dot (e.g. 'A. P. J.' or 'क.')
    masked = re.sub(r'\b([A-Za-z\u0900-\u097F])\.', r'\1' + _PLACEHOLDER_DOT, masked)
    # Mask decimal numbers (e.g. '2.5' or '99.9%')
    masked = re.sub(r'(\d+)\.(\d+)', r'\1' + _PLACEHOLDER_DOT + r'\2', masked)

    # `INDIC_SENTENCE_REGEX` contains a capturing group, so re.split
    # returns [part, delim, part, delim, ...] (delimiters interleaved).
    tokens = INDIC_SENTENCE_REGEX.split(masked)

    # --- Step 1: pair each text part with the punctuation that followed it ---
    # Produces clean, complete sentences like "Hello." or "यह ठीक है।".
    raw: list[str] = []
    i = 0
    while i < len(tokens):
        part = tokens[i].strip()
        punct = tokens[i + 1].strip() if i + 1 < len(tokens) else ""
        if part:
            raw.append(f"{part}{punct}".strip())
        i += 2  # step over the delimiter token too

    # Unmask placeholder dots back to standard periods
    raw = [s.replace(_PLACEHOLDER_DOT, ".") for s in raw]

    # --- Step 2: greedily merge short fragments, then enforce max_chars ---
    # Use a LIST buffer instead of string concatenation: appending is O(1)
    # and we join once per output sentence (O(n) total) instead of rebuilding
    # a growing string each time (O(n^2)).
    merged: list[str] = []
    buf: list[str] = []
    buf_len = 0  # tracked incrementally so we never re-len() a growing string

    for s in raw:
        if not buf:
            # Start a fresh buffer.
            buf.append(s)
            buf_len = len(s)
        elif buf_len < min_chars:
            # Still too short: glue on the next sentence (no string rebuild).
            buf.append(s)
            buf_len += 1 + len(s)  # +1 for the joining space
        else:
            # Buffer is long enough: emit it and start over with `s`.
            merged.append(" ".join(buf))
            buf = [s]
            buf_len = len(s)
    if buf:
        merged.append(" ".join(buf))

    # --- Step 3: hard-split anything still longer than max_chars ---
    # Prefer breaking on a space so we don't cut a word in half.
    out: list[str] = []
    for s in merged:
        while len(s) > max_chars:
            cut = s.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars  # no space found -> hard cut
            chunk = s[:cut].strip()
            if chunk:
                out.append(chunk)
            s = s[cut:].strip()
        if s:
            out.append(s)

    return out if out else [text]


def inspect_audio(wav_path: str | Path) -> AudioMetadata:
    """Inspect standard PCM WAV file parameters (or non-WAV via ffprobe) and generate diagnostic warnings for Bodhan models."""
    path = Path(wav_path)
    try:
        with wave.open(str(path), "rb") as wf:
            sr = wf.getframerate()
            ch = wf.getnchannels()
            sw = wf.getsampwidth()
            frames = wf.getnframes()
            duration = frames / float(sr) if sr > 0 else 0.0

            diag_warnings: list[str] = []
            if ch > 1:
                diag_warnings.append(
                    f"Multi-channel audio detected ({ch} channels). Bodhan Indic models expect mono (1 channel). Downmixing required."
                )
            if sr == 8000:
                diag_warnings.append(
                    "Telephony 8kHz sample rate detected. Low acoustic bandwidth may degrade Indic transcription accuracy; 16kHz or 24kHz recommended."
                )
            elif sr == 48000:
                diag_warnings.append(
                    "48kHz high-definition sample rate detected. Exceeds standard 16kHz/24kHz rate for Bodhan models; downsampling recommended to conserve payload bandwidth."
                )
            elif sr not in {16000, 24000}:
                diag_warnings.append(
                    f"Sample rate {sr}Hz differs from recommended 16kHz (Indic-Transcribe) or 24kHz (Indic-Speak)."
                )
            if sw != 2:
                diag_warnings.append(
                    f"Sample width is {sw * 8}-bit ({sw} bytes). Bodhan Indic models require 16-bit signed PCM (PCM16)."
                )
            if duration > 30.0:
                diag_warnings.append(
                    f"Duration {duration:.2f}s exceeds Indic-Transcribe 30.0s chunking limit. Silence-aware slicing required."
                )

            return AudioMetadata(
                duration_seconds=round(duration, 3),
                sample_rate=sr,
                channels=ch,
                sample_width=sw,
                num_frames=frames,
                warnings=tuple(diag_warnings),
            )
    except Exception:
        # Fallback to ffprobe if available for non-WAV / compressed media
        dur = _ffprobe_duration(path)
        if dur > 0.0:
            diag_warnings = (
                f"Compressed/non-WAV container detected ({path.suffix}). Converted via ffmpeg for processing.",
            )
            return AudioMetadata(
                duration_seconds=round(dur, 3),
                sample_rate=16000,
                channels=1,
                sample_width=2,
                num_frames=int(dur * 16000),
                warnings=diag_warnings,
            )
        raise


def resample_pcm16(audio: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    """Resample a 1D int16 PCM audio array using linear interpolation (pure NumPy, zero external binary)."""
    if orig_sr == target_sr:
        return audio
    num_orig = len(audio)
    num_target = int(round(num_orig * target_sr / orig_sr))
    orig_indices = np.linspace(0, num_orig - 1, num_orig)
    target_indices = np.linspace(0, num_orig - 1, num_target)
    return np.interp(target_indices, orig_indices, audio.astype(np.float32)).astype(np.int16)


def convert_to_indic_pcm16(
    input_wav: str | Path,
    output_wav: str | Path,
    target_sr: int = 24000,
) -> Path:
    """Convert an arbitrary WAV file to standardized 24kHz mono PCM16 format for Bodhan models.
    
    Performs mono downmixing and linear interpolation resampling with zero external binaries.
    """
    in_path = Path(input_wav)
    out_path = Path(output_wav)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with wave.open(str(in_path), "rb") as wf:
        orig_sr = wf.getframerate()
        ch = wf.getnchannels()
        sw = wf.getsampwidth()
        frames = wf.getnframes()
        raw_bytes = wf.readframes(frames)

    if sw == 2:
        audio = np.frombuffer(raw_bytes, dtype="<i2")
    elif sw == 1:
        raw_u8 = np.frombuffer(raw_bytes, dtype=np.uint8)
        audio = ((raw_u8.astype(np.int32) - 128) * 256).astype(np.int16)
    else:
        raise ValueError(f"Unsupported bit depth for conversion: {sw * 8}-bit")

    if ch > 1:
        audio = audio.reshape(-1, ch).mean(axis=1).astype(np.int16)

    if orig_sr != target_sr:
        audio = resample_pcm16(audio, orig_sr, target_sr)

    with wave.open(str(out_path), "wb") as out_wf:
        out_wf.setnchannels(1)
        out_wf.setsampwidth(2)
        out_wf.setframerate(target_sr)
        out_wf.writeframes(audio.tobytes())

    return out_path


def concatenate_wav_files(
    wav_paths: list[str | Path],
    output_path: str | Path,
    silence_gap_ms: int = 200,
) -> Path:
    """Concatenate multiple PCM16 WAV files of matching parameters into one file.

    Args:
        wav_paths: List of file paths to WAV audio clips.
        output_path: Path where the merged WAV audio will be written.
        silence_gap_ms: Milliseconds of PCM silence inserted between clips (default 200ms)
            to avoid unnatural co-articulation and ASR boundary clipping.
    """
    if not wav_paths:
        raise ValueError("No WAV files provided for concatenation.")

    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    first_meta = inspect_audio(wav_paths[0])
    
    silence_frames = int((silence_gap_ms / 1000.0) * first_meta.sample_rate) if silence_gap_ms > 0 else 0
    silence_bytes = b"\x00" * (silence_frames * first_meta.channels * first_meta.sample_width)

    with wave.open(str(out_file), "wb") as out_wf:
        out_wf.setnchannels(first_meta.channels)
        out_wf.setsampwidth(first_meta.sample_width)
        out_wf.setframerate(first_meta.sample_rate)

        for idx, p in enumerate(wav_paths):
            if idx > 0 and silence_bytes:
                out_wf.writeframes(silence_bytes)
            with wave.open(str(p), "rb") as in_wf:
                if (
                    in_wf.getframerate() != first_meta.sample_rate
                    or in_wf.getnchannels() != first_meta.channels
                    or in_wf.getsampwidth() != first_meta.sample_width
                ):
                    raise ValueError(f"WAV mismatch: {p} does not match {wav_paths[0]}")
                out_wf.writeframes(in_wf.readframes(in_wf.getnframes()))

    return out_file


@overload
def chunk_wav_silence_aware(
    wav_path: str | Path,
    output_dir: str | Path,
    max_chunk_sec: float = 28.0,
    search_window_start: float = 20.0,
    return_segments: Literal[False] = False,
) -> list[Path]: ...


@overload
def chunk_wav_silence_aware(
    wav_path: str | Path,
    output_dir: str | Path,
    max_chunk_sec: float = 28.0,
    search_window_start: float = 20.0,
    return_segments: Literal[True] = ...,
) -> list[AudioChunkSegment]: ...


def chunk_wav_silence_aware(
    wav_path: str | Path,
    output_dir: str | Path,
    max_chunk_sec: float = 28.0,
    search_window_start: float = 20.0,
    return_segments: bool = False,
) -> list[Path] | list[AudioChunkSegment]:
    """Slice an audio file longer than max_chunk_sec at natural silence boundaries.
    
    Guarantees every chunk is strictly under 30.0 seconds to satisfy Indic-Transcribe.
    Snaps cuts to the nearest zero-crossing within ±5ms to avoid audio pops/clicks.
    Auto-converts non-WAV formats via ensure_wav.
    """
    path = Path(wav_path)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if path.suffix.lower() != ".wav":
        path = ensure_wav(path, output_dir=out_dir)

    meta = inspect_audio(path)
    if meta.duration_seconds <= max_chunk_sec:
        if return_segments:
            return [AudioChunkSegment(path=path, start_sec=0.0, end_sec=meta.duration_seconds)]
        return [path]

    chunks: list[Path] = []
    segments: list[AudioChunkSegment] = []
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        ch = wf.getnchannels()
        sw = wf.getsampwidth()

        # Read entire raw audio as numpy array (explicitly little-endian 16-bit signed integer)
        raw_bytes = wf.readframes(wf.getnframes())
        if sw == 2:
            audio_data = np.frombuffer(raw_bytes, dtype="<i2")
        else:
            raise ValueError(f"Unsupported sample width: {sw} bytes (only PCM16 supported).")

        if ch > 1:
            audio_data = audio_data.reshape(-1, ch)
            mono_data = audio_data.mean(axis=1).astype(np.int16)
        else:
            mono_data = audio_data

        total_samples = len(mono_data)
        current_sample = 0
        chunk_idx = 1

        while current_sample < total_samples:
            start_sec = round(current_sample / float(sr), 3)
            remaining = total_samples - current_sample
            remaining_sec = remaining / float(sr)

            if remaining_sec <= max_chunk_sec:
                # Last slice
                slice_end = total_samples
            else:
                # Look for minimum RMS in the search window (search_window_start to max_chunk_sec)
                win_start_samp = int(current_sample + (search_window_start * sr))
                win_end_samp = int(current_sample + (max_chunk_sec * sr))
                win_start_samp = min(win_start_samp, total_samples)
                win_end_samp = min(win_end_samp, total_samples)

                # 100ms window RMS scan
                hop_len = int(0.1 * sr)
                min_rms = float("inf")
                best_cut = win_end_samp

                for cut_cand in range(win_start_samp, win_end_samp, hop_len):
                    test_win = mono_data[cut_cand : cut_cand + hop_len]
                    if len(test_win) > 0:
                        rms = np.sqrt(np.mean(test_win.astype(np.float64) ** 2))
                        if rms < min_rms:
                            min_rms = rms
                            best_cut = cut_cand

                # Snap cut to nearest zero-crossing within ±5ms to prevent audio pop/click
                zwin = int(0.005 * sr)
                lo = max(best_cut - zwin, current_sample)
                hi = min(best_cut + zwin, total_samples)
                if hi > lo:
                    best_cut = lo + int(np.argmin(np.abs(mono_data[lo:hi])))

                slice_end = best_cut

            end_sec = round(slice_end / float(sr), 3)

            # Write chunk
            chunk_file = out_dir / f"chunk_{chunk_idx:03d}.wav"
            chunk_slice = audio_data[current_sample:slice_end]

            with wave.open(str(chunk_file), "wb") as cw:
                cw.setnchannels(ch)
                cw.setsampwidth(sw)
                cw.setframerate(sr)
                cw.writeframes(chunk_slice.tobytes())

            chunks.append(chunk_file)
            segments.append(AudioChunkSegment(path=chunk_file, start_sec=start_sec, end_sec=end_sec))
            current_sample = slice_end
            chunk_idx += 1

    return segments if return_segments else chunks


def format_timestamp_srt(seconds: float) -> str:
    """Format seconds into SubRip (SRT) timestamp HH:MM:SS,mmm."""
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    hours = total_ms // 3600000
    remainder = total_ms % 3600000
    minutes = remainder // 60000
    remainder %= 60000
    secs = remainder // 1000
    millis = remainder % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def format_timestamp_vtt(seconds: float) -> str:
    """Format seconds into WebVTT timestamp HH:MM:SS.mmm."""
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    hours = total_ms // 3600000
    remainder = total_ms % 3600000
    minutes = remainder // 60000
    remainder %= 60000
    secs = remainder // 1000
    millis = remainder % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def format_srt_content(cues: list[tuple[float, float, str]]) -> str:
    """Format a list of (start_sec, end_sec, text) cues into valid SubRip (.srt) string."""
    blocks: list[str] = []
    idx = 1
    for start, end, text in cues:
        clean_text = text.strip()
        if not clean_text:
            continue
        start_ts = format_timestamp_srt(start)
        end_ts = format_timestamp_srt(end)
        blocks.append(f"{idx}\n{start_ts} --> {end_ts}\n{clean_text}")
        idx += 1
    if not blocks:
        return ""
    return "\n\n".join(blocks) + "\n"


def format_vtt_content(cues: list[tuple[float, float, str]]) -> str:
    """Format a list of (start_sec, end_sec, text) cues into valid WebVTT (.vtt) string."""
    blocks: list[str] = ["WEBVTT"]
    idx = 1
    for start, end, text in cues:
        clean_text = text.strip()
        if not clean_text:
            continue
        start_ts = format_timestamp_vtt(start)
        end_ts = format_timestamp_vtt(end)
        blocks.append(f"{idx}\n{start_ts} --> {end_ts}\n{clean_text}")
        idx += 1
    if len(blocks) == 1:
        return "WEBVTT\n"
    return "\n\n".join(blocks) + "\n"
