"""
ASR Stage for bodhan_dub.
Performs chunk slicing, parallel Indic-Transcribe calls, chunk-splitting retries,
hallucination filtering, and overlap deduplication.
"""

from __future__ import annotations

import asyncio
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import httpx
import rapidfuzz
import soundfile as sf

from bodhan_dub.gateway.bodhan import BodhanClient
from bodhan_dub.gateway.cost import BudgetExceededError
from bodhan_dub.gateway.resilience import CircuitBreakerOpen
from bodhan_dub.logging import PipelineLogger
from bodhan_dub.media.chunks import AudioChunk
from bodhan_dub.media.vad import SpeechRegion, VADResult
from bodhan_dub.text.normalize import detect_repeated_ngrams


class ASRError(Exception):
    """B1: Raised when ASR stage encounters a fatal error or authentication failure."""

    pass


@dataclass
class TranscribedSegment:
    text: str
    start: float
    end: float
    timestamp_source: str  # "api_snapped", "api_raw", or "vad_proportional"

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def snap_segment_to_vad(
    start: float,
    end: float,
    speech_regions: List[SpeechRegion],
    snap_tolerance_s: float = 0.400,
) -> Tuple[float, float, str]:
    """
    B4 (a): Snaps raw segment start and end to nearest VAD speech region boundaries.
    Returns (snapped_start, snapped_end, source_tag) where source_tag is
    'api_snapped' if snapped or 'api_raw' if no boundary was within tolerance.
    """
    snapped_start = start
    snapped_end = end
    did_snap = False

    for r in speech_regions:
        if abs(start - r.start) <= snap_tolerance_s:
            snapped_start = r.start
            did_snap = True
            break
        elif abs(start - r.end) <= snap_tolerance_s:
            snapped_start = r.end
            did_snap = True
            break

    for r in speech_regions:
        if abs(end - r.end) <= snap_tolerance_s:
            snapped_end = r.end
            did_snap = True
            break
        elif abs(end - r.start) <= snap_tolerance_s:
            snapped_end = r.start
            did_snap = True
            break

    if snapped_end <= snapped_start:
        snapped_start = start
        snapped_end = end
        did_snap = False

    source_tag = "api_snapped" if did_snap else "api_raw"
    return round(snapped_start, 3), round(snapped_end, 3), source_tag


def allocate_proportional_vad_segments(
    text: str,
    start_s: float,
    end_s: float,
    speech_regions: List[SpeechRegion],
) -> List[TranscribedSegment]:
    """
    B4 (b): When API returns unsegmented text without timestamps, split text into sentences
    and allocate boundaries proportionally by character count inside chunk's VAD speech regions,
    snapping to internal pauses. Never use raw chunk bounds as segment start/end.
    """
    chunk_regions = [
        r for r in speech_regions
        if r.end > start_s and r.start < end_s
    ]
    if not chunk_regions:
        mid = (start_s + end_s) / 2.0
        return [
            TranscribedSegment(
                text=text.strip(),
                start=round(mid, 3),
                end=round(mid, 3),
                timestamp_source="vad_proportional",
            )
        ]

    raw_sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    if not raw_sentences:
        raw_sentences = [text.strip()]

    total_chars = sum(len(s) for s in raw_sentences)
    if total_chars == 0:
        return []

    clamped_regions = [
        (max(start_s, r.start), min(end_s, r.end))
        for r in chunk_regions
    ]
    total_speech_dur = sum(r_end - r_start for r_start, r_end in clamped_regions)

    if total_speech_dur <= 0.05:
        first_start = clamped_regions[0][0]
        return [
            TranscribedSegment(
                text=text.strip(),
                start=round(first_start, 3),
                end=round(first_start + 0.1, 3),
                timestamp_source="vad_proportional",
            )
        ]

    results: List[TranscribedSegment] = []
    curr_region_idx = 0
    curr_offset_in_region = 0.0

    for sentence in raw_sentences:
        fraction = len(sentence) / total_chars
        sentence_dur = fraction * total_speech_dur

        r_start, r_end = clamped_regions[curr_region_idx]
        seg_start = r_start + curr_offset_in_region

        rem_dur = sentence_dur
        seg_end = seg_start + rem_dur
        while curr_region_idx < len(clamped_regions):
            r_start, r_end = clamped_regions[curr_region_idx]
            avail = (r_end - r_start) - curr_offset_in_region
            if rem_dur <= avail:
                seg_end = (r_start + curr_offset_in_region) + rem_dur
                curr_offset_in_region += rem_dur
                break
            else:
                rem_dur -= avail
                curr_region_idx += 1
                curr_offset_in_region = 0.0
                if curr_region_idx >= len(clamped_regions):
                    seg_end = clamped_regions[-1][1]
                    break

        results.append(
            TranscribedSegment(
                text=sentence,
                start=round(seg_start, 3),
                end=round(max(seg_start + 0.05, seg_end), 3),
                timestamp_source="vad_proportional",
            )
        )

    return results

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def slice_wav_bytes(audio_path: Path, start_s: float, end_s: float) -> bytes:
    """Reads a slice of a 16kHz mono WAV file and returns WAV bytes in memory."""
    with sf.SoundFile(str(audio_path)) as sf_file:
        sr = sf_file.samplerate
        start_frame = int(start_s * sr)
        n_frames = max(1, int((end_s - start_s) * sr))

        sf_file.seek(start_frame)
        slice_data = sf_file.read(n_frames, dtype="int16")

    buf = io.BytesIO()
    with sf.SoundFile(
        buf, mode="w", format="WAV", subtype="PCM_16", samplerate=sr, channels=1
    ) as out_sf:
        out_sf.write(slice_data)
    return buf.getvalue()


def calculate_speech_overlap_ratio(
    start: float, end: float, speech_regions: List[SpeechRegion]
) -> float:
    """Calculates what fraction of [start, end] intersects with active VAD speech regions."""
    duration = end - start
    if duration <= 0.0:
        return 0.0

    overlap = 0.0
    for r in speech_regions:
        inter_start = max(start, r.start)
        inter_end = min(end, r.end)
        if inter_end > inter_start:
            overlap += inter_end - inter_start

    return min(1.0, overlap / duration)


def deduplicate_overlap_text(prev_text: str, curr_text: str) -> str:
    """
    Removes duplicate overlapping phrases between adjacent chunks using rapidfuzz.
    """
    prev_words = prev_text.strip().split()
    curr_words = curr_text.strip().split()

    if not prev_words or not curr_words:
        return curr_text

    # Compare trailing up to 6 words of prev with leading up to 6 words of curr
    max_check = min(len(prev_words), len(curr_words), 8)
    for n in range(max_check, 1, -1):
        prev_tail = " ".join(prev_words[-n:]).lower()
        curr_head = " ".join(curr_words[:n]).lower()
        # High similarity indicates duplicate boundary phrase
        if rapidfuzz.fuzz.ratio(prev_tail, curr_head) >= 85:
            # Strip the leading duplicate words from current text
            return " ".join(curr_words[n:])

    return curr_text


class ASRStage:
    """Orchestrates parallel chunk transcription, fault retries, and deduplication."""

    def __init__(
        self,
        bodhan_client: BodhanClient,
        logger: PipelineLogger,
        speech_overlap_min_ratio: float = 0.40,
    ):
        self.bodhan_client = bodhan_client
        self.logger = logger
        self.speech_overlap_min_ratio = speech_overlap_min_ratio

    async def transcribe_chunk(
        self,
        audio_path: Path,
        start_s: float,
        end_s: float,
        chunk_id: int,
        vad_result: VADResult,
        depth: int = 0,
    ) -> List[TranscribedSegment]:
        """
        Transcribes an individual audio chunk with retry and chunk-splitting on empty responses.
        """
        duration = end_s - start_s
        has_speech_in_chunk = any(
            r.end > start_s and r.start < end_s for r in vad_result.speech_regions
        )

        audio_bytes = slice_wav_bytes(audio_path, start_s, end_s)

        for attempt in range(1, 3):
            try:
                resp = await self.bodhan_client.transcribe(
                    audio_bytes=audio_bytes,
                    language="en",
                    response_format="verbose_json",
                    duration_seconds=duration,
                    stage="asr",
                    segment_id=f"chunk_{chunk_id}",
                )

                text = resp.get("text", "").strip()
                api_segments = resp.get("segments", [])

                # If text returned, parse segments
                if text:
                    results: List[TranscribedSegment] = []
                    if api_segments:
                        for s in api_segments:
                            s_text = s.get("text", "").strip()
                            if not s_text:
                                continue
                            raw_start = start_s + float(s.get("start", 0.0))
                            raw_end = min(end_s, start_s + float(s.get("end", duration)))
                            snapped_start, snapped_end, source_tag = snap_segment_to_vad(
                                raw_start, raw_end, vad_result.speech_regions
                            )
                            results.append(
                                TranscribedSegment(
                                    text=s_text,
                                    start=snapped_start,
                                    end=snapped_end,
                                    timestamp_source=source_tag,
                                )
                            )
                    else:
                        # B4: Proportional boundary allocation within VAD regions
                        results = allocate_proportional_vad_segments(
                            text=text,
                            start_s=start_s,
                            end_s=end_s,
                            speech_regions=vad_result.speech_regions,
                        )
                    return results

                # If empty text but VAD sees speech, retry or split
                if has_speech_in_chunk and attempt == 1:
                    self.logger.warning(
                        f"Chunk {chunk_id} returned empty text despite VAD detecting speech; retrying...",
                        stage="asr",
                        segment_id=f"chunk_{chunk_id}",
                    )
                    await asyncio.sleep(0.5)
                    continue

            except (BudgetExceededError, CircuitBreakerOpen):
                # B1: Always re-raise budget exhaustion and open circuit breaker
                raise
            except httpx.HTTPStatusError as e:
                # B2: Documented over-length 400 triggers existing split rather than immediate abort
                is_overlength_400 = (
                    e.response.status_code == 400
                    and any(w in e.response.text.lower() for w in ("duration", "length", "30", "too long", "maximum"))
                )
                if is_overlength_400 and depth < 2 and duration > 6.0:
                    self.logger.warning(
                        f"Chunk {chunk_id} over-length 400 ({e.response.text}); triggering split",
                        stage="asr",
                        segment_id=f"chunk_{chunk_id}",
                    )
                    mid = round((start_s + end_s) / 2.0, 3)
                    left_res = await self.transcribe_chunk(audio_path, start_s, mid, chunk_id, vad_result, depth=depth + 1)
                    right_res = await self.transcribe_chunk(audio_path, mid, end_s, chunk_id, vad_result, depth=depth + 1)
                    return left_res + right_res

                # Fatal non-retryable HTTP errors (400, 401, 403) must be re-raised immediately
                if e.response.status_code in (400, 401, 403):
                    self.logger.error(
                        f"Fatal non-retryable HTTP {e.response.status_code} in ASR chunk {chunk_id}: {e}",
                        stage="asr",
                        segment_id=f"chunk_{chunk_id}",
                    )
                    raise
                # Transient HTTP error (408, 429, 5xx) gets retried
                self.logger.warning(
                    f"Transient HTTP {e.response.status_code} in ASR chunk {chunk_id} attempt {attempt}: {e}",
                    stage="asr",
                    segment_id=f"chunk_{chunk_id}",
                )
                if attempt == 1:
                    await asyncio.sleep(1.0)
                    continue
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                # Transient network error gets retried
                self.logger.warning(
                    f"Transient network/timeout error in ASR chunk {chunk_id} attempt {attempt}: {e}",
                    stage="asr",
                    segment_id=f"chunk_{chunk_id}",
                )
                if attempt == 1:
                    await asyncio.sleep(1.0)
                    continue
            except Exception as e:
                if isinstance(e, (BudgetExceededError, CircuitBreakerOpen)):
                    raise
                if hasattr(e, "response") and getattr(e.response, "status_code", None) in (400, 401, 403):
                    raise
                self.logger.warning(
                    f"Chunk {chunk_id} transcription attempt {attempt} failed: {e}",
                    stage="asr",
                    segment_id=f"chunk_{chunk_id}",
                )
                if attempt == 1:
                    await asyncio.sleep(1.0)
                    continue

        # Split chunk in half if it failed and depth allows (max 2 splits)
        if has_speech_in_chunk and depth < 2 and duration > 6.0:
            mid = round((start_s + end_s) / 2.0, 3)
            self.logger.info(
                f"Splitting chunk {chunk_id} into [{start_s}-{mid}] and [{mid}-{end_s}]",
                stage="asr",
                segment_id=f"chunk_{chunk_id}",
            )
            left_res = await self.transcribe_chunk(
                audio_path, start_s, mid, chunk_id, vad_result, depth=depth + 1
            )
            right_res = await self.transcribe_chunk(
                audio_path, mid, end_s, chunk_id, vad_result, depth=depth + 1
            )
            return left_res + right_res

        self.logger.warning(
            f"Chunk {chunk_id} [{start_s}-{end_s}] produced no transcription. Continuing with gap.",
            stage="asr",
            segment_id=f"chunk_{chunk_id}",
        )
        return []

    async def run(
        self,
        audio_path: Path,
        chunks: List[AudioChunk],
        vad_result: VADResult,
    ) -> List[TranscribedSegment]:
        """
        Runs transcription on all planned chunks in parallel, applies hallucination filters,
        and deduplicates overlapping boundaries.
        """
        if not chunks:
            return []

        # B2: Run chunks in parallel with sibling cancellation on fatal errors
        task_list: List[asyncio.Task] = []
        fatal_error: Optional[BaseException] = None

        def _cancel_siblings_on_fatal(t: asyncio.Task):
            nonlocal fatal_error
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                is_fatal_http = (
                    isinstance(exc, httpx.HTTPStatusError)
                    and exc.response.status_code in (400, 401, 403)
                )
                is_fatal = is_fatal_http or isinstance(exc, (BudgetExceededError, CircuitBreakerOpen))
                if is_fatal and fatal_error is None:
                    fatal_error = exc
                    for other in task_list:
                        if not other.done():
                            other.cancel()

        for ch in chunks:
            t = asyncio.create_task(
                self.transcribe_chunk(
                    audio_path=audio_path,
                    start_s=ch.start,
                    end_s=ch.end,
                    chunk_id=ch.id,
                    vad_result=vad_result,
                )
            )
            t.add_done_callback(_cancel_siblings_on_fatal)
            task_list.append(t)

        chunk_results = await asyncio.gather(*task_list, return_exceptions=True)

        # Propagate fatal error after cancelling siblings
        if fatal_error is not None:
            raise fatal_error

        # Flatten and filter segments
        all_segments: List[TranscribedSegment] = []
        for seg_list in chunk_results:
            if isinstance(seg_list, BaseException):
                if not isinstance(seg_list, asyncio.CancelledError):
                    raise seg_list
                continue
            for seg in seg_list:
                # B1: Clamp and snap API segments to VAD speech regions first
                intersecting = [
                    r for r in vad_result.speech_regions
                    if r.end > seg.start and r.start < seg.end
                ]
                if not intersecting:
                    # Truly no speech overlap: drop hallucinated segment
                    self.logger.warning(
                        f"Dropping hallucinated segment '{seg.text}' (overlap 0.0%): no_speech_overlap",
                        stage="asr",
                    )
                    continue

                clamped_start = max(seg.start, intersecting[0].start)
                clamped_end = min(seg.end, intersecting[-1].end)
                speech_in_clamped = sum(
                    min(clamped_end, r.end) - max(clamped_start, r.start)
                    for r in intersecting
                )

                if speech_in_clamped <= 0.05:
                    self.logger.warning(
                        f"Dropping hallucinated segment '{seg.text}' (speech in clamped {speech_in_clamped:.2f}s): no_speech_overlap",
                        stage="asr",
                    )
                    continue

                # If coarse segment covers multiple distinct VAD speech sub-spans, split proportionally
                if len(intersecting) > 1 and len(seg.text.split()) > 4:
                    sub_segs = allocate_proportional_vad_segments(
                        text=seg.text,
                        start_s=clamped_start,
                        end_s=clamped_end,
                        speech_regions=intersecting,
                    )
                    for s in sub_segs:
                        # Non-empty check
                        if s.text.strip():
                            all_segments.append(s)
                    continue

                # Snap boundaries to the clamped speech envelope
                seg.start = round(clamped_start, 3)
                seg.end = round(clamped_end, 3)

                # 2. Hallucination Check: Looped n-grams
                is_loop, phrase = detect_repeated_ngrams(seg.text)
                if is_loop:
                    self.logger.warning(
                        f"Dropping looping segment '{seg.text}' (repeated phrase '{phrase}'): asr_loop",
                        stage="asr",
                    )
                    continue

                # 3. Non-empty check
                if not seg.text.strip():
                    continue

                all_segments.append(seg)

        # Sort chronologically by start time
        all_segments.sort(key=lambda s: s.start)

        # Overlap deduplication across chunk boundaries
        deduped: List[TranscribedSegment] = []
        for idx, seg in enumerate(all_segments):
            if not deduped:
                deduped.append(seg)
                continue

            prev = deduped[-1]
            # If segments overlap in time, deduplicate text
            if seg.start < prev.end:
                clean_text = deduplicate_overlap_text(prev.text, seg.text)
                if clean_text:
                    seg.text = clean_text
                    deduped.append(seg)
            else:
                deduped.append(seg)

        if vad_result.has_speech and not deduped:
            self.logger.error(
                "ASR failure: VAD detected speech but zero segments survived transcription and filtering",
                stage="asr",
            )
            raise ASRError("ASR failed: VAD detected active speech but zero segments were transcribed")

        return deduped
