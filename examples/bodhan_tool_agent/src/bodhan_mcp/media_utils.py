from __future__ import annotations

import mimetypes
import os
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
from PIL import Image

MAX_AUDIO_CHUNK_SECONDS = 28.0
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".ogg", ".flac", ".aac", ".webm"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
PDF_EXTENSIONS = {".pdf"}
TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".srt", ".vtt"}


def get_ffmpeg_bin() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg  # type: ignore

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def get_ffprobe_bin() -> str | None:
    return shutil.which("ffprobe")


def validate_existing_file(file_path: str | Path) -> Path:
    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if not path.is_file():
        raise ValueError(f"Path is not a regular file: {path}")
    if path.stat().st_size == 0:
        raise ValueError(f"File is empty (0 bytes): {path}")
    return path


def get_audio_duration_seconds(audio_path: Path) -> float:
    ffprobe = get_ffprobe_bin()
    if ffprobe:
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(audio_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            try:
                return float(proc.stdout.strip())
            except ValueError:
                pass

    ffmpeg = get_ffmpeg_bin()
    if ffmpeg:
        proc = subprocess.run(
            [ffmpeg, "-i", str(audio_path), "-f", "null", "-"],
            capture_output=True,
            text=True,
            check=False,
        )
        for line in proc.stderr.splitlines():
            if "Duration:" in line:
                try:
                    raw = line.split("Duration:")[1].split(",")[0].strip()
                    hh, mm, ss = raw.split(":")
                    return int(hh) * 3600 + int(mm) * 60 + float(ss)
                except Exception:
                    pass

    if audio_path.suffix.lower() == ".wav":
        with wave.open(str(audio_path), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            if rate > 0:
                return frames / float(rate)

    raise RuntimeError(f"Unable to determine duration for audio file: {audio_path}")


def inspect_media_file(file_path: str | Path) -> dict[str, Any]:
    path = validate_existing_file(file_path)
    ext = path.suffix.lower()
    mime, _ = mimetypes.guess_type(str(path))
    size_bytes = path.stat().st_size

    info: dict[str, Any] = {
        "path": str(path),
        "filename": path.name,
        "extension": ext,
        "mime_type": mime or "application/octet-stream",
        "size_bytes": size_bytes,
    }

    if ext in AUDIO_EXTENSIONS:
        info["kind"] = "audio"
        duration = get_audio_duration_seconds(path)
        info["duration_seconds"] = round(duration, 3)
        info["needs_chunking"] = duration > MAX_AUDIO_CHUNK_SECONDS
    elif ext in PDF_EXTENSIONS:
        info["kind"] = "pdf"
        doc = pdfium.PdfDocument(str(path))
        try:
            page_count = len(doc)
        finally:
            doc.close()
        info["page_count"] = page_count
        info["needs_page_split"] = page_count > 1
    elif ext in IMAGE_EXTENSIONS:
        info["kind"] = "image"
        with Image.open(path) as img:
            width, height = img.size
            mode = img.mode
        info["width"] = width
        info["height"] = height
        info["mode"] = mode
    elif ext in TEXT_EXTENSIONS:
        info["kind"] = "text"
        text = path.read_text(encoding="utf-8", errors="replace")
        info["character_count"] = len(text)
        info["line_count"] = len(text.splitlines())
    else:
        info["kind"] = "other"

    return info


def chunk_audio_file(
    audio_path: str | Path,
    work_dir: str | Path,
    chunk_seconds: float = MAX_AUDIO_CHUNK_SECONDS,
) -> list[dict[str, Any]]:
    src = validate_existing_file(audio_path)
    out_dir = Path(work_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    duration = get_audio_duration_seconds(src)
    if duration <= chunk_seconds:
        return [
            {
                "index": 0,
                "path": str(src),
                "start_seconds": 0.0,
                "end_seconds": round(duration, 3),
                "duration_seconds": round(duration, 3),
            }
        ]

    ffmpeg = get_ffmpeg_bin()
    if not ffmpeg:
        raise RuntimeError(
            "ffmpeg is required to split audio longer than 30 seconds, but was not found on PATH."
        )

    chunks: list[dict[str, Any]] = []
    start = 0.0
    idx = 0
    while start < duration - 0.05:
        end = min(start + chunk_seconds, duration)
        chunk_len = round(end - start, 3)
        chunk_path = out_dir / f"{src.stem}_chunk_{idx:03d}.wav"
        cmd = [
            ffmpeg,
            "-y",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{chunk_len:.3f}",
            "-i",
            str(src),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(chunk_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0 or not chunk_path.exists():
            raise RuntimeError(f"ffmpeg failed splitting {src.name} at {start}s: {proc.stderr.strip()}")

        chunks.append(
            {
                "index": idx,
                "path": str(chunk_path),
                "start_seconds": round(start, 3),
                "end_seconds": round(end, 3),
                "duration_seconds": chunk_len,
            }
        )
        start = end
        idx += 1

    return chunks


def pdf_to_page_images(
    pdf_path: str | Path,
    work_dir: str | Path,
    scale: float = 2.0,
) -> list[str]:
    src = validate_existing_file(pdf_path)
    out_dir = Path(work_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    doc = pdfium.PdfDocument(str(src))
    image_paths: list[str] = []
    try:
        for i in range(len(doc)):
            page = doc[i]
            bitmap = page.render(scale=scale)
            try:
                pil_img = bitmap.to_pil()
                page_file = out_dir / f"{src.stem}_page_{i + 1:03d}.png"
                pil_img.save(page_file, format="PNG")
                image_paths.append(str(page_file))
            finally:
                bitmap.close()
                page.close()
    finally:
        doc.close()

    return image_paths


def stitch_wav_files(wav_paths: list[str | Path], output_path: str | Path) -> str:
    if not wav_paths:
        raise ValueError("No audio segments provided to stitch.")

    resolved = [validate_existing_file(p) for p in wav_paths]
    out_file = Path(output_path).expanduser().resolve()
    out_file.parent.mkdir(parents=True, exist_ok=True)

    ffmpeg = get_ffmpeg_bin()
    if ffmpeg:
        list_file = out_file.parent / f"{out_file.stem}_concat.txt"
        lines = []
        for p in resolved:
            escaped = str(p).replace("\\", "/").replace("'", "'\\''")
            lines.append(f"file '{escaped}'")
        list_file.write_text("\n".join(lines), encoding="utf-8")
        proc = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_file),
                "-c:a",
                "pcm_s16le",
                str(out_file),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and out_file.exists():
            return str(out_file)

    with wave.open(str(resolved[0]), "rb") as first_wf:
        params = first_wf.getparams()
        frames = [first_wf.readframes(first_wf.getnframes())]

    for p in resolved[1:]:
        with wave.open(str(p), "rb") as wf:
            if wf.getparams()[:3] != params[:3]:
                raise ValueError(f"Incompatible WAV parameters in {p}")
            frames.append(wf.readframes(wf.getnframes()))

    with wave.open(str(out_file), "wb") as out_wf:
        out_wf.setparams(params)
        for chunk in frames:
            out_wf.writeframes(chunk)

    return str(out_file)


def ensure_dir(dir_path: str | Path) -> Path:
    path = Path(dir_path).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o755) if os.name != "nt" else None
    return path