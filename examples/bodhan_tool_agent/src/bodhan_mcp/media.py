from __future__ import annotations

import array
import io
import math
import re
import shutil
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from bodhan_mcp.config import (
    MAX_ASR_WINDOW_SECONDS,
    MAX_TRANSLATE_CHUNK_CHARS,
    MAX_TTS_CHUNK_CHARS,
)


@dataclass
class AudioChunk:
    chunk_index: int
    file_path: Path
    start_sec: float
    end_sec: float
    duration_sec: float


def get_ffmpeg_binary() -> Optional[str]:
    sys_ffmpeg = shutil.which("ffmpeg")
    if sys_ffmpeg:
        return sys_ffmpeg
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def ensure_wav_16k_mono(input_path: Path, work_dir: Path) -> Path:
    input_path = Path(input_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Media file not found: {input_path}")
    if input_path.stat().st_size == 0:
        raise ValueError(f"Media file is empty (0 bytes): {input_path}")

    if input_path.suffix.lower() == ".wav":
        try:
            with wave.open(str(input_path), "rb") as wf:
                if wf.getcomptype() == "NONE" and wf.getsampwidth() == 2:
                    return input_path
        except Exception:
            pass

    ffmpeg_bin = get_ffmpeg_binary()
    if not ffmpeg_bin:
        if input_path.suffix.lower() == ".wav":
            return input_path
        raise RuntimeError(
            f"Cannot decode '{input_path.name}': ffmpeg binary is required for {input_path.suffix} files."
        )

    work_dir.mkdir(parents=True, exist_ok=True)
    out_wav = work_dir / f"{input_path.stem}_16k.wav"
    cmd = [
        ffmpeg_bin,
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
        str(out_wav),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0 or not out_wav.exists():
        raise RuntimeError(f"Failed to extract/convert audio from '{input_path.name}': {proc.stderr[-400:]}")
    return out_wav


def inspect_wav_file(wav_path: Path) -> Dict[str, float]:
    with wave.open(str(wav_path), "rb") as wf:
        n_channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        framerate = wf.getframerate()
        n_frames = wf.getnframes()
        duration = n_frames / float(max(1, framerate))

        if n_frames == 0 or sample_width != 2:
            return {
                "duration_sec": round(duration, 3),
                "sample_rate": framerate,
                "channels": n_channels,
                "rms": 0.0,
                "silence_ratio": 1.0,
                "clipping_ratio": 0.0,
            }

        raw_bytes = wf.readframes(n_frames)
        samples = array.array("h")
        samples.frombytes(raw_bytes)
        if n_channels > 1:
            samples = samples[::n_channels]

        total_samples = len(samples)
        if total_samples == 0:
            return {
                "duration_sec": round(duration, 3),
                "sample_rate": framerate,
                "channels": n_channels,
                "rms": 0.0,
                "silence_ratio": 1.0,
                "clipping_ratio": 0.0,
            }

        window_size = max(1, int(framerate * 0.05))
        silent_windows = 0
        total_windows = 0
        clipped_samples = 0
        sum_sq = 0.0

        for idx in range(0, total_samples, window_size):
            win = samples[idx : idx + window_size]
            if not win:
                continue
            total_windows += 1
            win_sq = sum(float(s) * float(s) for s in win)
            sum_sq += win_sq
            win_rms = math.sqrt(win_sq / len(win))
            if win_rms < 180.0:
                silent_windows += 1
            clipped_samples += sum(1 for s in win if abs(s) >= 32700)

        overall_rms = math.sqrt(sum_sq / total_samples)
        return {
            "duration_sec": round(duration, 3),
            "sample_rate": framerate,
            "channels": n_channels,
            "rms": round(overall_rms, 2),
            "silence_ratio": round(silent_windows / max(1, total_windows), 3),
            "clipping_ratio": round(clipped_samples / max(1, total_samples), 5),
        }


def chunk_audio_file(
    input_path: Path,
    work_dir: Path,
    max_window_sec: float = MAX_ASR_WINDOW_SECONDS,
) -> List[AudioChunk]:
    work_dir.mkdir(parents=True, exist_ok=True)
    wav_path = ensure_wav_16k_mono(input_path, work_dir)

    with wave.open(str(wav_path), "rb") as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        n_frames = wf.getnframes()
        total_duration = n_frames / float(max(1, framerate))

        if total_duration <= max_window_sec:
            return [
                AudioChunk(
                    chunk_index=0,
                    file_path=wav_path,
                    start_sec=0.0,
                    end_sec=round(total_duration, 3),
                    duration_sec=round(total_duration, 3),
                )
            ]

        raw = wf.readframes(n_frames)

    frame_bytes = n_channels * sampwidth
    max_frames_per_chunk = int(max_window_sec * framerate)
    min_search_frames = int(max(5.0, max_window_sec - 6.0) * framerate)
    window_frames = max(1, int(0.1 * framerate))

    chunks: List[AudioChunk] = []
    cur_frame = 0
    chunk_idx = 0

    while cur_frame < n_frames:
        remaining = n_frames - cur_frame
        if remaining <= max_frames_per_chunk:
            end_frame = n_frames
        else:
            search_start = cur_frame + min_search_frames
            search_end = cur_frame + max_frames_per_chunk
            best_split = search_end
            lowest_energy = float("inf")

            if sampwidth == 2:
                for pos in range(search_start, search_end - window_frames, window_frames):
                    byte_start = pos * frame_bytes
                    byte_end = (pos + window_frames) * frame_bytes
                    segment = array.array("h")
                    segment.frombytes(raw[byte_start:byte_end])
                    if segment:
                        energy = sum(abs(x) for x in segment) / len(segment)
                        if energy < lowest_energy:
                            lowest_energy = energy
                            best_split = pos + (window_frames // 2)
            end_frame = best_split

        chunk_file = work_dir / f"{input_path.stem}_chunk_{chunk_idx:03d}.wav"
        byte_s = cur_frame * frame_bytes
        byte_e = end_frame * frame_bytes
        with wave.open(str(chunk_file), "wb") as out_wf:
            out_wf.setnchannels(n_channels)
            out_wf.setsampwidth(sampwidth)
            out_wf.setframerate(framerate)
            out_wf.writeframes(raw[byte_s:byte_e])

        start_sec = round(cur_frame / float(framerate), 3)
        end_sec = round(end_frame / float(framerate), 3)
        chunks.append(
            AudioChunk(
                chunk_index=chunk_idx,
                file_path=chunk_file,
                start_sec=start_sec,
                end_sec=end_sec,
                duration_sec=round(end_sec - start_sec, 3),
            )
        )
        cur_frame = end_frame
        chunk_idx += 1

    return chunks


def split_text_for_tts(text: str, max_chars: int = MAX_TTS_CHUNK_CHARS) -> List[str]:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return []
    if len(cleaned) <= max_chars:
        return [cleaned]

    sentence_pattern = re.compile(r"(?<=[.!?।॥\n])\s+")
    raw_sentences = sentence_pattern.split(text.strip())

    chunks: List[str] = []
    current = ""

    for sent in raw_sentences:
        sent = sent.strip()
        if not sent:
            continue
        if len(sent) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            clauses = re.split(r"(?<=[,;—:\-])\s+", sent)
            clause_buf = ""
            for cl in clauses:
                if len(cl) > max_chars:
                    if clause_buf:
                        chunks.append(clause_buf)
                        clause_buf = ""
                    words = cl.split(" ")
                    w_buf = ""
                    for w in words:
                        if not w_buf:
                            w_buf = w[:max_chars]
                        elif len(w_buf) + 1 + len(w) <= max_chars:
                            w_buf = f"{w_buf} {w}"
                        else:
                            chunks.append(w_buf)
                            w_buf = w[:max_chars]
                    if w_buf:
                        clause_buf = w_buf
                elif not clause_buf:
                    clause_buf = cl
                elif len(clause_buf) + 1 + len(cl) <= max_chars:
                    clause_buf = f"{clause_buf} {cl}"
                else:
                    chunks.append(clause_buf)
                    clause_buf = cl
            if clause_buf:
                chunks.append(clause_buf)
        elif not current:
            current = sent
        elif len(current) + 1 + len(sent) <= max_chars:
            current = f"{current} {sent}"
        else:
            chunks.append(current)
            current = sent

    if current:
        chunks.append(current)
    return chunks


def generate_wav_bytes_from_pcm(
    duration_sec: float = 1.5,
    sample_rate: int = 16000,
    freq_hz: float = 220.0,
) -> bytes:
    n_samples = max(1, int(duration_sec * sample_rate))
    samples = array.array("h")
    for i in range(n_samples):
        t = i / float(sample_rate)
        envelope = math.sin(math.pi * min(1.0, t / max(0.1, duration_sec)))
        val = int(6000 * envelope * math.sin(2.0 * math.pi * freq_hz * t))
        samples.append(val)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.tobytes())
    return buf.getvalue()


def stitch_audio_chunks(
    chunk_bytes_list: List[bytes],
    output_path: Path,
    pause_ms: int = 220,
) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not chunk_bytes_list:
        raise ValueError("No audio chunks provided to stitch.")

    with tempfile.TemporaryDirectory(prefix="bodhan_tts_") as tmpdir:
        tmp_path = Path(tmpdir)
        wav_files: List[Path] = []

        for i, b_data in enumerate(chunk_bytes_list):
            raw_file = tmp_path / f"part_{i:03d}.bin"
            raw_file.write_bytes(b_data)
            norm_wav = ensure_wav_16k_mono(raw_file, tmp_path)
            distinct_wav = tmp_path / f"norm_{i:03d}.wav"
            shutil.copyfile(norm_wav, distinct_wav)
            wav_files.append(distinct_wav)

        combined_wav = tmp_path / "combined.wav"
        framerate = 16000
        silence_samples = int((pause_ms / 1000.0) * framerate)
        silence_bytes = b"\x00\x00" * silence_samples

        with wave.open(str(combined_wav), "wb") as out_wf:
            out_wf.setnchannels(1)
            out_wf.setsampwidth(2)
            out_wf.setframerate(framerate)
            for idx, wf_path in enumerate(wav_files):
                with wave.open(str(wf_path), "rb") as in_wf:
                    out_wf.writeframes(in_wf.readframes(in_wf.getnframes()))
                if idx < len(wav_files) - 1 and pause_ms > 0:
                    out_wf.writeframes(silence_bytes)

        if output_path.suffix.lower() == ".mp3":
            ffmpeg_bin = get_ffmpeg_binary()
            if ffmpeg_bin:
                cmd = [
                    ffmpeg_bin,
                    "-y",
                    "-i",
                    str(combined_wav),
                    "-codec:a",
                    "libmp3lame",
                    "-qscale:a",
                    "4",
                    str(output_path),
                ]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                if proc.returncode == 0 and output_path.exists():
                    return output_path
            shutil.copyfile(combined_wav, output_path)
            return output_path
        else:
            shutil.copyfile(combined_wav, output_path)
            return output_path


def split_markdown_for_translation(
    text: str,
    max_chars: int = MAX_TRANSLATE_CHUNK_CHARS,
) -> List[str]:
    if len(text) <= max_chars:
        return [text] if text.strip() else []

    paragraphs = re.split(r"(\n\s*\n)", text)
    chunks: List[str] = []
    current = ""

    for part in paragraphs:
        if len(current) + len(part) <= max_chars:
            current += part
        else:
            if current.strip():
                chunks.append(current.strip())
            if len(part) <= max_chars:
                current = part
            else:
                lines = part.splitlines(keepends=True)
                line_buf = ""
                for ln in lines:
                    if len(line_buf) + len(ln) <= max_chars:
                        line_buf += ln
                    else:
                        if line_buf.strip():
                            chunks.append(line_buf.strip())
                        line_buf = ln
                current = line_buf

    if current.strip():
        chunks.append(current.strip())

    return chunks


def render_document_to_images(
    input_path: Path,
    work_dir: Path,
    dpi: int = 150,
    page_range: Optional[Tuple[int, int]] = None,
) -> List[Tuple[int, Path]]:
    input_path = Path(input_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Document or image path not found: {input_path}")

    work_dir.mkdir(parents=True, exist_ok=True)
    image_exts = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif"}

    if input_path.is_dir():
        imgs = sorted([p for p in input_path.iterdir() if p.is_file() and p.suffix.lower() in image_exts])
        if not imgs:
            pdfs = sorted([p for p in input_path.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"])
            if pdfs:
                return render_document_to_images(pdfs[0], work_dir, dpi=dpi, page_range=page_range)
            raise ValueError(f"No images or PDFs found inside directory: {input_path}")
        results = [(idx + 1, p) for idx, p in enumerate(imgs)]
        if page_range:
            s_p, e_p = page_range
            results = [(p_num, p) for p_num, p in results if s_p <= p_num <= e_p]
        return results

    suffix = input_path.suffix.lower()
    if suffix in image_exts:
        if input_path.stat().st_size == 0:
            raise ValueError(f"Image file is empty (0 bytes): {input_path}")
        return [(1, input_path)]

    if suffix == ".pdf":
        if input_path.stat().st_size == 0:
            raise ValueError(f"PDF file is empty (0 bytes): {input_path}")
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(input_path))
        n_pages = len(pdf)
        start_p = 1
        end_p = n_pages
        if page_range is not None:
            start_p = max(1, page_range[0])
            end_p = min(n_pages, page_range[1])

        scale = dpi / 72.0
        rendered: List[Tuple[int, Path]] = []
        for page_idx in range(start_p - 1, end_p):
            page = pdf[page_idx]
            bitmap = page.render(scale=scale)
            pil_image = bitmap.to_pil()
            out_img = work_dir / f"{input_path.stem}_page_{page_idx + 1:03d}.png"
            pil_image.save(str(out_img), format="PNG")
            rendered.append((page_idx + 1, out_img))
        return rendered

    raise ValueError(
        f"Unsupported document format '{suffix}' for OCR. Expected PDF or image ({', '.join(sorted(image_exts))})."
    )


def format_timestamp_srt(seconds: float) -> str:
    ms_total = max(0, int(round(seconds * 1000)))
    hours = ms_total // 3_600_000
    rem = ms_total % 3_600_000
    minutes = rem // 60_000
    rem = rem % 60_000
    secs = rem // 1000
    millis = rem % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def format_timestamp_vtt(seconds: float) -> str:
    return format_timestamp_srt(seconds).replace(",", ".")


def build_subtitle_content(
    segments: List[Dict[str, object]],
    fmt: str = "srt",
) -> str:
    fmt_clean = fmt.lower().strip().lstrip(".")
    lines: List[str] = []
    if fmt_clean == "vtt":
        lines.append("WEBVTT\n")

    valid_idx = 1
    for seg in segments:
        text = str(seg.get("text", "")).strip()
        if not text:
            continue
        start_s = float(seg.get("start", 0.0))
        end_s = float(seg.get("end", start_s + 3.0))
        if end_s <= start_s:
            end_s = start_s + 2.5

        if fmt_clean == "vtt":
            lines.append(f"{valid_idx}")
            lines.append(f"{format_timestamp_vtt(start_s)} --> {format_timestamp_vtt(end_s)}")
            lines.append(text)
            lines.append("")
        else:
            lines.append(f"{valid_idx}")
            lines.append(f"{format_timestamp_srt(start_s)} --> {format_timestamp_srt(end_s)}")
            lines.append(text)
            lines.append("")
        valid_idx += 1

    return "\n".join(lines).strip() + "\n"
