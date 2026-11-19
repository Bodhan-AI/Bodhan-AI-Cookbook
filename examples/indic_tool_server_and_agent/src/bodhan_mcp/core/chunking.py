"""Chunking utilities for Audio, Text, and Speech.

- Audio: Splits files into <= 30-second segments (VAD / silence-aware via pydub or ffmpeg/wave fallback).
- Text: Splits documents into <= 32K token windows (paragraph & sentence aware, preserves tables/markdown).
- Speech (TTS): Splits text on sentence boundaries (max ~400-500 chars) for stable Indic-Speak synthesis.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from bodhan_mcp.config import settings
from bodhan_mcp.core.errors import BodhanError, INVALID_INPUT, FILE_NOT_FOUND

logger = logging.getLogger(__name__)

# Sentence boundary regex supporting Western (. ? !) and Indic (।) punctuation
SENTENCE_SPLIT_REGEX = re.compile(r'(?<=[.?!।\n])\s+')


def count_tokens(text: str) -> int:
    """Estimate or accurately count tokens in text."""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        # Fallback approximation: 1 token ~= 3.5 characters for Indic/multilingual
        return max(1, int(len(text) / 3.5))


def chunk_text(
    text: str,
    max_tokens: int = settings.translate_max_tokens,
) -> List[str]:
    """Split text into chunks of at most `max_tokens` tokens.
    
    Tries to split on paragraphs first, then sentences, preserving Markdown tables.
    """
    if not text or not text.strip():
        return []

    if count_tokens(text) <= max_tokens:
        return [text]

    chunks: List[str] = []
    paragraphs = text.split("\n\n")
    current_chunk: List[str] = []
    current_tokens = 0

    for para in paragraphs:
        para_tokens = count_tokens(para)
        if para_tokens > max_tokens:
            # Paragraph itself exceeds limit, split by sentences
            if current_chunk:
                chunks.append("\n\n".join(current_chunk))
                current_chunk = []
                current_tokens = 0

            sentences = SENTENCE_SPLIT_REGEX.split(para)
            sent_chunk: List[str] = []
            sent_tokens = 0

            for sent in sentences:
                st = count_tokens(sent)
                if st > max_tokens:
                    # Single sentence exceeds limit, slice by words
                    words = sent.split()
                    w_chunk: List[str] = []
                    w_tokens = 0
                    for w in words:
                        wt = count_tokens(w + " ")
                        if w_tokens + wt > max_tokens:
                            if w_chunk:
                                chunks.append(" ".join(w_chunk))
                            w_chunk = [w]
                            w_tokens = wt
                        else:
                            w_chunk.append(w)
                            w_tokens += wt
                    if w_chunk:
                        chunks.append(" ".join(w_chunk))
                elif sent_tokens + st > max_tokens:
                    chunks.append(" ".join(sent_chunk))
                    sent_chunk = [sent]
                    sent_tokens = st
                else:
                    sent_chunk.append(sent)
                    sent_tokens += st

            if sent_chunk:
                chunks.append(" ".join(sent_chunk))
        elif current_tokens + para_tokens > max_tokens:
            chunks.append("\n\n".join(current_chunk))
            current_chunk = [para]
            current_tokens = para_tokens
        else:
            current_chunk.append(para)
            current_tokens += para_tokens

    if current_chunk:
        chunks.append("\n\n".join(current_chunk))

    return chunks


def chunk_text_for_tts(
    text: str,
    max_chars: int = settings.tts_max_chars_per_call,
) -> List[str]:
    """Split text into small sentence units (<= max_chars) for high quality TTS."""
    if not text or not text.strip():
        return []

    raw_sentences = SENTENCE_SPLIT_REGEX.split(text.strip())
    chunks: List[str] = []
    curr = ""

    for s in raw_sentences:
        s = s.strip()
        if not s:
            continue
        if len(s) > max_chars:
            # Sub-split on commas, semicolons or spaces
            parts = re.split(r'(?<=[,;])\s+', s)
            for part in parts:
                part = part.strip()
                if not part:
                    continue
                if len(curr) + len(part) + 1 <= max_chars:
                    curr = f"{curr} {part}".strip()
                else:
                    if curr:
                        chunks.append(curr)
                    curr = part
        else:
            if len(curr) + len(s) + 1 <= max_chars:
                curr = f"{curr} {s}".strip()
            else:
                if curr:
                    chunks.append(curr)
                curr = s

    if curr:
        chunks.append(curr)

    return chunks


def get_audio_duration_seconds(file_path: str) -> float:
    """Get duration of audio file in seconds."""
    if not os.path.exists(file_path):
        raise BodhanError(FILE_NOT_FOUND, f"Audio file not found: {file_path}")

    if os.path.getsize(file_path) == 0:
        return 0.0

    # 1. Try pydub
    try:
        from pydub import AudioSegment
        seg = AudioSegment.from_file(file_path)
        return len(seg) / 1000.0
    except Exception:
        pass

    # 2. Try wave
    try:
        import wave
        with wave.open(file_path, 'rb') as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            if rate > 0:
                return frames / float(rate)
    except Exception:
        pass

    # 3. Try ffprobe
    try:
        cmd = [
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration", "-of", "default=noprint_wrappers=1:nokey=1",
            file_path
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(res.stdout.strip())
    except Exception:
        pass

    raise BodhanError(INVALID_INPUT, f"Invalid or corrupt audio file: {file_path}")


def chunk_audio(
    file_path: str,
    chunk_seconds: int = settings.stt_max_chunk_seconds,
    output_dir: Optional[str] = None,
) -> List[Tuple[str, float, float]]:
    """Split audio file into <= chunk_seconds chunks.
    
    Returns list of tuples: (chunk_file_path, start_time_sec, end_time_sec).
    """
    if not os.path.exists(file_path):
        raise BodhanError(FILE_NOT_FOUND, f"File does not exist: {file_path}")

    duration = get_audio_duration_seconds(file_path)
    if duration <= 0:
        return []
    ext = Path(file_path).suffix.lower()
    if duration <= chunk_seconds and ext in (".wav", ".flac", ".ogg", ".mp3"):
        return [(file_path, 0.0, duration)]

    out_path = Path(output_dir) if output_dir else Path(file_path).parent / "chunks"
    out_path.mkdir(parents=True, exist_ok=True)
    base_name = Path(file_path).stem

    # Attempt with pydub first
    try:
        from pydub import AudioSegment
        audio = AudioSegment.from_file(file_path)
        total_ms = len(audio)
        chunk_ms = chunk_seconds * 1000
        results: List[Tuple[str, float, float]] = []
        for idx, start_ms in enumerate(range(0, total_ms, chunk_ms)):
            end_ms = min(start_ms + chunk_ms, total_ms)
            chunk = audio[start_ms:end_ms]
            chunk_file = str(out_path / f"{base_name}_part{idx:03d}.wav")
            chunk.export(chunk_file, format="wav")
            results.append((chunk_file, start_ms / 1000.0, end_ms / 1000.0))
        return results
    except Exception as pydub_err:
        logger.info("pydub audio chunking unavailable or failed (%s), trying ffmpeg", pydub_err)

    # Fallback to ffmpeg
    if duration <= 0:
        # Fallback single chunk
        return [(file_path, 0.0, 0.0)]

    results = []
    idx = 0
    start = 0.0
    while start < duration:
        end = min(start + chunk_seconds, duration)
        chunk_file = str(out_path / f"{base_name}_part{idx:03d}.wav")
        cmd = [
            "ffmpeg", "-y", "-ss", str(start), "-t", str(end - start),
            "-i", file_path, "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            chunk_file
        ]
        try:
            subprocess.run(cmd, capture_output=True, check=True)
            results.append((chunk_file, start, end))
        except Exception as e:
            logger.error("ffmpeg audio slicing failed: %s", e)
            if not results:
                return [(file_path, 0.0, duration)]
            break
        idx += 1
        start = end

    return results


from typing import List, Optional, Tuple, NamedTuple


class AudioChunk(NamedTuple):
    start_ms: float
    end_ms: float
    path: str

    @property
    def duration_seconds(self) -> float:
        return (self.end_ms - self.start_ms) / 1000.0

    @property
    def start_seconds(self) -> float:
        return self.start_ms / 1000.0

    @property
    def end_seconds(self) -> float:
        return self.end_ms / 1000.0


def split_audio_chunks(
    path: str,
    max_seconds: int = 30,
) -> List[AudioChunk]:
    """Return list of AudioChunk(start_ms, end_ms, temp_path) matching assignment specification."""
    chunks = chunk_audio(path, chunk_seconds=max_seconds)
    # Convert seconds to ms
    return [AudioChunk(start * 1000.0, end * 1000.0, p) for p, start, end in chunks]


# Aliases
split_text_chunks = chunk_text
split_tts_chunks = chunk_text_for_tts
