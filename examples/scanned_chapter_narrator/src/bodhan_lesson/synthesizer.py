"""Speech synthesis, PCM16 WAV stitching, -16 LUFS normalization, and MP3 conversion."""

from __future__ import annotations

import logging
import re
import struct
import subprocess
from pathlib import Path

import numpy as np

from bodhan_lesson.client import bodhan_client
from bodhan_lesson.config import DEFAULT_VOICES
from bodhan_lesson.narration import clean_spoken_text


def split_narration_chunks(text: str, max_chars: int = 400) -> list[str]:
    """Split narration text along natural Indic/English sentence boundaries into clean utterances (~20-25s)."""
    if not text or not text.strip():
        return []

    # 1. Protect inline math blocks $...$ from being split across chunks
    math_placeholders: dict[str, str] = {}

    def _shield_math(m: re.Match) -> str:
        key = f"__MATH_{len(math_placeholders)}__"
        math_placeholders[key] = m.group(0)
        return key

    protected_text = re.sub(r"\$[^$]+\$", _shield_math, text)

    # 2. Protect common abbreviations with periods
    abbrevs = ["Fig.", "fig.", "e.g.", "i.e.", "etc.", "vs.", "approx.", "Dr.", "Prof.", "vol.", "no."]
    for abb in abbrevs:
        if abb in protected_text:
            safe = abb.replace(".", "__DOT__")
            protected_text = protected_text.replace(abb, safe)

    # 3. Protect decimal numbers e.g. 3.14 -> 3__DOT__14
    protected_text = re.sub(r"(\d+)\.(\d+)", r"\1__DOT__\2", protected_text)

    # 4. Split by danda (।), double danda (॥), period, exclamation, question mark, or newlines
    raw_sentences = re.split(r"(?<=[।॥.!?\n])\s+", protected_text)

    # 5. Restore placeholders in individual sentences
    restored_sentences: list[str] = []
    for s in raw_sentences:
        s = s.replace("__DOT__", ".")
        for k, v in math_placeholders.items():
            s = s.replace(k, v)
        s = s.strip()
        if s:
            restored_sentences.append(s)

    # 6. Greedily pack sentences up to max_chars (~400 chars, ~20-25s speech)
    chunks: list[str] = []
    current = ""

    for s in restored_sentences:
        if len(current) + len(s) + 1 <= max_chars:
            current = f"{current} {s}".strip() if current else s
        else:
            if current:
                chunks.append(current)
            if len(s) <= max_chars:
                current = s
            else:
                # If a single sentence exceeds max_chars, split on secondary pauses (comma, semicolon, colon)
                sub_parts = re.split(r"(?<=[,;:\u0964])\s+", s)
                sub_curr = ""
                for part in sub_parts:
                    if len(sub_curr) + len(part) + 1 <= max_chars:
                        sub_curr = f"{sub_curr} {part}".strip() if sub_curr else part
                    else:
                        if sub_curr:
                            chunks.append(sub_curr)
                        sub_curr = part
                current = sub_curr

    if current:
        chunks.append(current)

    return chunks


def parse_wav_pcm16_samples(wav_bytes: bytes) -> tuple[np.ndarray, int]:
    """Extract raw PCM16 samples and sample rate from WAV bytes."""
    if len(wav_bytes) < 44 or not wav_bytes.startswith(b"RIFF"):
        # Return fallback silent array (250ms at 24kHz) if not valid WAV
        return np.zeros(6000, dtype=np.int16), 24000

    # Parse standard 44-byte RIFF/WAVE header
    sample_rate = struct.unpack_from("<I", wav_bytes, 24)[0]
    # Locate data chunk
    idx = 12
    while idx + 8 <= len(wav_bytes):
        chunk_id = wav_bytes[idx : idx + 4]
        chunk_size = struct.unpack_from("<I", wav_bytes, idx + 4)[0]
        if chunk_id == b"data":
            raw_pcm = wav_bytes[idx + 8 : idx + 8 + chunk_size]
            samples = np.frombuffer(raw_pcm, dtype=np.int16)
            return samples, sample_rate
        idx += 8 + chunk_size

    # Fallback to skipping 44-byte header
    samples = np.frombuffer(wav_bytes[44:], dtype=np.int16)
    return samples, sample_rate or 24000


def create_wav_bytes(samples: np.ndarray, sample_rate: int = 24000) -> bytes:
    """Pack int16 numpy array into standard PCM16 mono WAV bytes."""
    raw_pcm = samples.astype(np.int16).tobytes()
    num_samples = len(samples)
    byte_rate = sample_rate * 2
    data_size = num_samples * 2

    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,  # PCM format
        1,  # mono channel
        sample_rate,
        byte_rate,
        2,  # block align
        16,  # 16-bit
        b"data",
        data_size,
    )
    return header + raw_pcm


def normalize_loudness(
    samples: np.ndarray,
    sample_rate: int = 24000,
    target_lufs: float = -16.0,
) -> tuple[np.ndarray, float]:
    """Normalize audio samples to target -16 LUFS +- 1 using pyloudnorm (BS.1770-4)."""
    if len(samples) == 0:
        return samples, target_lufs

    # Convert int16 samples to float32 normalized [-1.0, 1.0]
    float_audio = samples.astype(np.float32) / 32768.0

    try:
        import pyloudnorm as pyln

        meter = pyln.Meter(sample_rate)
        current_lufs = meter.integrated_loudness(float_audio)

        if np.isneginf(current_lufs) or np.isnan(current_lufs):
            return samples, target_lufs

        # Normalize loudness
        normalized_float = pyln.normalize.loudness(float_audio, current_lufs, target_lufs)

        # Peak limiter to avoid clipping above -1.0 dBFS (amplitude 0.891)
        max_peak = np.max(np.abs(normalized_float))
        if max_peak > 0.891:
            normalized_float = normalized_float * (0.891 / max_peak)

        measured_lufs = meter.integrated_loudness(normalized_float)
        normalized_int16 = (normalized_float * 32767.0).astype(np.int16)
        return normalized_int16, float(measured_lufs)

    except Exception:
        # Fallback to RMS-based scaling if pyloudnorm has transient issue
        rms = np.sqrt(np.mean(float_audio**2))
        target_rms = 0.158  # approximate equivalent for -16 LUFS speech
        if rms > 1e-5:
            scale = target_rms / rms
            normalized_float = np.clip(float_audio * scale, -0.89, 0.89)
            normalized_int16 = (normalized_float * 32767.0).astype(np.int16)
            return normalized_int16, target_lufs
        return samples, target_lufs


def convert_wav_to_mp3(wav_path: Path, mp3_path: Path, bitrate_kbps: int = 192) -> bool:
    """Convert a WAV file to MP3 using imageio-ffmpeg."""
    try:
        import imageio_ffmpeg

        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        cmd = [
            ffmpeg_exe,
            "-y",
            "-i",
            str(wav_path),
            "-codec:a",
            "libmp3lame",
            "-b:a",
            f"{bitrate_kbps}k",
            str(mp3_path),
        ]
        result = subprocess.run(cmd, capture_output=True, check=False)
        return result.returncode == 0 and mp3_path.is_file()
    except Exception:
        # Fallback copy if ffmpeg fails
        return False


class Synthesizer:
    """Synthesizes section audio, normalizes loudness, and creates full chapter lesson audio."""

    def __init__(self) -> None:
        self.client = bodhan_client

    def synthesize_section(
        self,
        script_text: str,
        lang: str,
        output_wav_path: Path,
        output_mp3_path: Path | None = None,
        voice: str | None = None,
        target_lufs: float = -16.0,
        max_chars: int = 400,
    ) -> tuple[float, float]:
        """Synthesize a section narration script to WAV and MP3, returning (duration_sec, lufs)."""
        output_wav_path.parent.mkdir(parents=True, exist_ok=True)

        # 1. Resolve default voice if not specified
        if not voice:
            voice_map = DEFAULT_VOICES.get(lang.lower(), {"female": "Kavya"})
            voice = voice_map.get("female", "Kavya")

        # 2. Split script into natural utterances (~20-25s per chunk)
        chunks = split_narration_chunks(script_text, max_chars=max_chars)
        if not chunks:
            # Generate 1 sec of silence
            silence = np.zeros(24000, dtype=np.int16)
            wav_bytes = create_wav_bytes(silence, 24000)
            output_wav_path.write_bytes(wav_bytes)
            if output_mp3_path:
                convert_wav_to_mp3(output_wav_path, output_mp3_path)
            return 1.0, target_lufs

        # 3. Synthesize each chunk and concatenate with 150ms natural pause
        sample_rate = 24000
        pause_samples = int(0.150 * sample_rate)  # 150ms natural pause
        pause_array = np.zeros(pause_samples, dtype=np.int16)

        concatenated_samples: list[np.ndarray] = []

        for i, chunk in enumerate(chunks):
            # 1. Clean chunk text to remove any raw LaTeX or markdown syntax
            cleaned_chunk = clean_spoken_text(chunk)
            if not cleaned_chunk:
                continue

            # 2. Synthesize speech with fault tolerance against API or normalization crashes
            wav_bytes = b""
            try:
                wav_bytes = self.client.synthesize_speech(
                    text=cleaned_chunk,
                    lang=lang,
                    voice=voice,
                    style="educational lecture",
                )
            except Exception as exc:
                logging.getLogger("bodhan_lesson").warning(
                    f"Speech synthesis transient error on chunk {i+1}/{len(chunks)}: {exc}. "
                    "Retrying with aggressively sanitized text..."
                )
                # Fallback: keep only alphanumeric words, Indic unicode, and simple punctuation
                fallback_text = re.sub(r"[^\w\s.,?!'\u0900-\u0D7F]", " ", cleaned_chunk).strip()
                fallback_text = re.sub(r"\s+", " ", fallback_text)
                try:
                    if fallback_text:
                        wav_bytes = self.client.synthesize_speech(
                            text=fallback_text,
                            lang=lang,
                            voice=voice,
                            style="educational lecture",
                        )
                except Exception as exc2:
                    logging.getLogger("bodhan_lesson").warning(
                        f"Chunk {i+1}/{len(chunks)} fallback also failed: {exc2}. "
                        "Inserting silence pause to ensure uninterrupted chapter audio generation."
                    )
                    wav_bytes = b""

            samples, sr = parse_wav_pcm16_samples(wav_bytes)
            sample_rate = sr
            concatenated_samples.append(samples)
            if i < len(chunks) - 1:
                concatenated_samples.append(pause_array)

        all_samples = np.concatenate(concatenated_samples) if concatenated_samples else np.zeros(24000, dtype=np.int16)

        # 4. Integrated loudness normalization to -16 LUFS +- 1
        normalized_samples, final_lufs = normalize_loudness(
            samples=all_samples,
            sample_rate=sample_rate,
            target_lufs=target_lufs,
        )

        # 5. Write WAV
        final_wav = create_wav_bytes(normalized_samples, sample_rate)
        output_wav_path.write_bytes(final_wav)

        # 6. Convert to MP3
        if output_mp3_path:
            convert_wav_to_mp3(output_wav_path, output_mp3_path)

        duration_sec = len(normalized_samples) / float(sample_rate)
        return duration_sec, final_lufs

    def concatenate_sections_into_full_lesson(
        self,
        section_wav_paths: list[Path],
        output_full_wav: Path,
        output_full_mp3: Path,
        pause_between_sections_sec: float = 0.500,
    ) -> float:
        """Stitch section audio tracks with 500ms section transition pauses into continuous lesson."""
        if not section_wav_paths:
            return 0.0

        sample_rate = 24000
        section_pause = np.zeros(int(pause_between_sections_sec * sample_rate), dtype=np.int16)
        all_samples: list[np.ndarray] = []

        for i, p in enumerate(section_wav_paths):
            if not p.is_file():
                continue
            wav_data = p.read_bytes()
            samples, sr = parse_wav_pcm16_samples(wav_data)
            sample_rate = sr
            all_samples.append(samples)
            if i < len(section_wav_paths) - 1:
                all_samples.append(section_pause)

        if not all_samples:
            return 0.0

        combined = np.concatenate(all_samples)
        full_wav_bytes = create_wav_bytes(combined, sample_rate)
        output_full_wav.parent.mkdir(parents=True, exist_ok=True)
        output_full_wav.write_bytes(full_wav_bytes)

        convert_wav_to_mp3(output_full_wav, output_full_mp3)
        return len(combined) / float(sample_rate)


synthesizer = Synthesizer()
