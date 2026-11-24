#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import wave
import contextlib
from pathlib import Path
from typing import Iterator

try:
    import pypdfium2
    from PIL import Image
    HAS_PYPDFIUM = True
except ImportError:
    HAS_PYPDFIUM = False


AUDIO_MAX_CHUNK_SEC = 30.0
SILENCE_FALLBACK_CAP_SEC = 28.0
TTS_MAX_CHARS = 3000


def run_cmd(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def inspect_audio_video(path: str) -> dict:
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", path
    ]
    result = run_cmd(cmd)
    data = json.loads(result.stdout)
    fmt = data.get("format", {})
    duration = float(fmt.get("duration", 0))
    bitrate = int(fmt.get("bit_rate", 0))
    codec = fmt.get("format_name", "")
    streams = data.get("streams", [])
    sample_rate = channels = None
    audio_codec = None
    for s in streams:
        if s.get("codec_type") == "audio":
            sample_rate = (
                int(s.get("sample_rate", 0)) if s.get("sample_rate") else None
            )
            channels = s.get("channels")
            audio_codec = s.get("codec_name")
            break
    return {
        "duration_sec": duration,
        "bitrate": bitrate,
        "codec": codec,
        "audio_codec": audio_codec,
        "sample_rate": sample_rate,
        "channels": channels,
    }


def inspect_pdf(path: str) -> dict:
    if not HAS_PYPDFIUM:
        raise ImportError("pypdfium2 not installed")
    doc = pypdfium2.PdfDocument(path)
    page_count = len(doc)
    doc.close()
    return {"pages": page_count}


def inspect_image(path: str) -> dict:
    if not HAS_PYPDFIUM:
        raise ImportError("Pillow not installed")
    with Image.open(path) as img:
        return {"width": img.width, "height": img.height, "format": img.format}


def convert_to_wav_16khz_mono(in_path: str, out_path: str) -> str:
    cmd = [
        "ffmpeg", "-y", "-i", in_path,
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", out_path
    ]
    run_cmd(cmd)
    return out_path


def detect_silence_intervals(
    path: str, noise_db: int = -40, min_silence_dur: float = 0.5
) -> list[tuple[float, float]]:
    cmd = [
        "ffmpeg", "-i", path, "-af",
        f"silencedetect=noise={noise_db}dB:d={min_silence_dur}",
        "-f", "null", "-"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    intervals = []
    start = None
    for line in result.stderr.splitlines():
        if "silence_start" in line:
            match = re.search(r"silence_start: ([\d.]+)", line)
            if match:
                start = float(match.group(1))
        elif "silence_end" in line and start is not None:
            match = re.search(r"silence_end: ([\d.]+)", line)
            if match:
                end = float(match.group(1))
                intervals.append((start, end))
                start = None
    return intervals


def chunk_wav_silence_aware(
    path: str,
    max_sec: float = AUDIO_MAX_CHUNK_SEC,
    silence_db: int = -40,
    min_silence_dur: float = 0.5,
) -> Iterator[tuple[int, str, tuple[float, float]]]:
    info = inspect_audio_video(path)
    duration = info["duration_sec"]
    if duration <= max_sec:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
        convert_to_wav_16khz_mono(path, tmp_path)
        yield 0, tmp_path, (0.0, duration)
        return

    silence_intervals = detect_silence_intervals(path, silence_db, min_silence_dur)
    silence_starts = [s for s, _ in silence_intervals]

    start = 0.0
    idx = 0
    while start < duration:
        ideal_end = start + max_sec
        hard_cap = start + SILENCE_FALLBACK_CAP_SEC
        end = min(ideal_end, duration)

        if end < duration:
            candidate = None
            for s in silence_starts:
                if start < s <= hard_cap:
                    candidate = s
                    break
            if candidate is not None:
                end = min(candidate, hard_cap)
            else:
                end = hard_cap

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
        cmd = [
            "ffmpeg", "-y", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}",
            "-i", path, "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", tmp_path
        ]
        run_cmd(cmd)
        yield idx, tmp_path, (start, end)

        start = end
        idx += 1


def split_sentences(text: str, lang: str = "en") -> list[str]:
    if not text.strip():
        return []

    indian_langs = frozenset([
        "hi", "bn", "te", "ta", "mr", "gu", "kn", "ml", "or", "sa", "ur",
        "as", "bho", "brx", "doi", "mai", "mni", "ne", "pa", "sat",
        "sd", "ks", "kok", "bhb",
    ])

    if lang in indian_langs:
        pattern = r"(?<=[।.!?])\s+"
    else:
        pattern = r"(?<=[.!?])\s+"

    sentences = [s.strip() for s in re.split(pattern, text) if s.strip()]

    chunks = []
    current = ""
    for s in sentences:
        if len(current) + len(s) + 1 <= TTS_MAX_CHARS:
            current = (current + " " + s).strip()
        else:
            if current:
                chunks.append(current)
            current = s
    if current:
        chunks.append(current)
    return chunks


def chunk_text_for_translation(text: str) -> list[str]:
    if not text.strip():
        return []

    chunks = []
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        if "|" in para and re.search(r"^\|.*\|$", para, re.MULTILINE):
            lines = para.split("\n")
            header = None
            separator = None
            body = []
            for line in lines:
                if line.strip().startswith("|"):
                    if header is None and re.search(r"\|.*-+.*\|", line):
                        separator = line
                    elif header is None:
                        header = line
                    else:
                        body.append(line)
            if header and separator and body:
                for row in body:
                    chunks.append(header + "\n" + separator + "\n" + row)
            else:
                chunks.append(para)
        else:
            if len(para) > 5000:
                sentences = split_sentences(para, "en")
                current = ""
                for s in sentences:
                    if len(current) + len(s) + 1 <= 5000:
                        current = (current + " " + s).strip()
                    else:
                        if current:
                            chunks.append(current)
                        current = s
                if current:
                    chunks.append(current)
            else:
                chunks.append(para)
    return chunks


def pdf_to_pngs(path: str, dpi: int = 150) -> list[str]:
    if not HAS_PYPDFIUM:
        raise ImportError("pypdfium2 not installed")
    images = []
    doc = pypdfium2.PdfDocument(path)
    for page_idx in range(len(doc)):
        page = doc.get_page(page_idx)
        bitmap = page.render(scale=dpi / 72)
        if hasattr(bitmap, "to_pil"):
            img = bitmap.to_pil()
        else:
            img = Image.frombytes(
                "RGB", (bitmap.width, bitmap.height), bitmap.samples
            )
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            img.save(tmp.name, "PNG")
            images.append(tmp.name)
        page.close()
    doc.close()
    return images


def stitch_wav(in_paths: list[str], out_path: str) -> str:
    params = None
    frames = []
    for path in in_paths:
        with contextlib.closing(wave.open(path, "rb")) as wf:
            if params is None:
                params = wf.getparams()
            frames.append(wf.readframes(wf.getnframes()))
    with contextlib.closing(wave.open(out_path, "wb")) as wf:
        wf.setparams(params)
        for f in frames:
            wf.writeframes(f)
    return out_path


def stitch_wav_ffmpeg(in_paths: list[str], out_path: str) -> str:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        for p in in_paths:
            f.write(f"file '{Path(p).resolve()}'\n")
        list_file = f.name

    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file,
        "-c", "copy", out_path
    ]
    run_cmd(cmd)
    Path(list_file).unlink(missing_ok=True)
    return out_path


def count_table_rows(text: str) -> int:
    return len([
        line for line in text.split("\n")
        if line.strip().startswith("|") and "---" not in line
    ])


def count_table_cols(text: str) -> int:
    for line in text.split("\n"):
        if "---" in line:
            return len([c for c in line.strip().split("|") if c.strip()])
    return 0


def verify_table_parity(source: str, target: str) -> bool:
    src_rows = count_table_rows(source)
    tgt_rows = count_table_rows(target)
    src_cols = count_table_cols(source)
    tgt_cols = count_table_cols(target)
    return src_rows == tgt_rows and src_cols == tgt_cols


def detect_script(text: str) -> str:
    patterns = {
        "devanagari": r"[\u0900-\u097F]",
        "tamil": r"[\u0B80-\u0BFF]",
        "telugu": r"[\u0C00-\u0C7F]",
        "bengali": r"[\u0980-\u09FF]",
        "kannada": r"[\u0C80-\u0CFF]",
        "gujarati": r"[\u0A80-\u0AFF]",
        "malayalam": r"[\u0D00-\u0D7F]",
        "oriya": r"[\u0B00-\u0B7F]",
        "gurmukhi": r"[\u0A00-\u0A7F]",
        "arabic": r"[\u0600-\u06FF]",
    }
    counts = {
        name: len(re.findall(pattern, text))
        for name, pattern in patterns.items()
    }
    max_script = max(counts, key=counts.get)
    return max_script if counts[max_script] > 0 else "latin"


def script_ratio(text: str, target_script: str) -> float:
    patterns = {
        "devanagari": r"[\u0900-\u097F]",
        "tamil": r"[\u0B80-\u0BFF]",
        "telugu": r"[\u0C00-\u0C7F]",
        "bengali": r"[\u0980-\u09FF]",
        "kannada": r"[\u0C80-\u0CFF]",
        "gujarati": r"[\u0A80-\u0AFF]",
        "malayalam": r"[\u0D00-\u0D7F]",
        "oriya": r"[\u0B00-\u0B7F]",
        "gurmukhi": r"[\u0A00-\u0A7F]",
        "arabic": r"[\u0600-\u06FF]",
        "latin": r"[A-Za-z]",
    }
    if target_script not in patterns:
        return 0.0
    target_chars = len(re.findall(patterns[target_script], text))
    total_letters = sum(len(re.findall(p, text)) for p in patterns.values())
    return target_chars / total_letters if total_letters > 0 else 0.0

