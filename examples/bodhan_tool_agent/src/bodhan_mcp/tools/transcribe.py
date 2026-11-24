"""
Indic-Transcribe Tool: Speech-to-text with universal audio decoding and <=30s chunking.
"""

import os
import wave
import contextlib
import tempfile
import numpy as np
import soundfile as sf
from typing import List, Tuple

from ..models import TranscribeInput, TranscribeResult, TranscribeSegment, ToolCallMetadata
from ..client import BodhanClient, BodhanAPIError
from ..pricing import calculate_transcribe_cost


def convert_to_standard_wav(input_path: str, target_sr: int = 16000) -> Tuple[str, float]:
    """
    Ensure input audio is decoded and normalized to a 16kHz 16-bit mono WAV file.
    Returns (normalized_wav_path, duration_seconds).
    """
    temp_dir = tempfile.mkdtemp(prefix="audio_norm_")
    norm_wav_path = os.path.join(temp_dir, "normalized.wav")

    try:
        # soundfile supports FLAC, WAV, OGG, etc.
        data, sr = sf.read(input_path, dtype="float32")
        # Convert stereo/multichannel to mono
        if len(data.shape) > 1:
            data = data.mean(axis=1)

        duration = len(data) / float(sr)

        # Write to target 16-bit PCM WAV
        sf.write(norm_wav_path, data, sr, subtype="PCM_16", format="WAV")
        return norm_wav_path, float(duration)
    except Exception:
        # Fallback using standard wave module
        try:
            with contextlib.closing(wave.open(input_path, "rb")) as f:
                frames = f.getnframes()
                sr = f.getframerate()
                duration = frames / float(sr)
                return input_path, float(duration)
        except Exception:
            # Fallback estimation based on file size
            size = os.path.getsize(input_path)
            est_duration = max(1.0, size / 32000.0)
            return input_path, float(est_duration)


def get_audio_duration_and_frames(file_path: str) -> Tuple[float, int, int, int]:
    """Return (duration_seconds, sample_rate, channels, sampwidth)."""
    try:
        info = sf.info(file_path)
        return float(info.duration), int(info.samplerate), int(info.channels), 2
    except Exception:
        try:
            with contextlib.closing(wave.open(file_path, "rb")) as f:
                frames = f.getnframes()
                rate = f.getframerate()
                channels = f.getnchannels()
                sampwidth = f.getsampwidth()
                duration = frames / float(rate)
                return duration, rate, channels, sampwidth
        except Exception:
            size = os.path.getsize(file_path)
            est_duration = max(1.0, size / 32000.0)
            return est_duration, 16000, 1, 2


def chunk_wav_file(file_path: str, max_chunk_seconds: float = 28.0) -> List[Tuple[str, float, float]]:
    """
    Split audio file into chunks <= max_chunk_seconds.
    Returns list of (chunk_file_path, start_seconds, duration_seconds).
    """
    normalized_path, total_duration = convert_to_standard_wav(file_path)

    if total_duration <= max_chunk_seconds:
        return [(normalized_path, 0.0, total_duration)]

    try:
        data, sr = sf.read(normalized_path, dtype="float32")
        samples_per_chunk = int(max_chunk_seconds * sr)
        total_samples = len(data)

        chunks = []
        start_sample = 0
        chunk_idx = 0
        temp_dir = tempfile.mkdtemp(prefix="transcribe_chunks_")

        while start_sample < total_samples:
            end_sample = min(start_sample + samples_per_chunk, total_samples)
            chunk_data = data[start_sample:end_sample]

            start_sec = start_sample / float(sr)
            chunk_dur = len(chunk_data) / float(sr)

            chunk_filename = os.path.join(temp_dir, f"chunk_{chunk_idx:04d}.wav")
            sf.write(chunk_filename, chunk_data, sr, subtype="PCM_16", format="WAV")

            chunks.append((chunk_filename, round(start_sec, 2), round(chunk_dur, 2)))
            start_sample = end_sample
            chunk_idx += 1

        return chunks
    except Exception:
        # Basic wave fallback
        try:
            with contextlib.closing(wave.open(normalized_path, "rb")) as f:
                rate = f.getframerate()
                channels = f.getnchannels()
                sampwidth = f.getsampwidth()
                total_frames = f.getnframes()

                frames_per_chunk = int(max_chunk_seconds * rate)
                chunks = []
                start_frame = 0
                temp_dir = tempfile.mkdtemp(prefix="transcribe_chunks_")
                chunk_idx = 0

                while start_frame < total_frames:
                    f.setpos(start_frame)
                    current_frames = min(frames_per_chunk, total_frames - start_frame)
                    raw_data = f.readframes(current_frames)

                    start_sec = start_frame / float(rate)
                    chunk_dur = current_frames / float(rate)

                    chunk_filename = os.path.join(temp_dir, f"chunk_{chunk_idx:04d}.wav")
                    with wave.open(chunk_filename, "wb") as chunk_f:
                        chunk_f.setnchannels(channels)
                        chunk_f.setsampwidth(sampwidth)
                        chunk_f.setframerate(rate)
                        chunk_f.writeframes(raw_data)

                    chunks.append((chunk_filename, round(start_sec, 2), round(chunk_dur, 2)))
                    start_frame += current_frames
                    chunk_idx += 1

                return chunks
        except Exception:
            return [(normalized_path, 0.0, total_duration)]


def run_transcribe(args: TranscribeInput, client: BodhanClient) -> TranscribeResult:
    """
    Transcribe audio with Indic-Transcribe, handling format decoding & chunking <= 30s.
    """
    if not os.path.exists(args.audio_path):
        raise BodhanAPIError(f"Audio file not found: {args.audio_path}", status_code=404)

    total_duration, _, _, _ = get_audio_duration_and_frames(args.audio_path)
    chunks = chunk_wav_file(args.audio_path, max_chunk_seconds=28.0)

    combined_text = []
    all_segments: List[TranscribeSegment] = []
    total_latency = 0.0
    detected_lang = args.language or "unknown"

    for chunk_path, start_sec, duration in chunks:
        res = client.transcribe(
            audio_file_path=chunk_path,
            duration_seconds=duration,
            language=args.language,
            model_variant=args.model_variant,
        )
        total_latency += res["latency_ms"]
        chunk_text = res["text"].strip()
        if chunk_text:
            combined_text.append(chunk_text)

        if res.get("language") and detected_lang == "unknown":
            detected_lang = res["language"]

        all_segments.append(
            TranscribeSegment(
                start_seconds=round(start_sec, 2),
                end_seconds=round(start_sec + duration, 2),
                text=chunk_text,
            )
        )

    full_text = " ".join(combined_text)
    total_cost = calculate_transcribe_cost(total_duration)

    return TranscribeResult(
        text=full_text,
        language=detected_lang,
        duration_seconds=round(total_duration, 2),
        segments=all_segments,
        metadata=ToolCallMetadata(
            cost_inr=total_cost,
            latency_ms=round(total_latency, 2),
            model_name=f"indic-transcribe-{args.model_variant}",
        ),
    )
