"""Deterministic media and document helpers. No ASR/OCR/translation by non-Bodhan models."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from .errors import TaskError


def require_binary(name: str) -> str:
    binary = shutil.which(name)
    if not binary:
        raise TaskError("DEPENDENCY_MISSING", f"Install {name} and add it to PATH")
    return binary


def command(args: list[str], timeout: int = 120) -> None:
    try:
        subprocess.run(args, capture_output=True, text=True, check=True, timeout=timeout)
    except subprocess.CalledProcessError as exc:
        raise TaskError("MEDIA_PROCESSING_FAILED", exc.stderr[-700:]) from exc
    except subprocess.TimeoutExpired as exc:
        raise TaskError("MEDIA_TIMEOUT", "Audio or document conversion timed out") from exc


def duration(path: Path) -> float:
    require_binary("ffprobe")
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, check=True, timeout=30,
        )
        result = float(json.loads(proc.stdout)["format"]["duration"])
        if result <= 0:
            raise ValueError("zero duration")
        return result
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        raise TaskError("BAD_AUDIO", f"Cannot inspect media duration: {path.name}") from exc


def to_pcm_wav(path: Path, dest: Path) -> Path:
    """Normalizes audio/video with ffmpeg; it does not transcribe or infer speech."""
    require_binary("ffmpeg")
    dest.parent.mkdir(parents=True, exist_ok=True)
    command(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(path),
             "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dest)], timeout=600)
    return dest


def audio_chunks(path: Path, out_dir: Path, seconds: float = 25.0) -> list[dict]:
    """Split on <=25s windows; timestamps are truthful window boundaries, not word alignments."""
    if seconds <= 0 or seconds > 28:
        raise TaskError("INVALID_CHUNK_SIZE", "Audio chunks must be at most 28 seconds")
    norm = to_pcm_wav(path, out_dir / "normalized.wav")
    total = duration(norm)
    if total > 7200:
        raise TaskError("INPUT_TOO_LONG", "Audio may not exceed two hours")
    result = []
    for i in range(int((total - 1e-8) // seconds) + 1):
        start = round(i * seconds, 3)
        if start >= total:
            break
        length = min(seconds, total - start)
        target = out_dir / f"chunk_{i:04d}.wav"
        command(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(start),
                 "-t", str(length), "-i", str(norm), "-c:a", "pcm_s16le", str(target)])
        result.append({"path": str(target.resolve()), "start": start,
                       "end": round(start + length, 3), "seconds": length})
    return result


def render_pdf(path: Path, out_dir: Path, limit: int = 100) -> list[Path]:
    try:
        import fitz
    except ImportError as exc:
        raise TaskError("DEPENDENCY_MISSING", "Install PyMuPDF") from exc
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        doc = fitz.open(str(path))
        if not 0 < len(doc) <= limit:
            raise TaskError("INVALID_PDF_PAGES", f"PDF must have 1–{limit} pages")
        pages = []
        for idx, page in enumerate(doc):
            target = out_dir / f"page_{idx + 1:03d}.png"
            # Image render only. NEVER use page.get_text(), even when a text layer exists.
            page.get_pixmap(matrix=fitz.Matrix(1.6, 1.6), alpha=False).save(str(target))
            pages.append(target)
        doc.close()
        return pages
    except TaskError:
        raise
    except Exception as exc:
        raise TaskError("BAD_DOCUMENT", "Cannot render the supplied PDF") from exc


def split_speech(text: str, max_chars: int = 210) -> list[str]:
    """Sentences first, with spaces/words as the only fallback; never silently omit characters."""
    if max_chars < 15:
        raise ValueError("max_chars too small")
    sentences = re.split(r"(?<=[.!?।॥])\s+|\n+", text.strip())
    result: list[str] = []
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        words = sentence.split()
        chunk = ""
        for word in words:
            if len(word) > max_chars:
                if chunk:
                    result.append(chunk)
                    chunk = ""
                result.extend(word[i:i + max_chars] for i in range(0, len(word), max_chars))
                continue
            candidate = f"{chunk} {word}".strip()
            if len(candidate) > max_chars:
                result.append(chunk)
                chunk = word
            else:
                chunk = candidate
        if chunk:
            result.append(chunk)
    return result


def combine_wavs(parts: list[Path], out_path: Path) -> None:
    if not parts:
        raise TaskError("EMPTY_AUDIO", "No generated audio available to combine")
    require_binary("ffmpeg")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Canonicalize every part before concat to avoid sample-rate/format mismatches.
    list_file = out_path.parent / "concat_list.txt"
    canonical = []
    for i, p in enumerate(parts):
        wav = out_path.parent / f"canon_{i:04d}.wav"
        command(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(p),
                 "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)])
        canonical.append(wav)
    list_file.write_text("".join(f"file '{p.name}'\n" for p in canonical), encoding="utf-8")
    command(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
             "-i", str(list_file), "-c:a", "pcm_s16le", str(out_path)])
    for temp in canonical + [list_file]:
        temp.unlink(missing_ok=True)


def format_srt_time(seconds: float) -> str:
    ms = max(0, int(round(seconds * 1000)))
    hours, rest = divmod(ms, 3_600_000)
    mins, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02}:{mins:02}:{secs:02},{millis:03}"


def make_srt(segments: list[dict]) -> str:
    lines = []
    for idx, seg in enumerate(segments, 1):
        start, end = float(seg["start"]), float(seg["end"])
        if end <= start:
            raise TaskError("INVALID_SEGMENT", "Subtitle end must be after start")
        text = str(seg.get("text", "")).strip()
        if text:
            lines.append(f"{idx}\n{format_srt_time(start)} --> {format_srt_time(end)}\n{text}\n")
    return "\n".join(lines) + "\n"
