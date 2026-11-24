"""indic-transcribe: POST /v1/audio/transcriptions (multipart, <=30 s per request)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from app.services.audio.processing import AudioSegment, prepare_segments
from app.services.bodhan.capabilities import (
    TRANSCRIBE_MAX_SECONDS, ensure_transcribe_language,
)
from app.services.bodhan.exceptions import BodhanError, BodhanProtocolError, BodhanRequestInvalid

SERVICE = "transcribe"
MODEL = "indic-transcribe"


@dataclass
class SegmentTranscript:
    index: int
    start_seconds: float
    end_seconds: float
    text: str
    request_id: str | None = None


@dataclass
class TranscriptionResult:
    language: str
    text: str
    segments: list[SegmentTranscript] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        return self.segments[-1].end_seconds if self.segments else 0.0


async def transcribe_segments(client, segments: list[AudioSegment], language: str) -> TranscriptionResult:
    """Send each WAV segment in order. A failed segment aborts the whole job and names the segment;
    audio is never silently skipped."""
    ensure_transcribe_language(language)
    if not segments:
        raise BodhanRequestInvalid("There is no audio to transcribe.", code="empty_audio")
    results: list[SegmentTranscript] = []
    for seg in sorted(segments, key=lambda s: s.index):
        if seg.duration_seconds > TRANSCRIBE_MAX_SECONDS:
            raise BodhanRequestInvalid(
                f"Segment {seg.index} is {seg.duration_seconds:.1f}s; the limit is {TRANSCRIBE_MAX_SECONDS:.0f}s.",
                code="segment_too_long")
        try:
            resp = await client.request(
                SERVICE, "POST", "/v1/audio/transcriptions",
                data={"model": MODEL, "language": language},
                files={"file": (f"segment_{seg.index:04d}.wav", seg.wav_bytes, "audio/wav")},
            )
        except BodhanError as exc:
            exc.message = f"Segment {seg.index + 1} of {len(segments)} failed: {exc.message}"
            raise
        body = resp.json
        if not isinstance(body, dict) or not isinstance(body.get("text"), str):
            raise BodhanProtocolError(SERVICE, "Response did not contain a 'text' string.",
                                      status_code=resp.status_code, request_id=resp.request_id)
        results.append(SegmentTranscript(seg.index, seg.start_seconds, seg.end_seconds,
                                         body["text"].strip(), resp.request_id))
    text = " ".join(r.text for r in results if r.text)
    return TranscriptionResult(language, text, results)


async def transcribe_file(client, path: Path, language: str, *, ffmpeg_path: str | None = None,
                          max_segment_seconds: float = 29.0, max_total_seconds: float = 3600.0,
                          ffmpeg_timeout: float = 300.0) -> TranscriptionResult:
    ensure_transcribe_language(language)
    segments = await asyncio.to_thread(
        prepare_segments, Path(path), ffmpeg_path=ffmpeg_path, max_segment_seconds=max_segment_seconds,
        max_total_seconds=max_total_seconds, timeout=ffmpeg_timeout)
    return await transcribe_segments(client, segments, language)
