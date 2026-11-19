"""MCP Tool: bodhan_transcribe
Speech-to-text with automatic >28s chunking at natural pauses and SRT generation.
"""

import os
from typing import List, Optional
from bodhan_mcp.client import bodhan_client
from bodhan_mcp.cost import calculate_transcribe_cost
from bodhan_mcp.types import SubtitleSegment, TranscribeInput, TranscribeOutput, ToolError
from bodhan_mcp.audio_helpers import (
    format_srt_timestamp,
    get_audio_info,
    split_audio_at_pauses,
    build_srt_content,
)


def transcribe_audio(
    audio_path: str,
    language: Optional[str] = None,
    vad: bool = True,
    output_format: str = "text",
    output_srt_path: Optional[str] = None,
) -> TranscribeOutput:
    """Execute speech-to-text on audio file, handling >28s chunking and SRT alignment."""
    if not os.path.exists(audio_path):
        raise ToolError("FILE_NOT_FOUND", f"Audio file not found: {audio_path}")

    info = get_audio_info(audio_path)
    total_duration = info["duration"]

    # 1. Split audio at natural pauses (max 28s, prefers 8-15s)
    chunks = split_audio_at_pauses(audio_path, min_chunk_s=8.0, max_chunk_s=28.0)

    segments: List[SubtitleSegment] = []
    full_text_parts: List[str] = []
    all_cached = True
    uncached_duration_s = 0.0
    recovery_attempts: List[str] = []

    for idx, (start_s, end_s, chunk_file) in enumerate(chunks, 1):
        chunk_text, is_cached = bodhan_client.transcribe(chunk_file, language=language, vad=vad)
        if not is_cached:
            all_cached = False
            uncached_duration_s += (end_s - start_s)

        # Check for transcription repetition loops and attempt recovery
        from bodhan_agent.verifier import check_repetition_or_omission
        if chunk_text and not check_repetition_or_omission(chunk_text):
            tried_steps = []
            loop_resolved = False

            # Recovery Attempt 1: Toggle VAD
            alt_vad = not vad
            tried_steps.append(f"vad={alt_vad}")
            alt_text, _ = bodhan_client.transcribe(chunk_file, language=language, vad=alt_vad)
            if alt_text and check_repetition_or_omission(alt_text):
                chunk_text = alt_text
                loop_resolved = True
                recovery_attempts.append(f"Chunk {idx}: repetition loop recovered by toggling vad={alt_vad}")

            # Recovery Attempt 2: Alternative chunk boundaries (split chunk into 2 halves)
            if not loop_resolved and (end_s - start_s) > 4.0:
                tried_steps.append("boundary sub-chunking into 2 halves")
                try:
                    import subprocess
                    half_dur = (end_s - start_s) / 2.0
                    sub1_path = chunk_file.replace(".wav", "_sub1.wav").replace(".mp3", "_sub1.mp3")
                    sub2_path = chunk_file.replace(".wav", "_sub2.wav").replace(".mp3", "_sub2.mp3")

                    subprocess.run(["ffmpeg", "-y", "-i", chunk_file, "-t", str(half_dur), "-c:a", "copy", sub1_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                    subprocess.run(["ffmpeg", "-y", "-ss", str(half_dur), "-i", chunk_file, "-c:a", "copy", sub2_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

                    t1, _ = bodhan_client.transcribe(sub1_path, language=language, vad=False)
                    t2, _ = bodhan_client.transcribe(sub2_path, language=language, vad=False)
                    comb_text = f"{t1} {t2}".strip()

                    if comb_text and check_repetition_or_omission(comb_text):
                        chunk_text = comb_text
                        loop_resolved = True
                        recovery_attempts.append(f"Chunk {idx}: repetition loop recovered by splitting chunk boundaries")

                    for p in [sub1_path, sub2_path]:
                        if os.path.exists(p):
                            try:
                                os.remove(p)
                            except Exception:
                                pass
                except Exception as e:
                    tried_steps.append(f"sub-split error: {e}")

            if not loop_resolved:
                attempt_str = f"Chunk {idx}: repetition loop persisted after attempting {', '.join(tried_steps)}"
                recovery_attempts.append(attempt_str)

        if chunk_text:
            full_text_parts.append(chunk_text)
            seg = SubtitleSegment(
                index=idx,
                start_time=format_srt_timestamp(start_s),
                end_time=format_srt_timestamp(end_s),
                start_seconds=round(start_s, 3),
                end_seconds=round(end_s, 3),
                text=chunk_text,
            )
            segments.append(seg)

    full_transcript = " ".join(full_text_parts).strip()
    cost_inr = 0.0 if all_cached else calculate_transcribe_cost(uncached_duration_s)

    srt_path = None
    if output_format in ("srt", "both"):
        if output_srt_path:
            srt_path = output_srt_path
        else:
            srt_path = os.path.splitext(audio_path)[0] + ".srt"

        os.makedirs(os.path.dirname(os.path.abspath(srt_path)), exist_ok=True)
        srt_content = build_srt_content(segments)
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write(srt_content)

    detected_lang = language
    if not detected_lang and full_transcript:
        from bodhan_mcp.tools.lang_detect import detect_text_language
        det = detect_text_language(full_transcript)
        detected_lang = det.language

    return TranscribeOutput(
        text=full_transcript,
        language=detected_lang,
        srt_path=srt_path,
        segments=segments,
        duration_seconds=round(total_duration, 2),
        cost_inr=cost_inr,
        cached=all_cached,
        recovery_attempts=recovery_attempts if recovery_attempts else None,
    )

