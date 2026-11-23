"""Synthesis, quality control and placement.

For each translated segment:

1. **speak** it with the language's voice and the configured style;
2. **back-transcribe** the clip and check it against what was asked for;
3. on failure, **retry**, then **split the text in half** and join the pieces,
   which recovers the common "looped" and "truncated" failures;
4. **trim the leading silence** — TTS output usually opens with 100-300 ms of
   quiet, which on its own would blow the sync budget;
5. **fit** the clip to its slot with ``atempo``, spilling into silence if needed;
6. **place** it at the original start time.

Segments are processed concurrently. Each one costs two model calls (speak, then
back-transcribe), and on a five-minute lesson that is over two hundred calls, so
doing them one at a time would put the runtime target at risk. The client's
per-model pacer is what keeps the concurrency inside the quota.

Every decision is recorded per segment, so a reviewer can see why a line was
sped up, retried or marked as a miss.
"""

from __future__ import annotations

import concurrent.futures
import re
import threading
from pathlib import Path
from typing import Any

import numpy as np

from . import languages, media, qa, text, timeline
from .duration import SpeakingRate, fit
from .log import event

MAX_SYNTH_ATTEMPTS = 2
SHORT_RETRY_CHARS = 12
SENTENCE_SPLIT = re.compile(r"(?<=[।॥.!?؟])\s+")


def speak_segments(
    segments: list[dict[str, Any]],
    *,
    lang: str,
    voice: str,
    settings: Any,
    client: Any,
    logger: Any,
    out_dir: Path,
    total_duration_s: float,
    refine: Any = None,
) -> dict[str, Any]:
    audio_dir = out_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    rate = SpeakingRate(lang)
    rate_lock = threading.Lock()
    workers = max(1, min(settings.max_inflight, len(segments) or 1))

    def work(segment: dict[str, Any]) -> dict[str, Any]:
        return _dub_one(
            segment,
            lang=lang,
            voice=voice,
            settings=settings,
            client=client,
            logger=logger,
            out_dir=out_dir,
            audio_dir=audio_dir,
            rate=rate,
            rate_lock=rate_lock,
            refine=refine,
        )

    if workers == 1:
        results = [work(segment) for segment in segments]
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(work, segments))

    records: list[dict[str, Any]] = []
    clips: list[tuple[float, np.ndarray]] = []
    counts = {"ok": 0, "skipped": 0, "failed": 0}
    fits = {"ok": 0, "stretched": 0, "spilled": 0, "miss": 0}
    cers: list[float] = []
    tempos: list[float] = []
    trimmed: list[float] = []
    misses: list[dict[str, Any]] = []
    qa_failures = 0

    for result in results:
        records.append(result["record"])
        status = result["record"].get("status")
        if status != "ok":
            counts[status if status in counts else "failed"] += 1
            continue
        counts["ok"] += 1
        fits[result["fit_status"]] = fits.get(result["fit_status"], 0) + 1
        cers.append(result["cer"])
        tempos.append(result["tempo"])
        trimmed.append(result["trimmed_s"])
        if result["qa_verdict"] != "pass":
            qa_failures += 1
        if result["fit_status"] == "miss":
            misses.append(result["miss"])
        clip = result.get("clip")
        if clip is not None:
            clips.append(clip)

    track_path = out_dir / f"audio.{lang}.wav"
    timeline.write_timeline(track_path, clips, total_duration_s, media.TTS_SAMPLE_RATE)

    stats = {
        "segments": len(segments),
        "ok": counts["ok"],
        "skipped": counts["skipped"],
        "failed": counts["failed"],
        "coverage": round(counts["ok"] / len(segments), 4) if segments else 0.0,
        "qa_failures": qa_failures,
        "mean_cer": round(sum(cers) / len(cers), 4) if cers else None,
        "max_tempo": round(max(tempos), 3) if tempos else None,
        "mean_tempo": round(sum(tempos) / len(tempos), 3) if tempos else None,
        "speed_violations": sum(1 for tempo in tempos if tempo > settings.max_tempo + 1e-6),
        "fits": fits,
        "misses": misses,
        "mean_leading_silence_trimmed_s": (round(sum(trimmed) / len(trimmed), 4) if trimmed else 0.0),
        "timeline": track_path.name,
        "speaking_rate": rate.to_dict(),
        "workers": workers,
    }

    return {"segments": records, "stats": stats, "timeline": track_path}


def _dub_one(
    segment: dict[str, Any],
    *,
    lang: str,
    voice: str,
    settings: Any,
    client: Any,
    logger: Any,
    out_dir: Path,
    audio_dir: Path,
    rate: SpeakingRate,
    rate_lock: threading.Lock,
    refine: Any = None,
) -> dict[str, Any]:
    record = dict(segment)
    record["voice"] = voice
    segment_id = int(segment["id"])
    translation = text.normalize_ws(str(segment.get("translation", "")))
    budget = max(0.0, float(segment["end"]) - float(segment["start"]))

    if not translation:
        record["status"] = "skipped"
        record["reason"] = "no translated text for this slot"
        event(
            logger,
            "speak.segment",
            stage="speak",
            segment=segment_id,
            status="skipped",
            reason="no translated text",
        )
        return {"record": record, "fit_status": "ok", "cer": 1.0, "tempo": 1.0,
                "trimmed_s": 0.0, "qa_verdict": "fail", "miss": None}

    spoken = _with_terminator(translation, lang)
    record["spoken_text"] = spoken
    record["predicted_s"] = round(rate.predict_seconds(len(spoken)), 3)

    try:
        audio, verdict, attempts, method = _synthesize(
            spoken, lang=lang, voice=voice, settings=settings, client=client, index=segment_id
        )
    except Exception as exc:  # one bad segment must not sink the language
        record["status"] = "failed"
        record["reason"] = f"{type(exc).__name__}: {exc}"[:300]
        event(
            logger,
            "speak.segment",
            level=40,
            stage="speak",
            segment=segment_id,
            status="failed",
            reason=str(exc)[:200],
        )
        return {"record": record, "fit_status": "miss", "cer": 1.0, "tempo": 1.0,
                "trimmed_s": 0.0, "qa_verdict": "fail", "miss": None}

    samples, sample_rate = media.read_wav_bytes(audio)
    trimmed_samples = media.trim_edges(samples, sample_rate)
    trimmed_s = media.trimmed_seconds(len(samples), len(trimmed_samples), sample_rate)
    actual_s = len(trimmed_samples) / float(sample_rate)
    with rate_lock:
        rate.observe(len(spoken), actual_s)

    decision = fit(actual_s, budget, max_tempo=settings.max_tempo, max_spill_s=settings.max_spill_s)

    # Overrun that even the maximum tempo cannot absorb: ask the LLM to shorten
    # the English, re-translate, and re-synthesize. The Indic text still comes
    # from Indic-Translate, never from the LLM.
    if decision.status == "miss" and refine is not None:
        shortened = _refine_translation(
            refine,
            segment=segment,
            budget_s=budget,
            actual_s=actual_s,
            lang=lang,
            voice=voice,
            settings=settings,
            client=client,
            logger=logger,
        )
        if shortened is not None:
            new_spoken, new_audio, new_verdict, extra_attempts = shortened
            new_samples, new_rate = media.read_wav_bytes(new_audio)
            new_trimmed = media.trim_edges(new_samples, new_rate)
            new_actual = len(new_trimmed) / float(new_rate)
            new_decision = fit(
                new_actual, budget, max_tempo=settings.max_tempo, max_spill_s=settings.max_spill_s
            )
            if new_decision.overrun_s < decision.overrun_s or new_decision.status != "miss":
                spoken = new_spoken
                audio = new_audio
                verdict = new_verdict
                attempts += extra_attempts
                samples, sample_rate = new_samples, new_rate
                trimmed_samples = new_trimmed
                trimmed_s = media.trimmed_seconds(len(new_samples), len(new_trimmed), new_rate)
                actual_s = new_actual
                decision = new_decision
                method = "condensed"
                record["refined"] = True
                with rate_lock:
                    rate.observe(len(spoken), actual_s)
    stretched = media.time_stretch_wav(
        media.to_wav_bytes(trimmed_samples, sample_rate), decision.tempo
    )
    final_samples, final_rate = media.read_wav_bytes(stretched)
    final_s = len(final_samples) / float(final_rate)

    clip_path = audio_dir / f"{segment_id:04d}.wav"
    clip_path.write_bytes(stretched)

    start = float(segment["start"])
    record.update(
        {
            "status": "ok",
            "tts": {
                "characters": len(spoken),
                "duration_s": round(actual_s, 3),
                "attempts": attempts,
                "method": method,
            },
            "audio": {
                "clip": str(clip_path.relative_to(out_dir)),
                "final_duration_s": round(final_s, 3),
                "leading_silence_trimmed_s": round(trimmed_s, 3),
            },
            "fit": {**decision.to_dict(), "placed_at": round(start, 3)},
            "sync_offset_ms": 0.0,
            "qa": verdict.to_dict(),
            "qa_heard": verdict.heard[:400],
        }
    )
    event(
        logger,
        "speak.segment",
        stage="speak",
        segment=segment_id,
        status="ok",
        chars=len(spoken),
        tts_s=round(actual_s, 2),
        budget_s=round(budget, 2),
        tempo=round(decision.tempo, 2),
        fit=decision.status,
        cer=round(verdict.cer, 3),
        qa=verdict.verdict,
        attempts=attempts,
        method=method,
    )

    miss = None
    if decision.status == "miss":
        miss = {
            "id": segment_id,
            "overrun_s": round(decision.overrun_s, 3),
            "start": round(start, 3),
        }

    return {
        "record": record,
        "clip": (start, final_samples),
        "fit_status": decision.status,
        "cer": verdict.cer,
        "tempo": decision.tempo,
        "trimmed_s": trimmed_s,
        "qa_verdict": verdict.verdict,
        "miss": miss,
    }


def _refine_translation(
    refine: Any,
    *,
    segment: dict[str, Any],
    budget_s: float,
    actual_s: float,
    lang: str,
    voice: str,
    settings: Any,
    client: Any,
    logger: Any,
) -> tuple[str, bytes, qa.SpeechQA, int] | None:
    """Shorten the English, re-translate and re-synthesize, or give up quietly."""
    source = str(segment.get("source", "")).strip()
    if not source:
        return None
    segment_id = int(segment["id"])

    try:
        shorter = refine(source, budget_s=budget_s, actual_s=actual_s)
    except Exception as exc:
        event(
            logger,
            "speak.refine_failed",
            level=30,
            stage="speak",
            segment=segment_id,
            reason=str(exc)[:160],
        )
        return None
    if not shorter:
        return None

    spoken = _with_terminator(shorter, lang)
    try:
        audio, verdict, attempts, _ = _synthesize(
            spoken, lang=lang, voice=voice, settings=settings, client=client, index=segment_id
        )
    except Exception as exc:
        event(
            logger,
            "speak.refine_failed",
            level=30,
            stage="speak",
            segment=segment_id,
            reason=str(exc)[:160],
        )
        return None

    event(
        logger,
        "speak.refined",
        stage="speak",
        segment=segment_id,
        chars=len(spoken),
        attempts=attempts,
    )
    return spoken, audio, verdict, attempts


def _synthesize(
    value: str,
    *,
    lang: str,
    voice: str,
    settings: Any,
    client: Any,
    index: int,
) -> tuple[bytes, qa.SpeechQA, int, str]:
    attempts = 0

    def once() -> tuple[bytes, qa.SpeechQA]:
        nonlocal attempts
        attempts += 1
        result = client.speak(
            value, voice=voice, language=lang, style=settings.tts_style, stage="speak", item=index
        )
        heard = _back_transcribe(result.audio, lang=lang, client=client, index=index)
        return result.audio, qa.evaluate(value, heard)

    best_audio, best_verdict = once()
    if best_verdict.verdict == "pass":
        return best_audio, best_verdict, attempts, "direct"

    # A clip too short to split is also too short to judge reliably. Retrying it
    # costs a synthesis call and almost never helps.
    if len(value) < SHORT_RETRY_CHARS or len(value.split()) < 3:
        return best_audio, best_verdict, attempts, "short_text"

    for _ in range(MAX_SYNTH_ATTEMPTS - 1):
        audio, verdict = once()
        if verdict.verdict == "pass":
            return audio, verdict, attempts, "direct"
        if verdict.cer < best_verdict.cer:
            best_audio, best_verdict = audio, verdict

    parts = _halves(value)
    if len(parts) > 1:
        try:
            pieces = [
                client.speak(
                    part,
                    voice=voice,
                    language=lang,
                    style=settings.tts_style,
                    stage="speak",
                    item=index,
                ).audio
                for part in parts
            ]
            attempts += len(parts)
            joined = media.concat_wav_bytes(pieces)
            heard = _back_transcribe(joined, lang=lang, client=client, index=index)
            verdict = qa.evaluate(value, heard)
            if verdict.verdict == "pass" or verdict.cer < best_verdict.cer:
                best_audio, best_verdict = joined, verdict
            if verdict.verdict == "pass":
                return joined, verdict, attempts, "split"
        except Exception:
            pass

    return best_audio, best_verdict, attempts, "best_effort"


def _back_transcribe(audio: bytes, *, lang: str, client: Any, index: int) -> str:
    seconds = media.wav_duration_bytes(audio)
    result = client.transcribe(
        audio,
        language=lang,
        audio_seconds=seconds,
        filename=f"qa{index:04d}.wav",
        stage="qa",
        item=index,
    )
    return str(result.data.get("text", ""))


def _halves(value: str) -> list[str]:
    parts = [part.strip() for part in SENTENCE_SPLIT.split(value) if part.strip()]
    if len(parts) >= 2:
        middle = max(1, len(parts) // 2)
        return [" ".join(parts[:middle]), " ".join(parts[middle:])]
    words = value.split()
    if len(words) < 4:
        return [value]
    middle = len(words) // 2
    return [" ".join(words[:middle]), " ".join(words[middle:])]


def _with_terminator(value: str, lang: str) -> str:
    if not value:
        return value
    if value[-1] in "।॥.!?؟":
        return value
    return value + languages.terminator(lang)
