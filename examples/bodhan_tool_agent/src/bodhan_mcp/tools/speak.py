"""
Synthesize speech using Indic-Speak.

Long text is split at sentence boundaries (≤500 chars per chunk) and each
chunk is synthesized concurrently. The resulting MP3 segments are stitched
with pydub. After stitching, duration is sanity-checked against the input
character count to detect truncation or runaway repetition.
"""
from __future__ import annotations

import asyncio
import io
import re
import uuid
from pathlib import Path

from pydub import AudioSegment

from ..clients.bodhan_client import call_speak

_MAX_CHARS = 500
# Sentence-ending punctuation including Devanagari danda (।) and double danda (॥)
_SENTENCE_END = re.compile(r"(?<=[।॥.!?])\s+")

# Heuristic: average spoken chars-per-second for most Indic languages
_CHARS_PER_SECOND_APPROX = 10.0
# Flag as suspicious if duration differs from expected by more than this ratio
_DURATION_TOLERANCE = 2.5


def _split_sentences(text: str, max_chars: int) -> list[str]:
    """Split text at sentence boundaries, keeping chunks under max_chars."""
    sentences = _SENTENCE_END.split(text.strip())
    chunks: list[str] = []
    current = ""
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        if not current:
            current = sent
        elif len(current) + 1 + len(sent) <= max_chars:
            current += " " + sent
        else:
            chunks.append(current)
            current = sent
    if current:
        chunks.append(current)
    return chunks or [text]


async def synthesize_speech(
    text: str,
    language: str,
    voice: str,
    out_dir: Path,
) -> dict:
    """Synthesize speech for text in the given language and voice.

    Args:
        text:     Text to speak. Any length; chunked internally.
        language: BCP-47 language code.
        voice:    Voice ID from the Indic-Speak library.
        out_dir:  Directory to write the output MP3 file.

    Returns:
        {
          "audio_path": str,
          "duration_seconds": float,
          "cost_inr": float,
          "warning": str | None,   # set if duration looks suspicious
        }
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    chunks = _split_sentences(text, _MAX_CHARS)
    audio_parts = await asyncio.gather(
        *[call_speak(chunk, language, voice) for chunk in chunks]
    )

    combined = AudioSegment.empty()
    total_cost = 0.0
    for audio_bytes, cost in audio_parts:
        segment = AudioSegment.from_file(io.BytesIO(audio_bytes))
        combined += segment
        total_cost += cost

    out_path = out_dir / f"speech_{uuid.uuid4().hex[:8]}.wav"
    combined.export(str(out_path), format="wav")
    duration_s = len(combined) / 1000.0

    # Sanity-check: flag if Indic-Speak repeated or omitted content
    expected_s = len(text) / _CHARS_PER_SECOND_APPROX
    warning: str | None = None
    if duration_s > expected_s * _DURATION_TOLERANCE:
        ratio = duration_s / expected_s
        warning = (
            f"Audio duration ({duration_s:.1f}s) is {ratio:.1f}x expected - "
            "possible repetition."
        )
    elif duration_s < expected_s / _DURATION_TOLERANCE:
        warning = (
            f"Audio duration ({duration_s:.1f}s) is much shorter than expected "
            f"({expected_s:.1f}s) - possible truncation."
        )

    return {
        "audio_path": str(out_path),
        "duration_seconds": round(duration_s, 2),
        "cost_inr": round(total_cost, 4),
        "warning": warning,
    }
