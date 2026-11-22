"""Back-transcribe narration and check Speak quality.

Uses Indic-Transcribe against each section MP3, computes CER, flags
repeat/omit, retries Speak once on failure, and optionally loudnorms
the full lesson track toward -16 LUFS.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import subprocess
import time
import wave
from pathlib import Path
from typing import Any, Callable, Iterator

from bodhan_lesson.bodhan_client import get_transcribe_api_key, post_multipart
from bodhan_lesson.parallel import get_max_lang_workers, map_parallel
from bodhan_lesson.progress import done, item, stage
from bodhan_lesson.speak import (
    concat_mp3s,
    default_voice_for,
    ffmpeg_available,
    speak_section_text,
)

TRANSCRIBE_PATH = "/v1/audio/transcriptions"
TRANSCRIBE_MODEL = "indic-transcribe"
# Soft fail line for character error rate (assignment target, not hard gate).
CER_FAIL = 0.25
LENGTH_OMIT_RATIO = 0.55
LENGTH_REPEAT_RATIO = 1.45
TARGET_LUFS = -16.0
# Hosted ASR rejects clips longer than ~30s; keep a margin.
CHUNK_SECONDS = 25.0
WAV_RATE = 16000


def normalize_for_cer(text: str) -> str:
    """Lowercase-ish normalize: collapse whitespace, strip punctuation noise."""
    out = (text or "").casefold()
    out = re.sub(r"\s+", " ", out)
    out = re.sub(r"[\"'`*_#]+", "", out)
    return out.strip()


def levenshtein(a: str, b: str) -> int:
    """Classic edit distance (chars)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    # Prefer shorter as columns
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            ins = cur[j - 1] + 1
            delete = prev[j] + 1
            sub = prev[j - 1] + (0 if ca == cb else 1)
            cur.append(min(ins, delete, sub))
        prev = cur
    return prev[-1]


def char_error_rate(reference: str, hypothesis: str) -> float:
    """CER = edit_distance / max(1, len(reference))."""
    ref = normalize_for_cer(reference)
    hyp = normalize_for_cer(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    return levenshtein(ref, hyp) / float(len(ref))


def detect_repeat(reference: str, hypothesis: str) -> bool:
    """Heuristic: transcript much longer, or a long chunk appears twice in a row.

    Length ratio is only applied on longer refs so short titles with spoken
    number forms (e.g. "6" → "सिक्स") are not false-flagged.
    """
    ref = normalize_for_cer(reference)
    hyp = normalize_for_cer(hypothesis)
    if not hyp:
        return False
    if ref and len(ref) >= 40 and len(hyp) >= int(len(ref) * LENGTH_REPEAT_RATIO):
        return True
    # Consecutive duplicate window.
    tokens = hyp.split()
    if len(tokens) >= 8:
        for size in (6, 8, 10):
            if len(tokens) < size * 2:
                continue
            for i in range(len(tokens) - size * 2 + 1):
                a = tokens[i : i + size]
                b = tokens[i + size : i + size * 2]
                if a == b:
                    return True
    return False


def detect_omit(reference: str, hypothesis: str) -> bool:
    """Heuristic: transcript much shorter than script on longer sections.

    Do not compare Arabic digits to ASR text — Speak often reads digits as
    spoken words ("6" → "सिक्स"), which is expected until source-side spoken
    forms land.
    """
    ref = normalize_for_cer(reference)
    hyp = normalize_for_cer(hypothesis)
    if ref and len(ref) >= 40 and len(hyp) <= int(len(ref) * LENGTH_OMIT_RATIO):
        return True
    return False


def mp3_to_wav_16k(mp3_path: Path, wav_path: Path) -> Path:
    """Convert any ffmpeg-readable audio to 16 kHz mono PCM WAV."""
    if not ffmpeg_available():
        raise RuntimeError("ffmpeg not found on PATH (required to prepare audio for Transcribe)")
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(mp3_path),
        "-ac",
        "1",
        "-ar",
        str(WAV_RATE),
        "-c:a",
        "pcm_s16le",
        str(wav_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not wav_path.is_file():
        raise RuntimeError(f"ffmpeg audio→wav failed: {(result.stderr or '')[-300:]}")
    return wav_path


def iter_wav_chunks(
    wav_path: Path, *, seconds: float = CHUNK_SECONDS
) -> Iterator[tuple[int, bytes]]:
    """Yield (index, wav-bytes) windows ≤ `seconds` (Bodhan ASR limit)."""
    with contextlib.closing(wave.open(str(wav_path), "rb")) as wf:
        params = wf.getparams()
        frames_per_chunk = max(1, int(params.framerate * seconds))
        idx = 0
        while True:
            frames = wf.readframes(frames_per_chunk)
            if not frames:
                break
            buf = io.BytesIO()
            with contextlib.closing(wave.open(buf, "wb")) as out:
                out.setparams(params)
                out.writeframes(frames)
            yield idx, buf.getvalue()
            idx += 1


def _transcribe_once(
    audio_path: Path,
    *,
    language: str,
    api_key: str,
    post_fn: Callable[..., dict[str, Any]] | None = None,
) -> str:
    """Upload one short audio file (prefer WAV) to Indic-Transcribe."""
    data = {"model": TRANSCRIBE_MODEL, "language": language}
    mime = "audio/wav" if audio_path.suffix.lower() == ".wav" else "application/octet-stream"

    def file_factory() -> tuple[str, Any, str]:
        return (audio_path.name, audio_path.open("rb"), mime)

    if post_fn is not None:
        result = post_fn(
            TRANSCRIBE_PATH,
            api_key=api_key,
            files={"file": file_factory},
            data=data,
        )
    else:
        result = post_multipart(
            TRANSCRIBE_PATH,
            api_key=api_key,
            files={"file": file_factory},
            data=data,
        )
    text = result.get("text")
    if not isinstance(text, str):
        raise RuntimeError("transcribe response missing text")
    return text


def transcribe_file(
    audio_path: Path,
    *,
    language: str,
    api_key: str | None = None,
    post_fn: Callable[..., dict[str, Any]] | None = None,
    work_dir: Path | None = None,
) -> str:
    """Transcribe one section audio: WAV-normalize, chunk ≤25s, join text.

    Hosted ASR rejects long clips and often rejects Speak MP3 containers; we
    always convert through 16 kHz mono WAV when ffmpeg is available.
    """
    if not audio_path.is_file():
        raise FileNotFoundError(f"Missing audio: {audio_path}")
    key = api_key or get_transcribe_api_key()
    scratch = work_dir or (audio_path.parent / "_work")
    scratch.mkdir(parents=True, exist_ok=True)

    # Prefer existing wav sidecar from Speak; else convert the mp3/other file.
    wav_path = scratch / f"{audio_path.stem}.qa.wav"
    if audio_path.suffix.lower() == ".wav":
        source_wav = audio_path
    elif ffmpeg_available():
        source_wav = mp3_to_wav_16k(audio_path, wav_path)
    else:
        # Unit-test / no-ffmpeg path: send the file as-is (must already be short WAV).
        source_wav = audio_path

    parts: list[str] = []
    chunk_paths: list[Path] = []
    for idx, blob in iter_wav_chunks(source_wav, seconds=CHUNK_SECONDS):
        chunk_path = scratch / f"{audio_path.stem}.qa.part{idx:02d}.wav"
        chunk_path.write_bytes(blob)
        chunk_paths.append(chunk_path)
        parts.append(
            _transcribe_once(
                chunk_path, language=language, api_key=key, post_fn=post_fn
            ).strip()
        )

    if not parts and source_wav.suffix.lower() != ".wav":
        # Non-wav without ffmpeg, or empty wav — last-ditch single upload.
        return _transcribe_once(
            audio_path, language=language, api_key=key, post_fn=post_fn
        ).strip()
    if not parts:
        # Empty audio
        return ""
    return " ".join(p for p in parts if p)


def evaluate_transcript(reference: str, hypothesis: str) -> dict[str, Any]:
    """Compute CER and repeat/omit flags.

    CER above CER_FAIL is recorded as a soft warning only — hard fail is
    reserved for repeat/omit (TTS loops or dropped content).
    """
    cer = char_error_rate(reference, hypothesis)
    repeated = detect_repeat(reference, hypothesis)
    omitted = detect_omit(reference, hypothesis)
    failed = repeated or omitted
    reasons: list[str] = []
    if repeated:
        reasons.append("repeat")
    if omitted:
        reasons.append("omit")
    if cer > CER_FAIL:
        reasons.append(f"cer>{CER_FAIL}")
    return {
        "cer": round(cer, 4),
        "repeat": repeated,
        "omit": omitted,
        "failed": failed,
        "reasons": reasons,
    }


def loudnorm_mp3(path: Path, *, target_lufs: float = TARGET_LUFS) -> dict[str, Any]:
    """Apply ffmpeg loudnorm in place. Returns status info."""
    if not path.is_file():
        return {"status": "skipped", "reason": "missing_file"}
    if not ffmpeg_available():
        return {"status": "skipped", "reason": "ffmpeg_missing"}

    tmp = path.with_suffix(".loudnorm.mp3")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(path),
        "-af",
        f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11",
        "-codec:a",
        "libmp3lame",
        "-qscale:a",
        "2",
        str(tmp),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not tmp.is_file():
        if tmp.is_file():
            tmp.unlink(missing_ok=True)
        return {
            "status": "error",
            "reason": (result.stderr or "loudnorm failed")[-300:],
        }
    tmp.replace(path)
    return {"status": "ok", "target_lufs": target_lufs}


def _qa_one_section(
    section: dict[str, Any],
    *,
    lang: str,
    voice: str,
    work_dir: Path,
    index: int,
    api_key: str | None,
    speak_key: str | None,
    post_fn: Callable[..., dict[str, Any]] | None,
    speak_post_fn: Callable[..., bytes] | None,
) -> dict[str, Any]:
    """Transcribe + evaluate one spoken section; retry Speak once on QA fail."""
    from bodhan_lesson.bodhan_client import get_speak_api_key

    text = section.get("text_for_tts") or ""
    audio_path = Path(section.get("audio_path") or section.get("path") or "")
    qa_box = {
        "cer": None,
        "transcript": None,
        "retries": int(section.get("qa", {}).get("retries") or 0)
        if isinstance(section.get("qa"), dict)
        else 0,
        "repeat": False,
        "omit": False,
        "status": "skipped",
        "reasons": [],
    }
    speak_status = section.get("speak_status")
    if speak_status is not None and speak_status != "ok":
        qa_box["status"] = "skipped"
        qa_box["reasons"] = [f"speak_{speak_status}"]
        section["qa"] = qa_box
        return {"retried": False, "ok": False, "skipped": True}
    if not str(text).strip() or not audio_path.is_file():
        qa_box["status"] = "skipped"
        qa_box["reasons"] = ["no_audio_or_text"]
        section["qa"] = qa_box
        return {"retried": False, "ok": False, "skipped": True}

    try:
        transcript = transcribe_file(
            audio_path,
            language=lang,
            api_key=api_key,
            post_fn=post_fn,
            work_dir=work_dir,
        )
    except Exception as exc:  # noqa: BLE001
        qa_box["status"] = "error"
        qa_box["reasons"] = [f"transcribe:{exc}"]
        section["qa"] = qa_box
        return {"retried": False, "ok": False, "skipped": False, "error": str(exc)}

    ev = evaluate_transcript(str(text), transcript)
    qa_box.update(
        {
            "cer": ev["cer"],
            "transcript": transcript,
            "repeat": ev["repeat"],
            "omit": ev["omit"],
            "status": "fail" if ev["failed"] else "ok",
            "reasons": ev["reasons"],
        }
    )

    retried = False
    if ev["failed"]:
        # One Speak retry, then re-transcribe.
        retried = True
        qa_box["retries"] = int(qa_box["retries"]) + 1
        slug = section.get("slug") or section.get("id") or f"sec_{index:03d}"
        stem = f"{index:02d}-{slug}.{lang}"
        try:
            sk = speak_key or get_speak_api_key()
            result = speak_section_text(
                str(text),
                lang=lang,
                voice=voice,
                work_dir=work_dir,
                stem=stem + ".retry",
                mp3_path=audio_path,
                api_key=sk,
                post_fn=speak_post_fn,
            )
            if result.get("status") == "ok":
                transcript2 = transcribe_file(
                    audio_path,
                    language=lang,
                    api_key=api_key,
                    post_fn=post_fn,
                    work_dir=work_dir,
                )
                ev2 = evaluate_transcript(str(text), transcript2)
                qa_box.update(
                    {
                        "cer": ev2["cer"],
                        "transcript": transcript2,
                        "repeat": ev2["repeat"],
                        "omit": ev2["omit"],
                        "status": "ok" if not ev2["failed"] else "fail",
                        "reasons": ev2["reasons"] or ["retry_still_fail"],
                    }
                )
                if result.get("duration_s") is not None:
                    section["duration_s"] = result["duration_s"]
            else:
                qa_box["reasons"] = list(qa_box["reasons"]) + [
                    f"retry_speak:{result.get('error')}"
                ]
        except Exception as exc:  # noqa: BLE001
            qa_box["reasons"] = list(qa_box["reasons"]) + [f"retry:{exc}"]

    section["qa"] = qa_box
    return {
        "retried": retried,
        "ok": qa_box["status"] == "ok",
        "skipped": False,
        "cer": qa_box["cer"],
    }


def qa_lesson_audio(
    script_path: Path,
    out_dir: Path,
    lang: str,
    *,
    voice: str | None = None,
    api_key: str | None = None,
    speak_key: str | None = None,
    post_fn: Callable[..., dict[str, Any]] | None = None,
    speak_post_fn: Callable[..., bytes] | None = None,
) -> dict[str, Any]:
    """Run QA on one language's narration script + section MP3s."""
    started = time.perf_counter()
    if not script_path.is_file():
        raise FileNotFoundError(f"Missing narration script: {script_path}")

    data = json.loads(script_path.read_text(encoding="utf-8"))
    sections = list(data.get("sections") or [])
    chosen_voice = voice or data.get("voice") or default_voice_for(lang)
    narr_dir = out_dir / lang / "narration"
    work_dir = narr_dir / "_work"
    work_dir.mkdir(parents=True, exist_ok=True)

    ok = 0
    failed = 0
    skipped = 0
    retries = 0
    cers: list[float] = []
    rebuilt = False
    total_sections = len(sections)
    stage("qa", f"lang={lang} sections={total_sections}")

    for index, section in enumerate(sections, start=1):
        # Prefer audio_path written by speak; fall back to conventional path.
        slug = section.get("slug") or section.get("id") or f"sec_{index:03d}"
        if not section.get("audio_path"):
            candidate = narr_dir / f"{index:02d}-{slug}.{lang}.mp3"
            if candidate.is_file():
                section["audio_path"] = str(candidate)

        item("qa", index, total_sections, f"{lang}/{slug}")
        row = _qa_one_section(
            section,
            lang=lang,
            voice=chosen_voice,
            work_dir=work_dir,
            index=index,
            api_key=api_key,
            speak_key=speak_key,
            post_fn=post_fn,
            speak_post_fn=speak_post_fn,
        )
        if row.get("retried"):
            retries += 1
            rebuilt = True
        qa_status = (section.get("qa") or {}).get("status") or (
            "ok" if row.get("ok") else ("skipped" if row.get("skipped") else "fail")
        )
        cer = (section.get("qa") or {}).get("cer")
        cer_bit = f" cer={cer}" if cer is not None else ""
        item("qa", index, total_sections, f"{lang}/{slug}", f"{qa_status}{cer_bit}")
        if row.get("skipped"):
            skipped += 1
        elif row.get("ok"):
            ok += 1
            if row.get("cer") is not None:
                cers.append(float(row["cer"]))
        else:
            failed += 1
            if row.get("cer") is not None:
                cers.append(float(row["cer"]))

    if rebuilt:
        # Rebuild full lesson from current section mp3s (with silence if present).
        mp3s: list[Path] = []
        silence = work_dir / "_silence.mp3"
        for index, section in enumerate(sections, start=1):
            path = Path(section.get("audio_path") or "")
            if not path.is_file():
                continue
            if mp3s and silence.is_file():
                mp3s.append(silence)
            mp3s.append(path)
        lesson_mp3 = out_dir / lang / f"lesson.{lang}.mp3"
        if mp3s:
            try:
                concat_mp3s(mp3s, lesson_mp3)
                data["lesson_mp3"] = str(lesson_mp3)
            except Exception:  # noqa: BLE001
                pass

    lesson_mp3 = Path(data.get("lesson_mp3") or out_dir / lang / f"lesson.{lang}.mp3")
    loudness = loudnorm_mp3(lesson_mp3) if lesson_mp3.is_file() else {
        "status": "skipped",
        "reason": "missing_lesson_mp3",
    }

    data["sections"] = sections
    data["qa_summary"] = {
        "ok": ok,
        "failed": failed,
        "skipped": skipped,
        "retries": retries,
        "mean_cer": round(sum(cers) / len(cers), 4) if cers else None,
        "loudness": loudness,
    }
    script_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    status = "ok" if failed == 0 and ok > 0 else ("partial" if ok else "error")
    done(
        "qa",
        lang=lang,
        ok=ok,
        failed=failed,
        skipped=skipped,
        mean_cer=(round(sum(cers) / len(cers), 4) if cers else None),
    )
    return {
        "lang": lang,
        "status": status,
        "ok": ok,
        "failed": failed,
        "skipped": skipped,
        "retries": retries,
        "mean_cer": round(sum(cers) / len(cers), 4) if cers else None,
        "loudness": loudness,
        "script_path": str(script_path),
        "lesson_mp3": str(lesson_mp3) if lesson_mp3.is_file() else None,
        "latency_s": round(time.perf_counter() - started, 3),
        "error": None if status != "error" else "all sections failed QA",
    }


def qa_spoken_lessons(
    out_dir: Path,
    speaks: list[dict[str, Any]],
    *,
    api_key: str | None = None,
    speak_key: str | None = None,
    post_fn: Callable[..., dict[str, Any]] | None = None,
    speak_post_fn: Callable[..., bytes] | None = None,
) -> list[dict[str, Any]]:
    """QA each successfully spoken language; isolate failures."""
    work = [row for row in speaks if row.get("lang")]
    total = len(work)
    workers = get_max_lang_workers()
    stage("qa", f"{total} language(s), workers={min(workers, max(1, total))}")

    def _one(job: tuple[int, dict[str, Any]]) -> dict[str, Any]:
        index, row = job
        lang = row.get("lang")
        item("qa", index, total, str(lang))
        if row.get("status") not in {"ok", "partial"}:
            item("qa", index, total, str(lang), "skipped")
            return {
                "lang": lang,
                "status": "skipped",
                "ok": 0,
                "failed": 0,
                "skipped": 0,
                "retries": 0,
                "mean_cer": None,
                "loudness": {"status": "skipped", "reason": "speak_not_ok"},
                "latency_s": 0.0,
                "error": row.get("error") or "speak not ok",
            }
        script_path = Path(
            row.get("script_path")
            or (out_dir / lang / f"narration_script.{lang}.json")
        )
        try:
            result = qa_lesson_audio(
                script_path,
                out_dir,
                lang,
                voice=row.get("voice"),
                api_key=api_key,
                speak_key=speak_key,
                post_fn=post_fn,
                speak_post_fn=speak_post_fn,
            )
            item("qa", index, total, str(lang), str(result.get("status")))
            return result
        except Exception as exc:  # noqa: BLE001
            item("qa", index, total, str(lang), f"error: {exc}")
            return {
                "lang": lang,
                "status": "error",
                "ok": 0,
                "failed": 0,
                "skipped": 0,
                "retries": 0,
                "mean_cer": None,
                "loudness": {"status": "skipped"},
                "latency_s": 0.0,
                "error": str(exc),
            }

    results = map_parallel(list(enumerate(work, start=1)), _one)
    ok = sum(1 for row in results if row.get("status") == "ok")
    done("qa", ok=ok, failed=len(results) - ok)
    return results
