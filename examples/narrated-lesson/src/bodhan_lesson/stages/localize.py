"""Stages 6-8: translate the English narration script, speak it, back-transcribe it and assemble.

Input is `source/narration_script.en.json`; output, per language, is
`<lang>/narration_script.<lang>.json` (the exact text sent to the TTS, voices, durations, checks and
back-transcription CER) and `<lang>/narration/<slug>.<lang>.mp3` plus `<lang>/lesson.<lang>.mp3`.
A failing unit, chunk or language is recorded and skipped; the rest of the run continues.
Languages run in parallel; within a language all units are translated in one pool and all chunks
spoken in another, so workers never idle at section boundaries.
"""

from __future__ import annotations

import logging
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from bodhan_lesson import config, storage
from bodhan_lesson.audio import ffmpeg, wav
from bodhan_lesson.clients.bodhan import BodhanClient, BodhanError, TruncatedTranslation
from bodhan_lesson.text import cer, chunking

log = logging.getLogger(__name__)

# Seconds of speech per character stay within this band for sane audio; outside it the chunk
# is suspiciously short (omission) or long (repetition or silence).
SECONDS_PER_CHAR = (0.02, 0.25)


# ---- translation -------------------------------------------------------------------------
def translate_unit(client: BodhanClient, text: str, language: str) -> tuple[str, list[str]]:
    """Translate one narration unit; returns (translation, warnings). English is used as is."""
    if language == "en":
        return text, []
    try:
        return client.translate(text, language), []
    except TruncatedTranslation:
        parts = [client.translate(sentence, language) for sentence in chunking.split_sentences(text)]
        return " ".join(parts), ["translated sentence by sentence (output budget reached)"]


# ---- speech and back-check ----------------------------------------------------------------
def _check(client: BodhanClient, audio: bytes, text: str, language: str) -> dict[str, Any]:
    """Back-transcribe one chunk and score it. A missing key or API error is recorded, not raised."""
    if not client.can_transcribe:
        return {}
    try:
        transcript = client.transcribe(audio, language)
    except BodhanError as exc:
        return {"check_error": str(exc)[:200]}
    distance, length = cer.errors(text, transcript)
    return {"transcript": transcript, "cer_errors": distance, "cer_len": length,
            "cer": round(distance / length, 4) if length else 0.0}


def _badness(candidate: dict[str, Any]) -> float:
    if candidate["status"] == "suspect_duration":
        return 2.0
    return candidate.get("cer", 0.0)


def speak_chunk(client: BodhanClient, text: str, language: str) -> dict[str, Any]:
    """Speak one chunk and back-check it. If the audio looks wrong (odd duration, or a transcript far
    from the text: repetition or omission) the alternate voice is tried and the better attempt is
    kept. Never raises."""
    primary, alternate = config.VOICES[language]
    attempts: list[dict[str, Any]] = []
    best: dict[str, Any] | None = None
    for voice in (primary, alternate):
        try:
            audio = client.speak(text, voice, language)
            seconds = wav.duration_s(audio)
        except (BodhanError, wav.WavError) as exc:
            attempts.append({"voice": voice, "error": str(exc)[:200]})
            continue
        per_char = seconds / max(1, len(text))
        suspect = not SECONDS_PER_CHAR[0] <= per_char <= SECONDS_PER_CHAR[1]
        check = _check(client, audio, text, language)
        attempts.append({"voice": voice, "duration_s": round(seconds, 2),
                         **{k: v for k, v in check.items() if k in ("cer", "check_error")}})
        high_cer = check.get("cer", 0.0) > config.CER_RESPEAK_THRESHOLD
        status = "suspect_duration" if suspect else ("high_cer" if high_cer else "ok")
        candidate = {"text": text, "voice": voice, "audio": audio, "duration_s": round(seconds, 2),
                     "status": status, **check}
        if best is None or _badness(candidate) < _badness(best):
            best = candidate
        if status == "ok":
            break
    if best is None:
        return {"text": text, "voice": None, "audio": None, "duration_s": 0.0, "status": "failed",
                "attempts": attempts}
    best["attempts"] = attempts
    return best


def _assemble(units: list[dict[str, Any]]) -> tuple[bytes | None, int]:
    """One WAV for a section: chunk gaps inside a unit, a longer gap between units."""
    parts: list[bytes] = []
    rate = 24000
    for unit in units:
        good = [c for c in unit["chunks"] if c.get("audio")]
        for index, chunk in enumerate(good):
            frames, rate = wav.read_wav(chunk["audio"])
            if parts:
                parts.append(wav.silence(config.GAP_CHUNK_S if index else config.GAP_UNIT_S, rate))
            parts.append(frames)
    return (wav.join(parts, rate), rate) if parts else (None, rate)


def _cer_of(units: list[dict[str, Any]]) -> float | None:
    chunks = [c for u in units for c in u["chunks"] if "cer" in c]
    length = sum(c["cer_len"] for c in chunks)
    return round(sum(c["cer_errors"] for c in chunks) / length, 4) if length else None


# ---- one language ----------------------------------------------------------------------------
def localize_language(script: dict[str, Any], language: str, client: BodhanClient, out_dir: Path, *,
                      speak: bool = True) -> dict[str, Any]:
    """Translate, speak and back-check every section; returns this language's report."""
    started = time.monotonic()
    lang_dir = out_dir / language
    audio_dir = lang_dir / "narration"
    voice = config.VOICES[language][0]
    report: dict[str, Any] = {"language": language, "voice": voice, "units": 0, "units_failed": 0,
                              "chunks": 0, "chunks_failed": 0, "chunks_suspect": 0, "chunks_high_cer": 0,
                              "audio_seconds": 0.0, "cer": None, "sections": [], "errors": [], "timing_s": {}}

    def translate(unit: dict[str, Any]) -> dict[str, Any]:
        try:
            text, warnings = translate_unit(client, unit["text"], language)
            return {"id": unit["id"], "kind": unit["kind"], "text_en": unit["text"], "text": text,
                    "warnings": warnings, "chunks": []}
        except BodhanError as exc:
            report["errors"].append(f"{unit['id']}: translation failed: {exc}")
            return {"id": unit["id"], "kind": unit["kind"], "text_en": unit["text"], "text": "",
                    "warnings": [f"translation failed: {exc}"], "chunks": []}

    with ThreadPoolExecutor(config.TRANSLATE_WORKERS) as pool:
        translated = list(pool.map(translate, [u for sec in script["sections"] for u in sec["units"]]))
    report["timing_s"]["translate"] = round(time.monotonic() - started, 1)

    sections: list[dict[str, Any]] = []
    cursor = 0
    for section in script["sections"]:
        count = len(section["units"])
        sections.append({"section_id": section["section_id"], "title": section["title"],
                         "slug": section["slug"], "units": translated[cursor:cursor + count]})
        cursor += count

    mark = time.monotonic()
    if speak:
        jobs = [(unit, text) for sec in sections for unit in sec["units"]
                for text in chunking.chunk_text(unit["text"], config.TTS_MAX_CHARS)]
        with ThreadPoolExecutor(config.TTS_WORKERS) as pool:
            results = list(pool.map(lambda job: speak_chunk(client, job[1], language), jobs))
        for (unit, _), result in zip(jobs, results):
            unit["chunks"].append(result)
    report["timing_s"]["speak_and_check"] = round(time.monotonic() - mark, 1)

    mark = time.monotonic()
    wavs: list[bytes] = []
    for section in sections:
        section["cer"] = _cer_of(section["units"])
        entry = {"section_id": section["section_id"], "audio": None, "duration_s": 0.0, "cer": section["cer"]}
        if speak:
            audio, _ = _assemble(section["units"])
            if audio:
                entry["duration_s"] = round(wav.duration_s(audio), 2)
                entry["audio"] = _encode(audio, audio_dir / f"{section['slug']}.{language}.mp3",
                                         section["title"], voice, report)
                wavs.append(audio)
        report["sections"].append(entry)
    if speak and wavs:
        frames: list[bytes] = []
        rate = 24000
        for index, audio in enumerate(wavs):
            data, rate = wav.read_wav(audio)
            frames += ([wav.silence(config.GAP_SECTION_S, rate)] if index else []) + [data]
        full = wav.join(frames, rate)
        report["lesson_audio"] = _encode(full, lang_dir / f"lesson.{language}.mp3", "Lesson", voice, report)
        report["audio_seconds"] = round(wav.duration_s(full), 1)
    report["timing_s"]["assemble"] = round(time.monotonic() - mark, 1)

    translated_chars = spoken_chars = 0
    for section in sections:
        for unit in section["units"]:
            report["units"] += 1
            report["units_failed"] += not unit["text"]
            translated_chars += len(unit["text"]) if language != "en" else 0
            for chunk in unit["chunks"]:
                report["chunks"] += 1
                report["chunks_failed"] += chunk["status"] == "failed"
                report["chunks_suspect"] += chunk["status"] == "suspect_duration"
                report["chunks_high_cer"] += chunk["status"] == "high_cer"
                spoken_chars += len(chunk["text"])
                chunk.pop("audio", None)  # bytes are on disk in the MP3 and the TTS cache
    report["cer"] = _cer_of([u for s in sections for u in s["units"]])
    report["timing_s"]["total"] = round(time.monotonic() - started, 1)
    transcribed_min = report["audio_seconds"] / 60 if client.can_transcribe else 0.0
    report["cost_estimate_inr"] = round(
        translated_chars / 10000 * config.PRICE_TRANSLATE_PER_10K_TOKENS  # ~1 token per character
        + spoken_chars * config.PRICE_SPEAK_PER_CHAR
        + transcribed_min * config.PRICE_TRANSCRIBE_PER_MIN, 2)
    back_check = ("every chunk back-transcribed with Indic-Transcribe; CER against the TTS text"
                  if client.can_transcribe else "not run: no Indic-Transcribe key was available")
    storage.write_json(lang_dir / f"narration_script.{language}.json", {
        "language": language, "ai_generated": True, "voice": voice,
        "ai_label": f"AI-generated narration: Bodhan Indic-Speak, voice {voice}",
        "back_check": back_check, "cer": report["cer"], "sections": sections})
    return report


def _encode(audio: bytes, mp3: Path, title: str, voice: str, report: dict[str, Any]) -> str | None:
    mp3.parent.mkdir(parents=True, exist_ok=True)
    comment = f"AI-generated narration: Bodhan Indic-Speak, voice {voice}"
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "in.wav"
        source.write_bytes(audio)
        if not ffmpeg.available():
            fallback = mp3.with_suffix(".wav")
            fallback.write_bytes(audio)
            report["errors"].append("ffmpeg not found: wrote WAV instead of MP3")
            return str(fallback)
        try:
            ffmpeg.encode_mp3(source, mp3, title=title, comment=comment)
        except ffmpeg.FfmpegError as exc:
            report["errors"].append(f"{mp3.name}: {exc}")
            return None
    return str(mp3)


def localize(out_dir: Path, languages: list[str], client: BodhanClient, *, speak: bool = True) -> dict[str, Any]:
    """Run every language in parallel; one failing language does not stop the others."""
    script = storage.read_json(out_dir / "source" / "narration_script.en.json")
    started = time.monotonic()

    def run(language: str) -> dict[str, Any]:
        try:
            return localize_language(script, language, client, out_dir, speak=speak)
        except Exception as exc:  # noqa: BLE001 - a language failure is reported, never fatal
            log.exception("language %s failed", language)
            return {"language": language, "fatal_error": f"{type(exc).__name__}: {exc}"}

    with ThreadPoolExecutor(max(1, len(languages))) as pool:
        reports = dict(zip(languages, pool.map(run, languages)))
    summary = {"languages": reports, "wall_time_s": round(time.monotonic() - started, 1),
               "translate_calls": client.translate_calls, "speak_calls": client.speak_calls,
               "transcribe_calls": client.transcribe_calls}
    storage.write_json(out_dir / "report.json", summary)
    return summary
