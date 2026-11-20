from __future__ import annotations

import hashlib
import io
import re
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Iterator

import fitz
import imageio_ffmpeg
import lameenc
from mutagen import File as MutagenFile, MutagenError
from mutagen.mp3 import MP3
from PIL import Image, UnidentifiedImageError

from bodhan_mcp.errors import ToolError

SUPPORTED_AUDIO = {".wav", ".flac", ".ogg", ".mp3"}
SUPPORTED_IMAGE = {".png", ".jpg", ".jpeg", ".webp"}
MAX_TRANSCRIBE_SECONDS = 30.0


def ffmpeg_executable() -> str:
    return shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve_input(path_value: str, roots: tuple[Path, ...]) -> Path:
    try:
        path = Path(path_value).expanduser().resolve(strict=True)
    except FileNotFoundError as exc:
        raise ToolError("missing_input", f"Input file does not exist: {Path(path_value).name}") from exc
    except OSError as exc:
        raise ToolError("invalid_path", "Input path cannot be accessed.") from exc
    if not path.is_file():
        raise ToolError("invalid_path", "Input must be a regular file.")
    normalized_name = path.name.casefold()
    if normalized_name in {"keys.txt", ".env"} or normalized_name.startswith(".env."):
        raise ToolError("sensitive_file_blocked", "Credential/configuration files cannot be processed as task inputs.")
    if not any(path == root or root in path.parents for root in roots):
        raise ToolError("invalid_path", "Input file is outside the permitted input roots.")
    if path.stat().st_size == 0:
        raise ToolError("empty_file", "Input file is empty.")
    return path


def audio_duration(path: Path) -> float:
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as audio:
                return audio.getnframes() / audio.getframerate()
        except (wave.Error, ZeroDivisionError) as exc:
            raise ToolError("corrupt_media", "The WAV file is invalid or unreadable.") from exc
    try:
        audio = MP3(path) if path.suffix.lower() == ".mp3" else MutagenFile(path)
        duration = float(audio.info.length) if audio is not None and audio.info else 0
    except (MutagenError, OSError, AttributeError, TypeError, ValueError):
        duration = 0
    if duration <= 0:
        ffprobe = shutil.which("ffprobe")
        if ffprobe:
            result = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            if result.returncode == 0:
                try:
                    duration = float(result.stdout.strip())
                except ValueError:
                    duration = 0
    if duration <= 0:
        raise ToolError("corrupt_media", "Could not determine a positive audio duration.")
    return duration


def inspect_media_file(path: Path) -> dict[str, object]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            with fitz.open(path) as document:
                if document.is_encrypted or document.page_count == 0:
                    raise ToolError("corrupt_media", "The PDF is encrypted or contains no pages.")
                return {
                    "status": "ok",
                    "kind": "document",
                    "format": "PDF",
                    "page_count": document.page_count,
                }
        except ToolError:
            raise
        except (fitz.FileDataError, RuntimeError, OSError) as exc:
            raise ToolError("corrupt_media", "The PDF is invalid or unreadable.") from exc

    if suffix in SUPPORTED_IMAGE:
        try:
            with Image.open(path) as image:
                image_format = image.format
                width, height = image.size
                image.verify()
            expected_formats = {
                ".png": "PNG",
                ".jpg": "JPEG",
                ".jpeg": "JPEG",
                ".webp": "WEBP",
            }
            if image_format != expected_formats[suffix]:
                raise ToolError("invalid_file_signature", "Image extension does not match its content.")
            return {
                "status": "ok",
                "kind": "image",
                "format": image_format,
                "width": width,
                "height": height,
                "page_count": 1,
            }
        except ToolError:
            raise
        except (UnidentifiedImageError, OSError) as exc:
            raise ToolError("corrupt_media", "The image file is invalid or unreadable.") from exc

    if suffix in SUPPORTED_AUDIO:
        try:
            if suffix == ".wav":
                with wave.open(str(path), "rb") as audio:
                    duration = audio.getnframes() / audio.getframerate()
                    sample_rate = audio.getframerate()
                    channels = audio.getnchannels()
                    codec = "WAV/PCM"
            else:
                audio = MP3(path) if suffix == ".mp3" else MutagenFile(path)
                info = audio.info if audio is not None else None
                if info is None:
                    raise ToolError("corrupt_media", "Audio metadata could not be read.")
                duration = float(info.length)
                sample_rate = getattr(info, "sample_rate", None)
                channels = getattr(info, "channels", None)
                codec = type(audio).__name__
            if duration <= 0:
                raise ToolError("corrupt_media", "Audio duration must be positive.")
            return {
                "status": "ok",
                "kind": "audio",
                "format": codec,
                "duration_seconds": round(duration, 3),
                "sample_rate_hz": sample_rate,
                "channels": channels,
            }
        except ToolError:
            raise
        except (wave.Error, ZeroDivisionError, MutagenError, OSError, AttributeError, TypeError, ValueError) as exc:
            raise ToolError("corrupt_media", "The audio file is invalid or unreadable.") from exc

    if suffix in {".txt", ".md", ".srt", ".csv"}:
        return {
            "status": "ok",
            "kind": "text",
            "format": suffix[1:].upper(),
        }

    return {
        "status": "unsupported",
        "kind": "unknown",
        "format": suffix[1:].upper() if suffix else "unknown",
    }


def split_audio(path: Path, window_seconds: float = MAX_TRANSCRIBE_SECONDS) -> Iterator[tuple[Path, float]]:
    duration = audio_duration(path)
    if path.suffix.lower() == ".wav":
        with wave.open(str(path), "rb") as source:
            params = source.getparams()
            bytes_per_second = params.framerate * params.nchannels * params.sampwidth
            frames_per_window = max(1, int(params.framerate * window_seconds))
            total_frames = params.nframes
            for start in range(0, total_frames, frames_per_window):
                source.setpos(start)
                frame_count = min(frames_per_window, total_frames - start)
                frames = source.readframes(frame_count)
                chunk = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
                chunk_path = Path(chunk.name)
                chunk.close()
                with wave.open(str(chunk_path), "wb") as target:
                    target.setparams(params._replace(nframes=frame_count))
                    target.writeframes(frames)
                yield chunk_path, len(frames) / bytes_per_second
        return
    ffmpeg = ffmpeg_executable()
    with tempfile.TemporaryDirectory(prefix="bodhan-audio-") as directory:
        pattern = str(Path(directory) / "chunk-%05d.wav")
        result = subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-i",
                str(path),
                "-f",
                "segment",
                "-segment_time",
                str(window_seconds),
                "-reset_timestamps",
                "1",
                "-c:a",
                "pcm_s16le",
                pattern,
            ],
            capture_output=True,
            text=True,
            timeout=max(30, int(duration * 2)),
            check=False,
        )
        if result.returncode:
            raise ToolError("corrupt_media", "FFmpeg could not split this audio file.")
        chunks = sorted(Path(directory).glob("chunk-*.wav"))
        if not chunks:
            raise ToolError("corrupt_media", "FFmpeg produced no audio chunks.")
        for chunk in chunks:
            chunk_duration = audio_duration(chunk)
            if chunk_duration > window_seconds + 0.05:
                raise ToolError("media_limit_exceeded", "An audio chunk exceeds Bodhan's 30-second limit.")
            yield chunk, chunk_duration


def image_pages(path: Path) -> Iterator[tuple[int, bytes, str]]:
    suffix = path.suffix.lower()
    if suffix in SUPPORTED_IMAGE:
        try:
            with Image.open(path) as image:
                image_format = image.format
                image.verify()
            if image_format not in {"PNG", "JPEG", "WEBP"}:
                raise ToolError("unsupported_file_type", "Image content must be PNG, JPEG, or WEBP.")
            if suffix == ".png" and image_format != "PNG":
                raise ToolError("invalid_file_signature", "Image extension does not match its content.")
            if suffix in {".jpg", ".jpeg"} and image_format != "JPEG":
                raise ToolError("invalid_file_signature", "Image extension does not match its content.")
            if image_format == "WEBP":
                with Image.open(path) as image:
                    converted = image.convert("RGB")
                    buffer = io.BytesIO()
                    converted.save(buffer, format="PNG")
                yield 1, buffer.getvalue(), "image/png"
            else:
                yield 1, path.read_bytes(), "image/png" if image_format == "PNG" else "image/jpeg"
        except ToolError:
            raise
        except (UnidentifiedImageError, OSError) as exc:
            raise ToolError("corrupt_media", "The image file is invalid or unreadable.") from exc
        return
    if suffix != ".pdf":
        raise ToolError("unsupported_file_type", "OCR accepts PDF, PNG, JPG, JPEG, and WEBP files.")
    try:
        document = fitz.open(path)
        if document.page_count == 0:
            document.close()
            raise ToolError("corrupt_media", "The PDF has no pages.")
        try:
            for page_number, page in enumerate(document, start=1):
                pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                yield page_number, pixmap.tobytes("png"), "image/png"
        finally:
            document.close()
    except (fitz.FileDataError, RuntimeError) as exc:
        raise ToolError("corrupt_media", "The PDF could not be rendered.") from exc


def split_text(text: str, max_chars: int) -> list[str]:
    if max_chars < 1:
        raise ValueError("max_chars must be positive.")
    text = text.strip()
    if not text:
        raise ToolError("invalid_tool_arguments", "Text must not be empty.")
    chunks: list[str] = []
    while len(text) > max_chars:
        boundary = max(
            text.rfind("\n\n", 0, max_chars + 1),
            text.rfind("\n", 0, max_chars + 1),
            text.rfind(" ", 0, max_chars + 1),
        )
        if boundary <= 0:
            boundary = max_chars
        chunks.append(text[:boundary].strip())
        text = text[boundary:].strip()
    if text:
        chunks.append(text)
    return chunks


def tts_chunks(text: str, max_chars: int = 280) -> list[str]:
    import re

    sentences = re.split(r"(?<=[.!?।])\s+", text.strip())
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if not sentence:
            continue
        parts = split_text(sentence, max_chars) if len(sentence) > max_chars else [sentence]
        for part in parts:
            candidate = f"{current} {part}".strip()
            if current and len(candidate) > max_chars:
                chunks.append(current)
                current = part
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


def join_wav_files(paths: list[Path], destination: Path) -> None:
    if not paths:
        raise ToolError("tts_generation_failed", "The speech model returned no audio.")
    try:
        with wave.open(str(paths[0]), "rb") as first:
            params = first.getparams()
            frames = [first.readframes(first.getnframes())]
        for path in paths[1:]:
            with wave.open(str(path), "rb") as audio:
                other = audio.getparams()
                if other[:3] + other[4:] != params[:3] + params[4:]:
                    raise ToolError("invalid_audio_response", "Speech chunks have incompatible WAV formats.")
                frames.append(audio.readframes(audio.getnframes()))
        with wave.open(str(destination), "wb") as output:
            output.setparams(params)
            for chunk in frames:
                output.writeframes(chunk)
    except (wave.Error, OSError) as exc:
        raise ToolError("invalid_audio_response", "The speech response was not a valid WAV file.") from exc


def wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as audio:
            return audio.getnframes() / audio.getframerate()
    except (wave.Error, ZeroDivisionError) as exc:
        raise ToolError("invalid_audio_response", "Generated audio is not a valid WAV file.") from exc


def convert_wav_to_mp3(source: Path, destination: Path) -> None:
    try:
        with wave.open(str(source), "rb") as audio:
            channels = audio.getnchannels()
            sample_width = audio.getsampwidth()
            sample_rate = audio.getframerate()
            frames = audio.readframes(audio.getnframes())
        if sample_width != 2:
            raise ToolError("invalid_audio_response", "MP3 conversion currently requires 16-bit PCM WAV.")
        encoder = lameenc.Encoder()
        encoder.set_in_sample_rate(sample_rate)
        encoder.set_channels(channels)
        encoder.set_bit_rate(128)
        encoder.set_quality(2)
        encoded = encoder.encode(frames) + encoder.flush()
        if not encoded:
            raise ToolError("audio_conversion_failed", "The MP3 encoder returned empty audio.")
        destination.write_bytes(encoded)
    except ToolError:
        raise
    except (OSError, wave.Error, RuntimeError) as exc:
        raise ToolError("audio_conversion_failed", "Could not encode the WAV output as MP3.") from exc


def _srt_timestamp(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{whole_seconds:02},{milliseconds:03}"


def write_srt(segments: list[dict[str, object]]) -> str:
    blocks = []
    for index, segment in enumerate(segments, start=1):
        start = float(segment["start"])
        end = float(segment["end"])
        text = str(segment["text"]).strip()
        if start < 0 or end <= start or not text:
            raise ToolError("invalid_subtitles", "Each subtitle needs text and a positive time range.")
        blocks.append(
            f"{index}\n{_srt_timestamp(start)} --> {_srt_timestamp(end)}\n{text}"
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def parse_srt(content: str) -> list[dict[str, object]]:
    segments: list[dict[str, object]] = []
    for block in re.split(r"\r?\n\s*\r?\n", content.strip()):
        lines = block.splitlines()
        if len(lines) < 3 or not lines[0].strip().isdigit():
            raise ToolError("invalid_subtitles", "The subtitle file must use standard SRT blocks.")
        match = re.fullmatch(
            r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s+-->\s+"
            r"(\d{2}):(\d{2}):(\d{2}),(\d{3})",
            lines[1].strip(),
        )
        if not match:
            raise ToolError("invalid_subtitles", "The subtitle file contains an invalid time range.")
        values = [int(value) for value in match.groups()]
        start = values[0] * 3600 + values[1] * 60 + values[2] + values[3] / 1000
        end = values[4] * 3600 + values[5] * 60 + values[6] + values[7] / 1000
        text = "\n".join(lines[2:]).strip()
        if end <= start or not text:
            raise ToolError("invalid_subtitles", "Each subtitle needs text and a positive time range.")
        segments.append({"start": start, "end": end, "text": text})
    if not segments:
        raise ToolError("invalid_subtitles", "The subtitle file contains no caption blocks.")
    return segments


def read_text_source(text: str | None, text_file_path: str | None, roots: tuple[Path, ...]) -> str:
    if bool(text and text.strip()) == bool(text_file_path):
        raise ToolError("invalid_tool_arguments", "Provide exactly one non-empty text value or text_file_path.")
    if text_file_path:
        path = resolve_input(text_file_path, roots)
        if path.suffix.lower() not in {".txt", ".md", ".srt", ".csv"}:
            raise ToolError("unsupported_file_type", "Text file references must use .txt, .md, .srt, or .csv.")
        try:
            value = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError("invalid_text_encoding", "Text files must be UTF-8 encoded.") from exc
    else:
        value = text or ""
    if not value.strip():
        raise ToolError("invalid_tool_arguments", "Text input must not be empty.")
    return value
