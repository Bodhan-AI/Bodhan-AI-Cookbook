"""Media inspection (stdlib + pypdfium2 + Pillow; ffprobe only for non-WAV audio/video). No model calls."""

from __future__ import annotations

import json
import shutil
import subprocess
import wave
from pathlib import Path

from bodhan_core.config import section

KINDS = {
    "audio": {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".aac", ".wma"},
    "video": {".mp4", ".mkv", ".mov", ".avi", ".webm"},
    "image": {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"},
    "pdf": {".pdf"},
    "text": {".txt", ".md", ".srt", ".csv", ".json"},
}


def kind_of(path: Path) -> str:
    ext = path.suffix.lower()
    return next((k for k, exts in KINDS.items() if ext in exts), "other")


def _ffprobe_duration(path: Path) -> float | None:
    if not shutil.which("ffprobe"):
        return None
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)], capture_output=True, timeout=20)
        return round(float(json.loads(out.stdout)["format"]["duration"]), 1)
    except Exception:
        return None


def details(path: Path, kind: str) -> dict:
    """Duration (audio/video), page count (PDF) or pixel size (image). Anything unreadable is simply left out."""
    try:
        if kind in ("audio", "video"):
            if path.suffix.lower() == ".wav":
                try:
                    with wave.open(str(path)) as w:
                        return {"duration_s": round(w.getnframes() / w.getframerate(), 1)}
                except wave.Error:
                    pass
            d = _ffprobe_duration(path)
            return {"duration_s": d} if d is not None else {}
        if kind == "pdf":
            import pypdfium2

            pdf = pypdfium2.PdfDocument(str(path))
            try:
                return {"pages": len(pdf)}
            finally:
                pdf.close()
        if kind == "image":
            from PIL import Image

            with Image.open(path) as im:
                return {"width": im.width, "height": im.height}
    except Exception:
        pass
    return {}


def estimate_inr(kind: str, info: dict) -> dict:
    """What the natural Bodhan operation for this file would cost: transcription for audio/video, OCR for images/PDFs."""
    if kind in ("audio", "video") and "duration_s" in info:
        return {"operation": "transcribe_audio", "est_cost_inr": round(info["duration_s"] / 60 * section("transcribe")["inr_per_minute"], 4)}
    if kind in ("pdf", "image"):
        pages = info.get("pages", 1)
        return {"operation": "ocr_document", "est_cost_inr": round(pages * section("ocr")["inr_per_page"], 4)}
    return {}
