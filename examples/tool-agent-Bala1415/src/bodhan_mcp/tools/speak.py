"""
Speak tool — text-to-speech using Bodhan Indic-Speak.

Handles:
- Input text length limits (chunking for long text)
- Voice selection by language
- Audio chunk concatenation
- Cost awareness (TTS is 60x more expensive than translation)
"""

from __future__ import annotations

import logging
import struct
from pathlib import Path
from typing import Any

from bodhan_mcp.client import BodhanClient, BodhanAPIError, DEFAULT_VOICES

logger = logging.getLogger(__name__)

# Indic-Speak recommended max input length per call
MAX_CHARS_PER_CALL = 2000  # Conservative limit to avoid truncation/repetition

# Supported voices (subset — the full list has 45)
VOICE_CATALOG: dict[str, dict[str, str]] = {
    "Kavya": {"gender": "female", "languages": "hi,ta,te,kn,ml,gu,bn,en"},
    "Kalpana": {"gender": "female", "languages": "hi,mr"},
    "Prabhat": {"gender": "male", "languages": "hi,en"},
    "Ishita": {"gender": "female", "languages": "bn"},
    "Debashis": {"gender": "male", "languages": "bn"},
    "Sourav": {"gender": "male", "languages": "bn"},
}


def _chunk_speak_text(text: str, max_chars: int = MAX_CHARS_PER_CALL) -> list[str]:
    """
    Split text into chunks suitable for TTS, breaking at sentence boundaries.
    """
    if len(text) <= max_chars:
        return [text]

    import re
    # Split on sentence endings (., !, ?, ।)
    sentences = re.split(r"(?<=[.!?।])\s+", text)
    chunks: list[str] = []
    current = ""

    for sent in sentences:
        if len(current) + len(sent) + 1 > max_chars:
            if current:
                chunks.append(current.strip())
            current = sent
        else:
            current = (current + " " + sent).strip() if current else sent

    if current.strip():
        chunks.append(current.strip())

    return chunks


def _concatenate_wav_files(wav_paths: list[str], output_path: str) -> str:
    """
    Concatenate multiple WAV files into one (simple PCM concatenation).
    Assumes all files have the same format (sample rate, channels, bit depth).
    """
    if len(wav_paths) == 1:
        # Just copy/rename
        import shutil
        shutil.copy2(wav_paths[0], output_path)
        return output_path

    all_data = bytearray()
    sample_rate = 16000
    channels = 1
    bits_per_sample = 16

    for wp in wav_paths:
        with open(wp, "rb") as f:
            content = f.read()
            # Find data chunk
            idx = content.find(b"data")
            if idx >= 0:
                data_size = struct.unpack_from("<I", content, idx + 4)[0]
                audio_data = content[idx + 8: idx + 8 + data_size]
                all_data.extend(audio_data)

                # Read format from first file
                if wp == wav_paths[0]:
                    fmt_idx = content.find(b"fmt ")
                    if fmt_idx >= 0:
                        channels = struct.unpack_from("<H", content, fmt_idx + 10)[0]
                        sample_rate = struct.unpack_from("<I", content, fmt_idx + 12)[0]
                        bits_per_sample = struct.unpack_from("<H", content, fmt_idx + 22)[0]

    # Write combined WAV
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    data_size = len(all_data)

    with open(output_path, "wb") as f:
        # RIFF header
        f.write(b"RIFF")
        f.write(struct.pack("<I", 36 + data_size))
        f.write(b"WAVE")
        # fmt chunk
        f.write(b"fmt ")
        f.write(struct.pack("<I", 16))
        f.write(struct.pack("<H", 1))  # PCM
        f.write(struct.pack("<H", channels))
        f.write(struct.pack("<I", sample_rate))
        f.write(struct.pack("<I", byte_rate))
        f.write(struct.pack("<H", block_align))
        f.write(struct.pack("<H", bits_per_sample))
        # data chunk
        f.write(b"data")
        f.write(struct.pack("<I", data_size))
        f.write(all_data)

    return output_path


async def synthesize_speech(
    client: BodhanClient,
    text: str,
    language: str,
    voice: str | None = None,
    output_path: str = "output.wav",
    style: str = "default",
) -> dict[str, Any]:
    """
    Synthesize speech from text using Bodhan Indic-Speak.

    Automatically chunks long text and concatenates the audio output.
    All generated audio is labeled as AI-generated.

    Args:
        client: BodhanClient instance.
        text: Text to convert to speech.
        language: ISO 639-1 language code (e.g., 'hi', 'ta', 'bn').
        voice: Voice name from the Indic-Speak library.
               If None, auto-selects based on language.
               Use list_voices() to see available options.
        output_path: Where to save the final audio file.
        style: Speaking style ('default', 'news', 'conversational').

    Returns:
        dict with:
            - output_path: Path to the generated audio file
            - voice: Voice used
            - language: Language code
            - total_characters: Total characters synthesized
            - num_chunks: Number of TTS calls made
            - cost_inr: Total cost of this synthesis
            - note: AI-generation disclosure

    Raises:
        BodhanAPIError: If the API call fails.
        ValueError: If text is empty.
    """
    if not text.strip():
        raise ValueError("Cannot synthesize empty text")

    voice = voice or DEFAULT_VOICES.get(language, "Kavya")
    chunks = _chunk_speak_text(text)
    total_chars = len(text)

    logger.info(
        f"Synthesizing {total_chars} chars in {len(chunks)} chunk(s), "
        f"voice={voice}, lang={language}"
    )

    chunk_paths: list[str] = []
    total_cost = 0.0

    out_dir = Path(output_path).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    for i, chunk_text in enumerate(chunks):
        chunk_out = str(out_dir / f"_tts_chunk_{i:04d}.wav")
        result = await client.speak(
            text=chunk_text,
            language=language,
            voice=voice,
            output_path=chunk_out,
            style=style,
        )
        chunk_paths.append(chunk_out)
        total_cost += result.get("cost_inr", 0)

    # Concatenate chunks into final file
    if len(chunk_paths) > 1:
        _concatenate_wav_files(chunk_paths, output_path)
        # Clean up chunk files
        for cp in chunk_paths:
            try:
                Path(cp).unlink()
            except OSError:
                pass
    elif chunk_paths:
        import shutil
        shutil.move(chunk_paths[0], output_path)

    return {
        "output_path": str(Path(output_path).resolve()),
        "voice": voice,
        "language": language,
        "total_characters": total_chars,
        "num_chunks": len(chunks),
        "cost_inr": round(total_cost, 4),
        "note": "AI-generated audio using Bodhan Indic-Speak. Not a real human voice.",
    }


def list_voices() -> dict[str, dict[str, str]]:
    """Return the available voice catalog with metadata."""
    return VOICE_CATALOG
