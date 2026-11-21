"""Deterministic media helpers: ffmpeg/ffprobe, silence-aware windowing, PDF rasterization, Pillow ops."""

from __future__ import annotations

import contextlib
import json
import math
import mimetypes
import re
import struct
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
from PIL import Image

from bodhan_mcp.schemas import FileKind

AUDIO_EXTS = {".wav", ".mp3", ".flac", ".ogg", ".opus", ".m4a", ".aac", ".amr", ".wma", ".webm"}
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".m4v", ".mpeg", ".mpg", ".3gp"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif"}
TEXT_EXTS = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".srt", ".vtt"}

SILENCE_START_RE = re.compile(r"silence_start:\s*([0-9]+(?:\.[0-9]+)?)")
SILENCE_END_RE = re.compile(r"silence_end:\s*([0-9]+(?:\.[0-9]+)?)")


class MediaError(RuntimeError):
    """Raised when ffmpeg/ffprobe or media parsing fails."""


@dataclass(frozen=True)
class AudioWindow:
    index: int
    start_s: float
    end_s: float
    is_pure_silence: bool = False

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_s - self.start_s)


def detect_file_kind(path: Path) -> tuple[FileKind, str | None]:
    """Detect file kind from magic bytes first, then extension."""
    if not path.exists() or not path.is_file():
        return "missing", None
    try:
        header = path.read_bytes()[:32]
    except OSError:
        return "unknown", None

    ext = path.suffix.lower()
    mime = mimetypes.guess_type(str(path))[0]

    if header.startswith(b"%PDF-"):
        return "pdf", "application/pdf"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image", "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image", "image/jpeg"
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image", "image/webp"
    if header[:2] in (b"BM", b"II", b"MM") and ext in IMAGE_EXTS:
        return "image", mime or "image/bmp"
    if header[:4] == b"RIFF" and header[8:12] == b"WAVE":
        return "audio", "audio/wav"
    if header[:4] == b"fLaC":
        return "audio", "audio/flac"
    if header[:4] == b"OggS":
        return "audio", "audio/ogg"
    if header[:3] == b"ID3" or (len(header) >= 2 and header[0] == 0xFF and (header[1] & 0xE0) == 0xE0):
        return "audio", "audio/mpeg"
    if len(header) >= 12 and header[4:8] == b"ftyp":
        if ext in {".m4a", ".aac", ".3gp"}:
            return "audio", "audio/mp4"
        return "video", "video/mp4"
    if header.startswith(b"\x1a\x45\xdf\xa3"):
        if ext == ".webm" and "audio" in (mime or ""):
            return "audio", "audio/webm"
        return ("video" if ext in VIDEO_EXTS else "audio"), mime or "video/webm"

    if ext == ".pdf":
        return "pdf", "application/pdf"
    if ext in IMAGE_EXTS:
        return "image", mime or "image/png"
    if ext in VIDEO_EXTS:
        return "video", mime or "video/mp4"
    if ext in AUDIO_EXTS:
        return "audio", mime or "audio/wav"
    if ext in TEXT_EXTS:
        return "text", mime or "text/plain"

    # Fallback: check if file is valid UTF-8 text without null bytes.
    if b"\x00" not in header:
        try:
            path.read_bytes()[:4096].decode("utf-8")
            return "text", "text/plain"
        except UnicodeDecodeError:
            pass
    return "unknown", mime


def _run_cmd(cmd: list[str], *, timeout_s: float = 120.0) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except FileNotFoundError as exc:
        raise MediaError(f"Required system binary not found: {cmd[0]}. Install ffmpeg/ffprobe.") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"Command timed out after {timeout_s}s: {' '.join(cmd[:4])}") from exc
    if proc.returncode != 0:
        err_tail = (proc.stderr or proc.stdout or "").strip()[-400:]
        raise MediaError(f"{cmd[0]} exited with {proc.returncode}: {err_tail}")
    return proc


def ffprobe_info(path: Path) -> dict[str, Any]:
    """Inspect an audio or video file with ffprobe."""
    proc = _run_cmd(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        timeout_s=30.0,
    )
    info = json.loads(proc.stdout or "{}")
    fmt = info.get("format") or {}
    streams = info.get("streams") or []
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    raw_dur = fmt.get("duration") or (audio_stream or {}).get("duration") or (video_stream or {}).get("duration")
    duration_s = float(raw_dur) if raw_dur is not None else 0.0
    sample_rate = int(audio_stream["sample_rate"]) if audio_stream and audio_stream.get("sample_rate") else None
    channels = int(audio_stream["channels"]) if audio_stream and audio_stream.get("channels") else None
    return {
        "duration_s": round(duration_s, 3),
        "sample_rate": sample_rate,
        "channels": channels,
        "has_audio": audio_stream is not None,
        "has_video": video_stream is not None,
        "format_name": fmt.get("format_name"),
    }


def convert_to_wav_16k_mono(input_path: Path, output_path: Path) -> Path:
    """Convert any ffmpeg-supported audio/video file into 16 kHz mono PCM16 WAV."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run_cmd(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(output_path),
        ],
        timeout_s=180.0,
    )
    return output_path


def wav_duration_s(wav_path: Path) -> float:
    with contextlib.closing(wave.open(str(wav_path), "rb")) as wf:
        rate = wf.getframerate()
        return wf.getnframes() / float(rate) if rate else 0.0


def detect_silences(
    wav_path: Path,
    *,
    noise_db: float = -35.0,
    min_silence_s: float = 0.35,
) -> list[tuple[float, float]]:
    """Run ffmpeg silencedetect and return sorted (start_s, end_s) silence intervals."""
    proc = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(wav_path),
            "-af",
            f"silencedetect=noise={noise_db}dB:d={min_silence_s}",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=120.0,
        check=False,
    )
    stderr = proc.stderr or ""
    duration = wav_duration_s(wav_path)
    silences: list[tuple[float, float]] = []
    cur_start: float | None = None
    for line in stderr.splitlines():
        m_start = SILENCE_START_RE.search(line)
        if m_start:
            cur_start = float(m_start.group(1))
            continue
        m_end = SILENCE_END_RE.search(line)
        if m_end:
            end_val = float(m_end.group(1))
            start_val = cur_start if cur_start is not None else 0.0
            if end_val > start_val:
                silences.append((start_val, min(end_val, duration)))
            cur_start = None
    if cur_start is not None and duration > cur_start:
        silences.append((cur_start, duration))
    return silences


def compute_audio_windows(
    duration_s: float,
    silences: list[tuple[float, float]],
    *,
    max_window_s: float = 28.0,
    min_window_s: float = 0.5,
) -> list[AudioWindow]:
    """Split [0, duration_s] into windows <= max_window_s, preferring silence midpoints.

    Pure-silence windows are flagged so callers can drop them before uploading.
    """
    if duration_s <= 0:
        return []

    def is_covered_by_silence(start: float, end: float) -> bool:
        if end <= start:
            return True
        covered = 0.0
        for s_start, s_end in silences:
            ov_start = max(start, s_start)
            ov_end = min(end, s_end)
            if ov_end > ov_start:
                covered += ov_end - ov_start
        return (covered / (end - start)) >= 0.96

    if duration_s <= max_window_s:
        return [
            AudioWindow(
                index=0,
                start_s=0.0,
                end_s=round(duration_s, 3),
                is_pure_silence=is_covered_by_silence(0.0, duration_s),
            )
        ]

    # Candidate split points: midpoints of silence intervals.
    split_points = sorted((s + e) / 2.0 for s, e in silences if (e - s) >= 0.15)
    windows: list[AudioWindow] = []
    cursor = 0.0
    idx = 0
    eps = 1e-3

    while cursor < duration_s - eps:
        remaining = duration_s - cursor
        if remaining <= max_window_s:
            end = duration_s
        else:
            limit = cursor + max_window_s
            min_cut = cursor + min(max_window_s * 0.35, 6.0)
            candidates = [pt for pt in split_points if min_cut <= pt <= limit]
            if not candidates:
                candidates = [pt for pt in split_points if cursor + min_window_s <= pt <= limit]
            end = candidates[-1] if candidates else limit

        if end <= cursor + eps:
            end = min(duration_s, cursor + max_window_s)
        windows.append(
            AudioWindow(
                index=idx,
                start_s=round(cursor, 3),
                end_s=round(end, 3),
                is_pure_silence=is_covered_by_silence(cursor, end),
            )
        )
        idx += 1
        cursor = end

    return windows


def slice_wav(wav_path: Path, start_s: float, end_s: float, out_path: Path) -> tuple[Path, float]:
    """Extract [start_s, end_s] from a PCM16 WAV file and return (out_path, rms)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.closing(wave.open(str(wav_path), "rb")) as wf:
        params = wf.getparams()
        rate = params.framerate
        start_frame = max(0, int(start_s * rate))
        end_frame = min(params.nframes, int(math.ceil(end_s * rate)))
        wf.setpos(start_frame)
        frames = wf.readframes(max(0, end_frame - start_frame))

    with contextlib.closing(wave.open(str(out_path), "wb")) as out_wf:
        out_wf.setparams(params)
        out_wf.writeframes(frames)

    rms = pcm16_rms(frames)
    return out_path, rms


def pcm16_rms(frames: bytes) -> float:
    """Compute root-mean-square amplitude of PCM16 little-endian audio bytes."""
    n_samples = len(frames) // 2
    if n_samples == 0:
        return 0.0
    samples = struct.unpack(f"<{n_samples}h", frames[: n_samples * 2])
    mean_sq = sum(s * s for s in samples) / n_samples
    return math.sqrt(mean_sq)


def extract_probe_wav(input_path: Path, out_wav: Path, *, max_seconds: float = 10.0) -> tuple[Path, float]:
    """Convert input media and extract up to `max_seconds` of non-silent audio for language probing."""
    full_wav = out_wav.with_name(f"{out_wav.stem}_full.wav")
    convert_to_wav_16k_mono(input_path, full_wav)
    dur = wav_duration_s(full_wav)
    silences = detect_silences(full_wav)
    # Find first non-silent start point.
    start_s = 0.0
    for s_start, s_end in silences:
        if s_start <= start_s + 0.1 and s_end > start_s:
            start_s = s_end
        else:
            break
    if start_s >= dur - 0.5:
        start_s = 0.0
    end_s = min(dur, start_s + max_seconds)
    slice_wav(full_wav, start_s, end_s, out_wav)
    with contextlib.suppress(OSError):
        full_wav.unlink()
    return out_wav, round(end_s - start_s, 3)


def concat_and_encode_audio(
    wav_paths: list[Path],
    out_path: Path,
    *,
    fmt: str = "mp3",
    normalize: bool = True,
    ai_comment: str = "AI-generated (Bodhan Indic-Speak)",
) -> tuple[Path, float]:
    """Concatenate WAV chunks, optionally loudness-normalize, encode to mp3/wav, and write AI-generated tag."""
    if not wav_paths:
        raise MediaError("No WAV chunks provided for concatenation.")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    concat_wav = out_path.with_name(f".{out_path.stem}_concat.wav")

    # Join WAV frames cleanly via ffmpeg concat demuxer (handles any sample-rate variance safely).
    list_file = out_path.with_name(f".{out_path.stem}_concat.txt")
    lines = [f"file '{p.resolve().as_posix()}'" for p in wav_paths]
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    try:
        _run_cmd(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_file),
                "-c:a",
                "pcm_s16le",
                "-ar",
                "24000",
                "-ac",
                "1",
                str(concat_wav),
            ],
            timeout_s=120.0,
        )

        codec_args = ["-c:a", "libmp3lame", "-b:a", "128k"] if fmt == "mp3" else ["-c:a", "pcm_s16le"]
        meta_args = [
            "-metadata",
            f"comment={ai_comment}",
            "-metadata",
            "artist=Bodhan Indic-Speak (AI-generated)",
        ]
        if fmt == "mp3":
            meta_args += ["-id3v2_version", "3"]

        if normalize:
            try:
                _run_cmd(
                    [
                        "ffmpeg",
                        "-y",
                        "-i",
                        str(concat_wav),
                        "-af",
                        "loudnorm=I=-16:TP=-1.5:LRA=11",
                        *codec_args,
                        *meta_args,
                        str(out_path),
                    ],
                    timeout_s=120.0,
                )
            except MediaError:
                # Fallback without loudnorm if clip is ultra-short or silent.
                _run_cmd(
                    ["ffmpeg", "-y", "-i", str(concat_wav), *codec_args, *meta_args, str(out_path)],
                    timeout_s=120.0,
                )
        else:
            _run_cmd(
                ["ffmpeg", "-y", "-i", str(concat_wav), *codec_args, *meta_args, str(out_path)],
                timeout_s=120.0,
            )

        info = ffprobe_info(out_path)
        return out_path, info["duration_s"]
    finally:
        for tmp in (list_file, concat_wav):
            with contextlib.suppress(OSError):
                tmp.unlink()


def pdf_page_count(path: Path) -> int:
    """Count pages in a PDF using pypdfium2 (no poppler required)."""
    doc = pdfium.PdfDocument(str(path))
    try:
        return len(doc)
    finally:
        doc.close()


def parse_page_spec(spec: str | None, total_pages: int) -> list[int]:
    """Parse 1-based page specification like '1-3,5' into sorted 1-based page numbers."""
    if not spec or spec.strip().lower() == "all":
        return list(range(1, total_pages + 1))
    result: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo_s, hi_s = part.split("-", 1)
            lo, hi = int(lo_s.strip()), int(hi_s.strip())
            if lo < 1 or hi > total_pages or lo > hi:
                raise ValueError(f"Page range '{part}' out of bounds for {total_pages}-page PDF.")
            result.update(range(lo, hi + 1))
        else:
            num = int(part)
            if num < 1 or num > total_pages:
                raise ValueError(f"Page {num} out of bounds for {total_pages}-page PDF.")
            result.add(num)
    if not result:
        raise ValueError(f"Empty page selection from '{spec}'.")
    return sorted(result)


def render_pdf_pages(
    pdf_path: Path,
    out_dir: Path,
    *,
    pages: list[int] | None = None,
    dpi: int = 175,
) -> list[tuple[int, Path]]:
    """Rasterize selected 1-based PDF pages to PNG files via pypdfium2."""
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        total = len(doc)
        selected = pages if pages is not None else list(range(1, total + 1))
        scale = dpi / 72.0
        rendered: list[tuple[int, Path]] = []
        for page_num in selected:
            page = doc[page_num - 1]
            bitmap = page.render(scale=scale)
            pil_img = bitmap.to_pil()
            out_file = out_dir / f"{pdf_path.stem}_page_{page_num:03d}.png"
            pil_img.save(out_file, format="PNG")
            rendered.append((page_num, out_file))
        return rendered
    finally:
        doc.close()


def image_info(path: Path) -> tuple[int, int]:
    with Image.open(path) as img:
        return img.size


def downscale_image(path: Path, out_path: Path, *, scale: float = 0.65) -> Path:
    """Downscale an image to reduce OCR token density on 502 overflow."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(path) as img:
        w, h = img.size
        new_size = (max(64, int(w * scale)), max(64, int(h * scale)))
        resized = img.convert("RGB").resize(new_size, Image.Resampling.LANCZOS)
        resized.save(out_path, format="PNG")
    return out_path


def split_image_vertical(path: Path, top_path: Path, bottom_path: Path, *, overlap_px: int = 24) -> tuple[Path, Path]:
    """Split a tall page image into top and bottom halves with small overlap for OCR 502 recovery."""
    top_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(path) as img:
        rgb = img.convert("RGB")
        w, h = rgb.size
        mid = h // 2
        top = rgb.crop((0, 0, w, min(h, mid + overlap_px)))
        bottom = rgb.crop((0, max(0, mid - overlap_px), w, h))
        top.save(top_path, format="PNG")
        bottom.save(bottom_path, format="PNG")
    return top_path, bottom_path
