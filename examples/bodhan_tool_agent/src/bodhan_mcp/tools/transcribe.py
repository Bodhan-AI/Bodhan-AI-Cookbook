"""Audio transcription tool absorbing silence chunking, timestamp remapping, and retries."""

from pathlib import Path
from typing import Any

from bodhan_mcp import config, errors
from bodhan_mcp.clients.bodhan import BodhanClient
from bodhan_mcp.schemas import Segment, TranscribeOut
from bodhan_mcp.utils import audio


async def transcribe_audio(
    input_path: str,
    language_hint: str | None = None,
    client: BodhanClient | None = None,
) -> TranscribeOut | errors.ToolErrorPayload:
    """Transcribe an audio file to text with global segment timestamps.

    Absorbs chunking at pauses (>=250ms silence, target 25s, hard cap 30s),
    timestamp remapping to global offsets, retries, and cost aggregation.

    Errors: FILE_NOT_FOUND, UNREADABLE_MEDIA, UNSUPPORTED_LANGUAGE, UPSTREAM_*.
    """
    file_path = Path(input_path)
    if not file_path.exists():
        return errors.make_tool_error(
            code=errors.FILE_NOT_FOUND,
            message=f"Audio file not found: {file_path}",
            hint="Ensure input_path exists and is readable.",
            details={"input_path": str(file_path)},
        )

    # Validate language hint if provided
    lang_name_map = {
        "hindi": "hi", "tamil": "ta", "telugu": "te", "bengali": "bn",
        "marathi": "mr", "gujarati": "gu", "kannada": "kn", "malayalam": "ml",
        "punjabi": "pa", "odia": "or", "assamese": "as", "urdu": "ur", "english": "en"
    }
    if language_hint:
        language_hint = lang_name_map.get(language_hint.lower().strip(), language_hint.lower().strip())
    if language_hint and language_hint not in config.SUPPORTED_LANGS["transcribe"]:
        return errors.make_tool_error(
            code=errors.UNSUPPORTED_LANGUAGE,
            message=f"Unsupported language hint: '{language_hint}'.",
            hint=f"Supported languages: {', '.join(config.SUPPORTED_LANGS['transcribe'])}",
            details={"language_hint": language_hint},
        )

    # Probe audio
    try:
        meta = audio.probe(file_path)
        total_duration = meta["duration_s"]
    except Exception as exc:
        return errors.make_tool_error(
            code=errors.UNREADABLE_MEDIA,
            message=f"Failed to read audio file: {exc}",
            hint="Ensure audio file is not corrupt and is in a standard format (WAV, MP3, etc.).",
            details={"input_path": str(file_path), "error": str(exc)},
        )

    bodhan = client or BodhanClient()

    try:
        # Single-shot path if <= 30.0s
        if total_duration <= config.AUDIO_CHUNK_HARD_S:
            audio_bytes = file_path.read_bytes()
            res = await bodhan.transcribe(
                audio_bytes=audio_bytes,
                filename=file_path.name,
                language_hint=language_hint,
                duration_s=total_duration,
            )
            raw_data = res.get("data", {})
            cost_inr = float(res.get("cost_inr", 0.0))

            segments = _parse_segments(raw_data.get("segments", []), default_duration=total_duration)
            transcript = raw_data.get("text") or " ".join(s.text for s in segments)
            detected_lang = raw_data.get("language", language_hint or "unknown")
            confidence = float(raw_data.get("language_confidence", 0.90))

            return TranscribeOut(
                transcript=transcript.strip(),
                language=detected_lang,
                language_confidence=confidence,
                segments=segments,
                duration_s=round(total_duration, 3),
                cost_inr=round(cost_inr, 4),
            )

        # Multi-chunk path for audio > 30.0s (absorbs limit)
        chunk_dir = file_path.parent / f"_{file_path.stem}_transcribe_chunks"
        chunks = audio.chunk_at_pauses(
            file_path,
            out_dir=chunk_dir,
            target_s=config.AUDIO_CHUNK_TARGET_S,
            hard_s=config.AUDIO_CHUNK_HARD_S,
        )

        per_chunk_segments: list[list[Segment]] = []
        chunk_transcripts: list[str] = []
        detected_languages: list[str] = []
        confidences: list[float] = []
        total_cost = 0.0

        for chunk in chunks:
            chunk_file = Path(chunk.path)
            chunk_bytes = chunk_file.read_bytes()
            chunk_duration = chunk.end_s - chunk.start_s

            c_res = await bodhan.transcribe(
                audio_bytes=chunk_bytes,
                filename=chunk_file.name,
                language_hint=language_hint,
                duration_s=chunk_duration,
            )
            c_data = c_res.get("data", {})
            total_cost += float(c_res.get("cost_inr", 0.0))

            c_segments = _parse_segments(c_data.get("segments", []), default_duration=chunk_duration)
            per_chunk_segments.append(c_segments)

            c_text = c_data.get("text") or " ".join(s.text for s in c_segments)
            if c_text.strip():
                chunk_transcripts.append(c_text.strip())

            if "language" in c_data:
                detected_languages.append(c_data["language"])
            if "language_confidence" in c_data:
                confidences.append(float(c_data["language_confidence"]))

        # Remap timestamps across chunks to global timeline
        global_segments = audio.remap_timestamps(chunks, per_chunk_segments)
        full_transcript = " ".join(chunk_transcripts).strip()
        overall_lang = detected_languages[0] if detected_languages else (language_hint or "unknown")
        avg_confidence = float(sum(confidences) / len(confidences)) if confidences else 0.85

        return TranscribeOut(
            transcript=full_transcript,
            language=overall_lang,
            language_confidence=round(avg_confidence, 2),
            segments=global_segments,
            duration_s=round(total_duration, 3),
            cost_inr=round(total_cost, 4),
        )

    except Exception as exc:
        return errors.make_tool_error(
            code=errors.INTERNAL,
            message=f"Transcription failed: {exc}",
            hint="Check upstream API status and credentials.",
            details={"error": str(exc)},
        )


def _parse_segments(raw_segments: list[Any], default_duration: float) -> list[Segment]:
    """Parse raw segment dictionary structures from model response."""
    segments: list[Segment] = []
    if not raw_segments:
        return segments

    for seg in raw_segments:
        if isinstance(seg, dict):
            start = float(seg.get("start", seg.get("start_s", 0.0)))
            end = float(seg.get("end", seg.get("end_s", default_duration)))
            text = str(seg.get("text", "")).strip()
            segments.append(Segment(start_s=start, end_s=end, text=text))
    return segments
