"""Files in and out: the path-based file store, audio with ffmpeg, and page images for OCR.

Media never travels inline: tools take paths and return paths. FFmpeg is used only to decode, cut, join
and encode audio; its built-in `whisper` filter (speech recognition) is never used, all recognition goes to
indic-transcribe. PDFs are only ever rendered to pixels with pypdfium2; their embedded text layer is never
read, all text extraction goes through indic-ocr.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import subprocess
import wave
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, UnidentifiedImageError

from .client import Code, ToolFailure

AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac", ".wma", ".amr", ".3gp"}
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".wmv", ".mpeg", ".mpg"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
PDF_EXT = {".pdf"}
TEXT_EXT = {".txt", ".md", ".markdown", ".srt", ".vtt", ".json", ".csv", ".tsv", ".html", ".htm", ".text"}

MAX_INPUT_BYTES = 500 * 1024 * 1024


def classify(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in AUDIO_EXT:
        return "audio"
    if ext in VIDEO_EXT:
        return "video"
    if ext in IMAGE_EXT:
        return "image"
    if ext in PDF_EXT:
        return "pdf"
    if ext in TEXT_EXT:
        return "text"
    try:
        head = path.read_bytes()[:2048]
    except OSError:
        return "unknown"
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith((b"\x89PNG", b"\xff\xd8\xff")):
        return "image"
    if head.startswith(b"RIFF") or head.startswith(b"ID3"):
        return "audio"
    try:
        head.decode("utf-8")
        return "text"
    except UnicodeDecodeError:
        return "unknown"


def _safe_stem(stem: str) -> str:
    stem = re.sub(r"[^\w\-.]+", "_", stem, flags=re.UNICODE).strip("._")
    return (stem or "output")[:60]


class Store:
    def __init__(self, workdir: Path) -> None:
        self.workdir = workdir
        self.workdir.mkdir(parents=True, exist_ok=True)

    def resolve_input(self, path: str) -> Path:
        if not isinstance(path, str) or not path.strip():
            raise ToolFailure(Code.INVALID_ARGUMENT, "`path` must be a non-empty file path.")
        p = Path(path.strip().strip('"')).expanduser()
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        if not p.exists():
            raise ToolFailure(Code.FILE_NOT_FOUND, f"File not found: {p}",
                              "Pass an existing file path (absolute, or relative to the server's working directory).")
        if not p.is_file():
            raise ToolFailure(Code.NOT_A_FILE, f"Not a file: {p}", "Pass a path to a single file, not a directory.")
        size = p.stat().st_size
        if size == 0:
            raise ToolFailure(Code.EMPTY_FILE, f"File is empty: {p}")
        if size > MAX_INPUT_BYTES:
            raise ToolFailure(Code.INPUT_TOO_LARGE, f"File is {size / 1e6:.0f} MB; the limit is 500 MB.")
        return p

    def output_path(self, stem: str, suffix: str, key_material: str) -> Path:
        """Deterministic output name: <stem>.<hash6>.<suffix>, so reruns reuse the same file."""
        digest = hashlib.sha256(key_material.encode("utf-8")).hexdigest()[:6]
        return self.workdir / f"{_safe_stem(stem)}.{digest}.{suffix}"

    @staticmethod
    def read_text(path: Path) -> str:
        data = path.read_bytes()
        for encoding in ("utf-8-sig", "utf-16"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise ToolFailure(Code.UNSUPPORTED_FORMAT, f"{path.name} is not UTF-8 text.",
                          "Convert it to UTF-8 text, or use ocr_document for images and scanned PDFs.")

    @staticmethod
    def write_text(path: Path, text: str) -> Path:
        path.write_text(text, encoding="utf-8")
        return path

    @staticmethod
    def write_json(path: Path, data) -> Path:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def text_input(self, path: str | None, text: str | None, *, field_hint: str = "") -> tuple[str, str]:
        """Exactly one of path/text. Returns (content, stem)."""
        if bool(path) == bool(text):
            raise ToolFailure(Code.INVALID_ARGUMENT, "Pass exactly one of `path` or `text`.",
                              "Use `path` for files (preferred for long or Indian-language text). " + field_hint)
        if text is not None and path is None:
            return text, "text"
        p = self.resolve_input(path)
        kind = classify(p)
        if kind != "text":
            raise ToolFailure(Code.UNSUPPORTED_FORMAT, f"{p.name} is {kind}, not a text file.",
                              "Use transcribe_audio for audio/video and ocr_document for images/PDFs first, "
                              "then pass the resulting text file.")
        return self.read_text(p), p.stem.split(".")[0]


TARGET_RATE = 16_000


def _bin(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise ToolFailure(Code.FFMPEG_NOT_FOUND, f"`{name}` is not installed or not on PATH.",
                          "Install ffmpeg (https://ffmpeg.org/download.html; e.g. `winget install ffmpeg`, "
                          "`brew install ffmpeg`, `apt install ffmpeg`) and restart the server.")
    return found


def _run(args: list[str], *, input_bytes: bytes | None = None, timeout: float = 600) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(args, input=input_bytes, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise ToolFailure(Code.MEDIA_ERROR, f"{Path(args[0]).name} timed out after {timeout:.0f}s.") from None
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-3:]
        raise ToolFailure(Code.MEDIA_ERROR, f"{Path(args[0]).name} failed: {' | '.join(tail)}",
                          "The file may be corrupt or in an unsupported format.")
    return proc


def probe(path: Path) -> dict:
    proc = _run([_bin("ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    data = json.loads(proc.stdout or b"{}")
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    video = [s for s in streams if s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic") != 1]
    duration = float(fmt.get("duration") or 0.0)
    return {
        "format": fmt.get("format_name"),
        "duration_s": round(duration, 2),
        "has_audio": bool(audio),
        "has_video": bool(video),
        "audio_codec": audio[0].get("codec_name") if audio else None,
        "sample_rate": int(audio[0].get("sample_rate", 0)) if audio else None,
        "channels": audio[0].get("channels") if audio else None,
    }


def to_pcm_wav(path: Path, rate: int = TARGET_RATE) -> bytes:
    """Decode any audio/video file to mono 16-bit PCM WAV at `rate` Hz (audio track only)."""
    proc = _run([_bin("ffmpeg"), "-v", "error", "-nostdin", "-i", str(path), "-vn", "-ac", "1", "-ar", str(rate),
                 "-sample_fmt", "s16", "-f", "wav", "pipe:1"])
    return _fix_wav_header(proc.stdout)


def _fix_wav_header(data: bytes) -> bytes:
    """ffmpeg writes placeholder sizes when piping WAV; rewrite a clean header."""
    if not data.startswith(b"RIFF"):
        return data
    idx = data.find(b"data")
    if idx < 0:
        return data
    pcm = data[idx + 8:]
    fmt_idx = data.find(b"fmt ")
    channels = int.from_bytes(data[fmt_idx + 10:fmt_idx + 12], "little")
    rate = int.from_bytes(data[fmt_idx + 12:fmt_idx + 16], "little")
    width = int.from_bytes(data[fmt_idx + 22:fmt_idx + 24], "little") // 8
    return make_wav(pcm, rate=rate, channels=channels, width=width)


def make_wav(pcm: bytes, *, rate: int, channels: int = 1, width: int = 2) -> bytes:
    usable = len(pcm) - (len(pcm) % (channels * width))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(pcm[:usable])
    return buf.getvalue()


def wav_params(data: bytes) -> tuple[int, int, int, int]:
    """(rate, channels, sample_width, frames)."""
    with wave.open(io.BytesIO(data)) as w:
        return w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()


def wav_duration(data: bytes) -> float:
    rate, _, _, frames = wav_params(data)
    return frames / rate if rate else 0.0


def slice_wav(data: bytes, start: float, end: float) -> bytes:
    with wave.open(io.BytesIO(data)) as w:
        rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        w.setpos(max(0, int(start * rate)))
        frames = w.readframes(max(0, int((end - start) * rate)))
    return make_wav(frames, rate=rate, channels=channels, width=width)


def concat_wavs(clips: list[bytes], gap_ms: int = 120) -> bytes:
    """Join WAV clips with short silences. Clips are resampled to the first clip's format if needed."""
    if not clips:
        raise ToolFailure(Code.MEDIA_ERROR, "No audio to join.")
    rate, channels, width, _ = wav_params(clips[0])
    silence = b"\x00" * int(rate * gap_ms / 1000) * channels * width
    frames = []
    for i, clip in enumerate(clips):
        r, c, w, _ = wav_params(clip)
        if (r, c, w) != (rate, channels, width):
            clip = _resample(clip, rate, channels)
        with wave.open(io.BytesIO(clip)) as wf:
            frames.append(wf.readframes(wf.getnframes()))
        if i < len(clips) - 1:
            frames.append(silence)
    return make_wav(b"".join(frames), rate=rate, channels=channels, width=width)


def _resample(clip: bytes, rate: int, channels: int) -> bytes:
    proc = _run([_bin("ffmpeg"), "-v", "error", "-nostdin", "-f", "wav", "-i", "pipe:0", "-ac", str(channels),
                 "-ar", str(rate), "-sample_fmt", "s16", "-f", "wav", "pipe:1"], input_bytes=clip)
    return _fix_wav_header(proc.stdout)


_SILENCE_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*([\d.]+)")


def detect_silences(wav: bytes, noise_db: int = -35, min_silence: float = 0.3) -> list[tuple[float, float]]:
    proc = _run([_bin("ffmpeg"), "-v", "info", "-nostdin", "-f", "wav", "-i", "pipe:0",
                 "-af", f"silencedetect=noise={noise_db}dB:d={min_silence}", "-f", "null", "-"], input_bytes=wav)
    text = proc.stderr.decode("utf-8", "replace")
    starts = [max(0.0, float(m)) for m in _SILENCE_START.findall(text)]
    ends = [float(m) for m in _SILENCE_END.findall(text)]
    duration = wav_duration(wav)
    pairs = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else duration
        if e > s:
            pairs.append((s, e))
    return pairs


def plan_chunks(duration: float, silences: list[tuple[float, float]], max_len: float = 25.0,
                min_len: float = 3.0) -> list[tuple[float, float]]:
    """Cut [0, duration] into windows of at most max_len seconds, preferring cuts in the middle of
    silences. Windows never exceed max_len (hard cut when no silence is available)."""
    if duration <= 0:
        return []
    mids = sorted((s + e) / 2 for s, e in silences)
    chunks: list[tuple[float, float]] = []
    start = 0.0
    while duration - start > max_len:
        limit = start + max_len
        candidates = [m for m in mids if start + min_len <= m <= limit]
        cut = candidates[-1] if candidates else limit
        chunks.append((round(start, 3), round(cut, 3)))
        start = cut
    chunks.append((round(start, 3), round(duration, 3)))
    if len(chunks) > 1 and chunks[-1][1] - chunks[-1][0] < 0.5:  # merge a tiny tail if it fits
        s, _ = chunks[-2]
        if duration - s <= max_len:
            chunks[-2:] = [(s, round(duration, 3))]
    return chunks


def encode(wav: bytes, out_path: Path, fmt: str, metadata: dict[str, str]) -> Path:
    """Write WAV audio as mp3 or wav, with metadata tags (used for the AI-generated label)."""
    args = [_bin("ffmpeg"), "-v", "error", "-nostdin", "-y", "-f", "wav", "-i", "pipe:0"]
    for k, v in metadata.items():
        args += ["-metadata", f"{k}={v}"]
    if fmt == "mp3":
        args += ["-codec:a", "libmp3lame", "-q:a", "4"]
    else:
        args += ["-codec:a", "pcm_s16le"]
    args.append(str(out_path))
    _run(args, input_bytes=wav)
    return out_path


MAX_SIDE = 2400
MAX_BYTES = 4 * 1024 * 1024
RENDER_DPI = 170


def pdf_page_count(path: Path) -> int:
    try:
        pdf = pdfium.PdfDocument(str(path))
    except pdfium.PdfiumError as exc:
        raise ToolFailure(Code.UNSUPPORTED_FORMAT, f"Cannot open PDF {path.name}: {exc}",
                          "The PDF may be corrupt or password-protected.") from None
    try:
        return len(pdf)
    finally:
        pdf.close()


def parse_pages(spec: str | None, total: int) -> list[int]:
    """'1-3,5' -> [1, 2, 3, 5] (1-based). None/'' -> all pages."""
    if not spec or not str(spec).strip():
        return list(range(1, total + 1))
    pages: set[int] = set()
    for part in str(spec).split(","):
        part = part.strip()
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            pages.update(range(min(a, b), max(a, b) + 1))
        elif part.isdigit():
            pages.add(int(part))
        else:
            raise ToolFailure(Code.INVALID_ARGUMENT, f"Bad page spec '{spec}'.", "Use e.g. '1-3,5' (1-based).")
    bad = [p for p in pages if p < 1 or p > total]
    if bad:
        raise ToolFailure(Code.INVALID_ARGUMENT, f"Pages {sorted(bad)} are out of range; the document has {total}.")
    return sorted(pages)


def _encode(img: Image.Image) -> tuple[bytes, str]:
    img = img.convert("RGB")
    if max(img.size) > MAX_SIDE:
        img.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    if buf.tell() <= MAX_BYTES:
        return buf.getvalue(), "image/png"
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue(), "image/jpeg"


def render_pdf_pages(path: Path, pages: list[int], dpi: int = RENDER_DPI) -> list[tuple[int, bytes, str]]:
    pdf = pdfium.PdfDocument(str(path))
    try:
        out = []
        for number in pages:
            bitmap = pdf[number - 1].render(scale=dpi / 72)
            data, mime = _encode(bitmap.to_pil())
            out.append((number, data, mime))
        return out
    finally:
        pdf.close()


def prepare_image(path: Path) -> tuple[bytes, str, tuple[int, int]]:
    try:
        with Image.open(path) as img:
            img.load()
            size = img.size
            if getattr(img, "n_frames", 1) > 1:
                img.seek(0)
            data, mime = _encode(img)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ToolFailure(Code.INVALID_IMAGE, f"{path.name} is not a readable image ({exc}).",
                          "Pass a PNG/JPEG/WebP/TIFF image or a PDF.") from None
    return data, mime, size


def image_info(path: Path) -> dict:
    try:
        with Image.open(path) as img:
            return {"width": img.width, "height": img.height, "format": img.format, "mode": img.mode}
    except (UnidentifiedImageError, OSError) as exc:
        raise ToolFailure(Code.INVALID_IMAGE, f"{path.name} is not a readable image ({exc}).") from None
