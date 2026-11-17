#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import wave
import contextlib
from typing import Iterator

try:
    import ffmpeg  # noqa: F401
    HAS_FFMPEG_PYTHON = True
except ImportError:
    HAS_FFMPEG_PYTHON = False

try:
    import pypdfium2
    from PIL import Image
    HAS_PYPDFIUM = True
except ImportError:
    HAS_PYPDFIUM = False


AUDIO_MAX_CHUNK_SEC = 25.0
VIDEO_MAX_CHUNK_SEC = 25.0


def unicode_script(text: str) -> str:
    blocks = {
        \"devanagari\": r\"[\u0900-\u097F]\",
        \"tamil\": r\"[\u0B80-\u0BFF]\",
        \"telugu\": r\"[\u0C00-\u0C7F]\",
        \"bengali\": r\"[\u0980-\u09FF]\",
        \"kannada\": r\"[\u0C80-\u0CFF]\",
        \"gujarati\": r\"[\u0A80-\u0AFF]\",
        \"malayalam\": r\"[\u0D00-\u0D7F]\",
        \"ori\": r\"[\u0B00-\u0B7F]\",
        \"punjabi\": r\"[\u0A00-\u0A7F]\",
        \"hindi\": r\"[\u0900-\u097F]\",
    }
    for name, pattern in blocks.items():
        if re.search(pattern, text):
            return name
    return \"latin\"


def detect_script(text: str) -> str:
    patterns = {
        \"devanagari\": r\"[\u0900-\u097F]\",
        \"tamil\": r\"[\u0B80-\u0BFF]\",
        \"telugu\": r\"[\u0C00-\u0C7F]\",
        \"bengali\": r\"[\u0980-\u09FF]\",
        \"kannada\": r\"[\u0C80-\u0CFF]\",
        \"gujarati\": r\"[\u0A80-\u0AFF]\",
        \"malayalam\": r\"[\u0D00-\u0D7F]\",
        \"ori\": r\"[\u0B00-\u0B7F]\",
        \"hindi\": r\"[\u0900-\u097F]\",
    }
    counts = {name: len(re.findall(pattern, text)) for name, pattern in patterns.items()}
    return max(counts, key=counts.get) if max(counts.values()) > 0 else \"latin\"


def inspect_audio_video(path: str) -> dict:
    cmd = [
        \"ffprobe\", \"-v\", \"quiet\", \"-print_format\", \"json\",
        \"-show_format\", \"-show_streams\", path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(result.stdout)
    fmt = data.get(\"format\", {})
    duration = float(fmt.get(\"duration\", 0))
    bitrate = int(fmt.get(\"bit_rate\", 0))
    streams = data.get(\"streams\", [])
    sample_rate = channels = None
    for s in streams:
        if s.get(\"codec_type\") == \"audio\":
            sample_rate = s.get(\"sample_rate\")
            channels = s.get(\"channels\")
    return {
        \"duration_sec\": duration,
        \"bitrate\": bitrate,
        \"sample_rate\": sample_rate,
        \"channels\": channels
    }


def convert_to_wav_16khz_mono(in_path: str, out_path: str) -> str:
    subprocess.run([
        \"ffmpeg\", \"-y\", \"-i\", in_path,
        \"-ar\", \"16000\", \"-ac\", \"1\", \"-c:a\", \"pcm_s16le\", out_path
    ], capture_output=True, check=True)
    return out_path


def chunk_wav(
    path: str,
    max_sec: float = AUDIO_MAX_CHUNK_SEC,
    overlap_sec: float = 0.5
) -> Iterator[tuple[int, str, tuple[float, float]]]:
    info = inspect_audio_video(path)
    duration = info[\"duration_sec\"]
    start = 0.0
    idx = 0
    while start < duration:
        end = min(start + max_sec, duration)
        with tempfile.NamedTemporaryFile(suffix=\".wav\", delete=False) as tmp:
            tmp_path = tmp.name
        subprocess.run([
            \"ffmpeg\", \"-y\", \"-ss\", f\"{start:.3f}\", \"-t\", f\"{end - start:.3f}\",
            \"-i\", path, \"-ar\", \"16000\", \"-ac\", \"1\", \"-c:a\", \"pcm_s16le\", tmp_path
        ], capture_output=True, check=True)
        yield idx, tmp_path, (start, end)
        start = end - overlap_sec
        idx += 1


def chunk_video(
    path: str, max_sec: float = VIDEO_MAX_CHUNK_SEC
) -> Iterator[tuple[int, str, float]]:
    info = inspect_audio_video(path)
    duration = info[\"duration_sec\"]
    start = 0.0
    idx = 0
    while start < duration:
        end = min(start + max_sec, duration)
        with tempfile.NamedTemporaryFile(suffix=\".mp4\", delete=False) as tmp:
            tmp_path = tmp.name
        subprocess.run([
            \"ffmpeg\", \"-y\", \"-ss\", f\"{start:.3f}\", \"-t\", f\"{end - start:.3f}\",
            \"-i\", path, \"-c\", \"copy\", tmp_path
        ], capture_output=True, check=True)
        yield idx, tmp_path, start
        start = end
        idx += 1


INDIAN_LANGS = (
    \"hi\", \"bn\", \"te\", \"ta\", \"mr\", \"gu\", \"kn\", \"ml\", \"or\", \"sa\", \"ur\",
    \"as\", \"bho\", \"brx\", \"doi\", \"mai\", \"mni\", \"ne\", \"or\", \"pa\", \"sat\",
    \"sd\", \"ks\", \"kok\", \"bhb\"
)


def split_sentences(text: str, lang: str = \"en\") -> list[str]:
    if lang in INDIAN_LANGS:
        pattern = r\"(?<=[?.!?])\s+\"
    else:
        pattern = r\"(?<=[.!?])\s+\"
    return [s.strip() for s in re.split(pattern, text) if s.strip()]


def chunk_markdown_table(text: str) -> list[str]:
    chunks = []
    for para in text.split(\"\\n\\n\"):
        if \"|\" in para and \"---\" not in para:
            chunks.append(para)
    return chunks


def pdf_to_pngs(path: str, dpi: int = 150) -> list[str]:
    if not HAS_PYPDFIUM:
        raise ImportError(\"pypdfium2 not installed\")
    images = []
    doc = pypdfium2.PdfDocument(path)
    for page_idx in range(len(doc)):
        page = doc.get_page(page_idx)
        bitmap = page.render(scale=dpi / 72)
        img = Image.frombytes(\"RGB\", (bitmap.width, bitmap.height), bitmap.samples)
        with tempfile.NamedTemporaryFile(suffix=\".png\", delete=False) as tmp:
            img.save(tmp.name, \"PNG\")
            images.append(tmp.name)
        page.close()
    doc.close()
    return images


def stitch_wav(in_paths: list[str], out_path: str) -> str:
    params = None
    frames = []
    for path in in_paths:
        with contextlib.closing(wave.open(path, \"rb\")) as wf:
            if params is None:
                params = wf.getparams()
            frames.append(wf.readframes(wf.getnframes()))
    with contextlib.closing(wave.open(out_path, \"wb\")) as wf:
        wf.setparams(params)
        for f in frames:
            wf.writeframes(f)
    return out_path


def count_table_rows(text: str) -> int:
    return len([
        line for line in text.split(\"\\n\")
        if line.strip().startswith(\"|\") and \"---\" not in line
    ])


def count_table_cols(text: str) -> int:
    for line in text.split(\"\\n\"):
        if \"---\" in line:
            return len(line.strip().split(\"|\")) - 1
    return 0
