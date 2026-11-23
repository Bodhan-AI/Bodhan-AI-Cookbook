"""Media processing utilities: audio duration, chunking, stitching, and PDF rasterization."""

from __future__ import annotations

import contextlib
import io
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Iterator

from bodhan_mcp.config import MAX_AUDIO_CHUNK_SECONDS
from bodhan_mcp.errors import InvalidInputError


def get_audio_duration_seconds(path: Path) -> float:
    """Return the duration of an audio file in seconds."""
    # First try standard wave module if it's a WAV file
    if path.suffix.lower() == ".wav":
        try:
            with contextlib.closing(wave.open(str(path), "rb")) as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                if rate > 0:
                    return float(frames) / float(rate)
        except Exception:
            pass

    # Try soundfile if available
    try:
        import soundfile as sf
        info = sf.info(str(path))
        return float(info.duration)
    except Exception:
        pass

    # Fallback to ffprobe
    if shutil.which("ffprobe"):
        try:
            cmd = [
                "ffprobe", "-v", "error", "-show_entries",
                "format=duration", "-of", "default=noprint_wrappers=1:nokey=1",
                str(path)
            ]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
            return float(res.stdout.strip())
        except Exception:
            pass

    return 0.0


def convert_to_wav(input_path: Path, output_wav_path: Path, sample_rate: int = 24000) -> Path:
    """Convert any audio file to 16-bit mono WAV at the specified sample rate."""
    if shutil.which("ffmpeg"):
        cmd = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-ar", str(sample_rate), "-ac", "1", "-c:a", "pcm_s16le",
            str(output_wav_path)
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode == 0 and output_wav_path.exists() and output_wav_path.stat().st_size > 44:
            return output_wav_path

    # Fallback to soundfile if ffmpeg is missing or failed
    try:
        import soundfile as sf
        data, sr = sf.read(str(input_path))
        sf.write(str(output_wav_path), data, sample_rate, subtype="PCM_16")
        return output_wav_path
    except Exception as e:
        raise InvalidInputError(f"Failed to convert audio file {input_path} to WAV: {e}")


def chunk_wav_file(wav_path: Path, chunk_seconds: float = MAX_AUDIO_CHUNK_SECONDS) -> Iterator[tuple[int, float, float, bytes]]:
    """Yield (index, start_seconds, end_seconds, wav_bytes) for sequential chunks under chunk_seconds."""
    with contextlib.closing(wave.open(str(wav_path), "rb")) as wf:
        params = wf.getparams()
        framerate = params.framerate
        frames_per_chunk = int(framerate * chunk_seconds)
        total_frames = wf.getnframes()
        if total_frames == 0:
            return

        idx = 0
        frames_read = 0
        while True:
            frames = wf.readframes(frames_per_chunk)
            if not frames:
                break
            num_frames_in_chunk = len(frames) // (params.nchannels * params.sampwidth)
            start_sec = float(frames_read) / float(framerate)
            end_sec = float(frames_read + num_frames_in_chunk) / float(framerate)
            frames_read += num_frames_in_chunk

            buf = io.BytesIO()
            with contextlib.closing(wave.open(buf, "wb")) as out_wf:
                out_wf.setparams(params)
                out_wf.writeframes(frames)

            yield idx, start_sec, end_sec, buf.getvalue()
            idx += 1


def stitch_wav_bytes(clips_bytes: list[bytes], output_path: Path) -> Path:
    """Concatenate multiple PCM16 WAV byte blobs into a single continuous WAV file."""
    if not clips_bytes:
        raise InvalidInputError("No audio clips to stitch")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    params = None
    frames_list = []

    for blob in clips_bytes:
        buf = io.BytesIO(blob)
        with contextlib.closing(wave.open(buf, "rb")) as wf:
            if params is None:
                params = wf.getparams()
            frames_list.append(wf.readframes(wf.getnframes()))

    with contextlib.closing(wave.open(str(output_path), "wb")) as out_wf:
        out_wf.setparams(params)
        for frames in frames_list:
            out_wf.writeframes(frames)

    return output_path


def rasterize_pdf_to_images(pdf_path: Path, dpi: int = 150) -> list[tuple[int, Path]]:
    """Convert a multi-page PDF into a list of (page_num_1_indexed, png_image_path).

    Uses pypdfium2 for high-performance in-memory rasterization.
    """
    import pypdfium2 as pdfium

    temp_dir = Path(tempfile.mkdtemp(prefix="bodhan_ocr_pdf_"))
    results = []

    doc = pdfium.PdfDocument(str(pdf_path))
    scale = float(dpi) / 72.0

    try:
        for idx in range(len(doc)):
            page = doc[idx]
            pil_image = page.render(scale=scale).to_pil()
            img_path = temp_dir / f"page_{idx + 1:03d}.png"
            pil_image.save(img_path, format="PNG")
            results.append((idx + 1, img_path))
    finally:
        doc.close()

    return results
