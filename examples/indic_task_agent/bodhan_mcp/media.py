"""Media plumbing with ffmpeg and pypdfium2. No model calls here."""

import json
import re
import shutil
import subprocess
from pathlib import Path

from .bodhan import BodhanError

AUDIO_EXT = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".opus", ".amr", ".wma"}
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".webm", ".avi"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
TEXT_EXT = {".txt", ".md", ".markdown", ".srt", ".csv", ".json"}


def kind(path: Path) -> str:
    s = path.suffix.lower()
    for name, exts in (("audio", AUDIO_EXT), ("video", VIDEO_EXT), ("image", IMAGE_EXT), ("text", TEXT_EXT)):
        if s in exts:
            return name
    return "pdf" if s == ".pdf" else "unknown"


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    if not shutil.which(cmd[0]):
        raise BodhanError("missing_dependency", f"{cmd[0]} is not installed", "Install ffmpeg (brew/apt install ffmpeg).")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise BodhanError("media_error", f"{cmd[0]} failed: {r.stderr.strip()[-300:]}", "Is the file a valid media file?")
    return r


def probe(path: Path) -> dict:
    """Duration and which streams a media file has."""
    j = json.loads(_run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)]).stdout)
    types = {s.get("codec_type") for s in j.get("streams", [])}
    return {"duration_s": round(float(j.get("format", {}).get("duration") or 0), 2),
            "has_audio": "audio" in types, "has_video": "video" in types and kind(path) == "video"}


def to_wav(src: Path, dst: Path) -> Path:
    """Any audio/video -> 16 kHz mono WAV (what the speech model expects)."""
    _run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", str(dst)])
    return dst


def silences(wav: Path, noise_db: int = -35, min_s: float = 0.4) -> list[tuple[float, float]]:
    """(start, end) of every stretch quieter than noise_db for at least min_s seconds."""
    err = _run(["ffmpeg", "-v", "info", "-i", str(wav), "-af", f"silencedetect=noise={noise_db}dB:d={min_s}",
                "-f", "null", "-"]).stderr
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    ends += [float("inf")] * (len(starts) - len(ends))  # file ended while silent
    return [(max(s, 0.0), e) for s, e in zip(starts, ends, strict=True)]


def plan_chunks(duration: float, quiet: list[tuple[float, float]], max_s: float = 30, target_s: float = 10):
    """Split [0, duration] into windows of at most max_s, cutting in the middle of a silence.
    Prefer the first silence after target_s (short windows make readable subtitles); if none,
    the last silence before max_s; if none at all, a hard cut at max_s."""
    cuts = [(s + min(e, duration)) / 2 for s, e in quiet]
    chunks, start = [], 0.0
    while duration - start > 0.05:
        if duration - start <= max_s:
            end = duration
        else:
            window = [c for c in cuts if start + 1 < c <= start + max_s]
            later = [c for c in window if c >= start + target_s]
            end = later[0] if later else (window[-1] if window else start + max_s)
        chunks.append((round(start, 3), round(end, 3)))
        start = end
    return chunks


def trim_silence(start: float, end: float, quiet: list[tuple[float, float]], pad: float = 0.2) -> tuple[float, float]:
    """Drop silence at the edges of a window (keeping `pad` seconds): silence costs money and
    makes the speech model hallucinate."""
    for s, e in quiet:
        if s <= start < e:
            start = max(start, min(e, end) - pad)
        if s < end <= e:
            end = min(end, s + pad)
    return round(start, 3), round(max(end, start), 3)


def speech_seconds(start: float, end: float, quiet: list[tuple[float, float]]) -> float:
    """Seconds of [start, end] not covered by silence."""
    return (end - start) - sum(max(0.0, min(end, e) - max(start, s)) for s, e in quiet)


def cut(wav: Path, start: float, end: float, dst: Path) -> Path:
    _run(["ffmpeg", "-v", "error", "-y", "-ss", str(start), "-t", str(end - start), "-i", str(wav), str(dst)])
    return dst


def concat_mp3(parts: list[Path], dst: Path, comment: str) -> Path:
    """Join audio pieces into one MP3 and tag it (we label generated speech as AI-generated)."""
    listing = dst.with_suffix(".txt")
    listing.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    _run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c:a", "libmp3lame",
          "-q:a", "4", "-metadata", f"comment={comment}", str(dst)])
    listing.unlink()
    return dst


def pdf_page_count(path: Path) -> int:
    import pypdfium2 as pdfium

    return len(pdfium.PdfDocument(path))


def page_images(path: Path, work: Path, dpi: int = 200) -> list[Path]:
    """PDF -> one PNG per page; other image types -> PNG; PNG/JPEG pass through."""
    if path.suffix.lower() == ".pdf":
        import pypdfium2 as pdfium

        pages = []
        for i, page in enumerate(pdfium.PdfDocument(path), 1):
            out = work / f"{path.stem}_p{i:03d}.png"
            page.render(scale=dpi / 72).to_pil().save(out)
            pages.append(out)
        return pages
    if path.suffix.lower() in {".png", ".jpg", ".jpeg"}:
        return [path]
    from PIL import Image

    out = work / f"{path.stem}.png"
    Image.open(path).convert("RGB").save(out)
    return [out]
