"""Stage 1 — English speech to timestamped segments.

The model returns text only, so the timestamps come from the cuts we make:

1. decode to 16 kHz mono PCM with ffmpeg;
2. detect speech with the energy VAD;
3. plan chunks that never exceed 28 s and never break a pause shorter than
   300 ms, so an utterance is not cut in half;
4. transcribe each chunk, in parallel, with the cache in front;
5. normalise, drop degenerate output, collapse any looping transcription.

Because a chunk *is* a speech region, its start and end are sample-accurate and
become the segment's start and end. That keeps every later stage — translation,
duration budgeting, placement — anchored to real speech boundaries.

A keyboard interrupt stops new work, releases every waiting worker, and still
writes the transcript for whatever finished. Partial output beats a lost run.
"""

from __future__ import annotations

import concurrent.futures
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import chunker, media, text
from .client import BodhanClient
from .config import MODEL_ASR, Settings
from .cost import CostMeter
from .errors import BodhanAPIError, BodhanDubError, ConfigError, MediaError
from .languages import SOURCE_LANG
from .log import event

SCHEMA = "bodhan-dub/transcript/v1"
LONG_INPUT_WARN_S = 15 * 60
FATAL_STATUS = {401, 403}


def run(
    *,
    input_path: Path,
    out_dir: Path,
    settings: Settings,
    client: BodhanClient,
    cost: CostMeter,
    logger: Any,
) -> dict[str, Any]:
    media.ensure_tools()
    event(logger, "asr.start", stage="asr", input=str(input_path))

    info = media.probe(input_path)
    if not info.has_audio:
        raise MediaError(
            f"{input_path.name} has no audio stream; nothing to transcribe or dub"
        )
    if info.duration_s > LONG_INPUT_WARN_S:
        event(
            logger,
            "asr.input_long",
            level=30,
            stage="asr",
            duration_s=round(info.duration_s, 1),
            note="above the 15 minute brief; expect a long run and a higher cost",
        )

    wav_path = _ensure_pcm(input_path, settings, logger)
    samples, sample_rate = media.read_wav(wav_path)
    duration_s = len(samples) / float(sample_rate)
    event(
        logger,
        "asr.audio_ready",
        stage="asr",
        duration_s=round(duration_s, 2),
        sample_rate=sample_rate,
    )

    chunks = chunker.plan(samples, sample_rate)
    if not chunks:
        raise MediaError(
            "no speech was detected. Either the file is silent, or the audio is too "
            "quiet for the energy VAD — check the mix before dubbing."
        )
    speech_s = sum(chunk.duration_s for chunk in chunks)
    event(
        logger,
        "asr.chunks_ready",
        stage="asr",
        chunks=len(chunks),
        speech_s=round(speech_s, 2),
        longest_s=round(max(chunk.duration_s for chunk in chunks), 2),
        coverage=round(speech_s / duration_s, 3) if duration_s else 0.0,
    )

    results, interrupted = _transcribe_all(
        chunks, samples, sample_rate, settings=settings, client=client, logger=logger
    )

    segments: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    empty = 0
    collapsed = 0

    for status, chunk, payload in results:
        if status != "ok":
            failures.append(
                {
                    "chunk": chunk.index,
                    "start": round(chunk.start_s, 3),
                    "end": round(chunk.end_s, 3),
                    "reason": str(payload),
                }
            )
            continue
        raw = str(payload.data.get("text", ""))
        cleaned = text.normalize_ws(raw)
        if text.is_degenerate(cleaned):
            empty += 1
            continue
        cleaned, did_collapse = text.collapse_repeats(cleaned)
        if did_collapse:
            collapsed += 1
            event(
                logger,
                "asr.repetition_collapsed",
                level=30,
                stage="asr",
                chunk=chunk.index,
                chars_before=len(raw),
                chars_after=len(cleaned),
            )
        segments.append(
            {
                "id": len(segments),
                "start": round(chunk.start_s, 3),
                "end": round(chunk.end_s, 3),
                "duration_s": round(chunk.duration_s, 3),
                "text": cleaned,
                "chunk": chunk.index,
                "regions": chunk.regions,
                "repetition_collapsed": did_collapse,
            }
        )

    if not segments and not interrupted:
        raise BodhanDubError(
            f"transcription produced no usable text ({len(failures)} chunk failures, "
            f"{empty} empty responses). First failure: "
            f"{failures[0]['reason'] if failures else 'none'}"
        )

    transcribed_s = sum(segment["duration_s"] for segment in segments)
    payload = {
        "schema": SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "input": info.to_dict(),
        "asr": {
            "model": MODEL_ASR,
            "language": SOURCE_LANG,
            "timing": "vad-chunk-boundaries",
            "chunk_max_s": chunker.MAX_CHUNK_S,
            "requests": len(chunks),
        },
        "stats": {
            "duration_s": round(duration_s, 3),
            "speech_s": round(speech_s, 3),
            "transcribed_s": round(transcribed_s, 3),
            "speech_coverage": round(speech_s / duration_s, 4) if duration_s else 0.0,
            "chunks": len(chunks),
            "segments": len(segments),
            "empty_responses": empty,
            "repetitions_collapsed": collapsed,
            "failures": len(failures),
            "interrupted": interrupted,
        },
        "segments": segments,
        "failures": failures,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = out_dir / f"transcript.{SOURCE_LANG}.json"
    _write_json(transcript_path, payload)

    event(
        logger,
        "asr.done",
        stage="asr",
        segments=len(segments),
        failures=len(failures),
        interrupted=interrupted,
        cost_inr=round(cost.total_inr, 4),
        **client.limiter_stats().get(MODEL_ASR, {}),
        out=str(transcript_path),
    )
    if failures and not interrupted:
        event(
            logger,
            "asr.partial",
            level=40,
            stage="asr",
            failed_chunks=len(failures),
            note="these stretches have no source text and will be skipped downstream",
        )
    if interrupted:
        event(
            logger,
            "asr.interrupted",
            level=40,
            stage="asr",
            note="partial transcript written; unfinished chunks are marked interrupted",
        )
    return payload


def _transcribe_all(
    chunks: list[chunker.Chunk],
    samples: Any,
    sample_rate: int,
    *,
    settings: Settings,
    client: BodhanClient,
    logger: Any,
) -> tuple[list[tuple[str, chunker.Chunk, Any]], bool]:
    workers = max(1, min(settings.max_inflight, len(chunks)))
    stop = threading.Event()
    fatal = threading.Event()
    fatal_reason: list[str] = []

    def work(chunk: chunker.Chunk) -> tuple[str, chunker.Chunk, Any]:
        if stop.is_set():
            return ("skipped", chunk, "interrupted")
        if fatal.is_set():
            return ("skipped", chunk, fatal_reason[0] if fatal_reason else "aborted")
        try:
            audio = samples[chunk.start_sample : chunk.end_sample]
            wav_bytes = media.to_wav_bytes(audio, sample_rate)
            result = client.transcribe(
                wav_bytes,
                language=SOURCE_LANG,
                audio_seconds=chunk.duration_s,
                filename=f"chunk{chunk.index:05d}.wav",
                segment=chunk.index,
            )
            event(
                logger,
                "asr.segment",
                stage="asr",
                segment=chunk.index,
                start=round(chunk.start_s, 2),
                end=round(chunk.end_s, 2),
                chars=len(result.data.get("text", "")),
                latency_ms=round(result.latency_ms, 1),
                attempts=result.attempts,
                cached=result.cached,
            )
            return ("ok", chunk, result)
        except Exception as exc:  # one bad chunk must not sink the run
            # A bad key will fail every chunk identically. Stop after the first
            # so a misconfiguration costs one request, not one per chunk.
            if isinstance(exc, BodhanAPIError) and exc.status in FATAL_STATUS:
                fatal_reason.append(str(exc))
                fatal.set()
            event(
                logger,
                "asr.chunk_failed",
                level=40,
                stage="asr",
                segment=chunk.index,
                start=round(chunk.start_s, 2),
                end=round(chunk.end_s, 2),
                reason=str(exc)[:200],
            )
            return ("error", chunk, exc)

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
    futures = [executor.submit(work, chunk) for chunk in chunks]
    results: list[tuple[str, chunker.Chunk, Any]] = []
    interrupted = False

    try:
        for future in futures:
            results.append(future.result())
    except KeyboardInterrupt:
        interrupted = True
        stop.set()
        client.stop()
        executor.shutdown(wait=False, cancel_futures=True)
    finally:
        if not interrupted:
            executor.shutdown(wait=True)

    if interrupted:
        results = []
        for chunk, future in zip(chunks, futures, strict=False):
            if future.done() and not future.cancelled():
                try:
                    results.append(future.result())
                except Exception as exc:  # pragma: no cover - defensive
                    results.append(("error", chunk, exc))
            else:
                results.append(("skipped", chunk, "interrupted"))

    if fatal.is_set() and not interrupted:
        raise ConfigError(
            f"the transcription key was rejected: {fatal_reason[0] if fatal_reason else 'auth failed'}. "
            "Check BODHAN_TRANSCRIBE_KEY (or BODHAN_TRANSCRIBE_API_KEY) and try again. "
            "No further requests were sent."
        )
    return results, interrupted


def _ensure_pcm(input_path: Path, settings: Settings, logger: Any) -> Path:
    """Decode once per input and reuse the 16 kHz WAV across runs."""
    work_dir = settings.cache_dir / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    digest = media.file_digest(input_path)
    wav_path = work_dir / f"{digest[:16]}.16k.wav"
    if wav_path.exists() and wav_path.stat().st_size > 44:
        event(logger, "asr.pcm_reused", stage="asr", path=str(wav_path))
        return wav_path
    media.extract_pcm_wav(input_path, wav_path)
    event(logger, "asr.pcm_extracted", stage="asr", path=str(wav_path))
    return wav_path


def _write_json(path: Path, payload: Any) -> None:
    import json

    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
