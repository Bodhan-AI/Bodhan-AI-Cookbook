"""Synthesize narration audio with Indic-Speak.

Chunks section text, calls POST /v1/audio/speech, converts WAV→MP3 with
ffmpeg, and concatenates sections with short silence gaps.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import wave
from pathlib import Path
from typing import Any, Callable

from bodhan_lesson.bodhan_client import get_speak_api_key, post_bytes
from bodhan_lesson.parallel import get_max_lang_workers, map_parallel
from bodhan_lesson.progress import done, item, stage

SPEAK_PATH = "/v1/audio/speech"
SPEAK_MODEL = "indic-speak"
DEFAULT_STYLE = "educational lecture"
# Soft char budget per Speak request (sentence pack; API wants ~sentence/30s).
DEFAULT_CHUNK_CHARS = 350
SECTION_GAP_MS = 400
AI_GENERATED_NOTE = "AI-generated narration (Indic-Speak; not a human voice clone)."

# Default female voice per lang from Bodhan rules; en uses Amit (cookbook sample).
DEFAULT_VOICES: dict[str, str] = {
    "as": "Prastuti",
    "bn": "Ishita",
    "brx": "Gwrbw",
    "doi": "Preeti",
    "en": "Amit",
    "gu": "Dhara",
    "hi": "Kavya",
    "kn": "Deepika",
    "kok": "Anjali",
    "ks": "Zoon",
    "mai": "Vaidehi",
    "ml": "Lakshmi",
    "mni": "Thoibi",
    "mr": "Anagha",
    "ne": "Srijana",
    "or": "Itishree",
    "pa": "Kaur",
    "sa": "Bharati",
    "sat": "Phulmani",
    "sd": "Moomal",
    "ta": "Anitha",
    "te": "Sravani",
    "ur": "Saba",
}

_SENTENCE_END = re.compile(r"(?<=[।.!?؟])\s+")
_VOICE_LINE = re.compile(r"^([A-Za-z0-9_-]+)\s*:\s*([A-Za-z0-9_-]+)\s*$")


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def default_voice_for(lang: str) -> str:
    return DEFAULT_VOICES.get(lang) or DEFAULT_VOICES.get("en", "Amit")


def load_voices_file(path: Path | None) -> dict[str, str]:
    """Load lang→voice map from a simple YAML/JSON file (no PyYAML required)."""
    if path is None:
        return {}
    if not path.is_file():
        raise FileNotFoundError(f"Voices file not found: {path}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    if path.suffix.lower() == ".json" or text.startswith("{"):
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("voices JSON must be an object of lang→voice")
        return {str(k): str(v) for k, v in data.items()}

    mapping: dict[str, str] = {}
    for line in text.splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        match = _VOICE_LINE.match(raw)
        if not match:
            raise ValueError(f"Bad voices line (want 'hi: Kavya'): {line!r}")
        mapping[match.group(1)] = match.group(2)
    return mapping


def resolve_voice(lang: str, voices: dict[str, str] | None = None) -> str:
    mapping = voices or {}
    return mapping.get(lang) or default_voice_for(lang)


def chunk_for_tts(text: str, *, max_chars: int = DEFAULT_CHUNK_CHARS) -> list[str]:
    """Split on sentence boundaries and pack under max_chars."""
    raw = (text or "").strip()
    if not raw:
        return []
    sentences = [s.strip() for s in _SENTENCE_END.split(raw) if s.strip()]
    if not sentences:
        return [raw]

    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for sentence in sentences:
        extra = len(sentence) + (1 if current else 0)
        if current and size + extra > max_chars:
            chunks.append(" ".join(current))
            current = [sentence]
            size = len(sentence)
        else:
            current.append(sentence)
            size += extra
    if current:
        chunks.append(" ".join(current))
    return chunks


def synthesize_wav(
    text: str,
    *,
    lang: str,
    voice: str,
    style: str = DEFAULT_STYLE,
    api_key: str | None = None,
    post_fn: Callable[..., bytes] | None = None,
) -> bytes:
    """One Indic-Speak call; returns WAV bytes."""
    key = api_key or get_speak_api_key()
    poster = post_fn or post_bytes
    instructions: dict[str, str] = {"lang": lang}
    if style:
        instructions["style"] = style
    body = {
        "model": SPEAK_MODEL,
        "input": text,
        "voice": voice,
        "instructions": json.dumps(instructions, ensure_ascii=False),
        "response_format": "wav",
    }
    return poster(SPEAK_PATH, body, api_key=key)


def write_wav(path: Path, wav_bytes: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(wav_bytes)


def wav_duration_s(path: Path) -> float:
    with wave.open(str(path), "rb") as wf:
        frames = wf.getnframes()
        rate = wf.getframerate() or 1
        return frames / float(rate)


def concat_wavs(paths: list[Path], out_path: Path) -> float:
    """Concatenate WAV clips (same format) into one file; return duration seconds."""
    if not paths:
        raise ValueError("no wav clips to concat")
    frames: list[bytes] = []
    params = None
    for path in paths:
        with wave.open(str(path), "rb") as wf:
            if params is None:
                params = wf.getparams()
            frames.append(wf.readframes(wf.getnframes()))
    assert params is not None
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as wf:
        wf.setparams(params)
        for block in frames:
            wf.writeframes(block)
    return wav_duration_s(out_path)


def make_silence_wav(ref_wav: Path, out_path: Path, *, ms: int = SECTION_GAP_MS) -> Path:
    """Write a short silence clip matching ref_wav format."""
    with wave.open(str(ref_wav), "rb") as wf:
        params = wf.getparams()
        rate = wf.getframerate()
        width = wf.getsampwidth()
        channels = wf.getnchannels()
    nframes = max(1, int(rate * (ms / 1000.0)))
    silence = b"\x00" * (nframes * width * channels)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as wf:
        wf.setparams(params)
        wf.writeframes(silence)
    return out_path


def wav_to_mp3(wav_path: Path, mp3_path: Path) -> None:
    """Convert WAV to MP3 via ffmpeg."""
    if not ffmpeg_available():
        raise RuntimeError(
            "ffmpeg not found on PATH (required for MP3 output and concat)"
        )
    mp3_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(wav_path),
        "-codec:a",
        "libmp3lame",
        "-qscale:a",
        "2",
        str(mp3_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg wav→mp3 failed: {result.stderr[-300:]}")


def concat_mp3s(paths: list[Path], out_path: Path) -> None:
    """Concat MP3s with ffmpeg concat demuxer."""
    if not paths:
        raise ValueError("no mp3 clips to concat")
    if not ffmpeg_available():
        raise RuntimeError("ffmpeg not found on PATH")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    list_path = out_path.with_suffix(".concat.txt")
    lines = [f"file '{p.resolve().as_posix()}'" for p in paths]
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c",
            "copy",
            str(out_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            # Re-encode fallback when codecs differ.
            cmd = [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_path),
                "-codec:a",
                "libmp3lame",
                "-qscale:a",
                "2",
                str(out_path),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                raise RuntimeError(f"ffmpeg concat failed: {result.stderr[-300:]}")
    finally:
        if list_path.is_file():
            list_path.unlink(missing_ok=True)


def speak_section_text(
    text: str,
    *,
    lang: str,
    voice: str,
    work_dir: Path,
    stem: str,
    mp3_path: Path,
    api_key: str | None = None,
    post_fn: Callable[..., bytes] | None = None,
    style: str = DEFAULT_STYLE,
    max_chars: int = DEFAULT_CHUNK_CHARS,
) -> dict[str, Any]:
    """Speak one section's text_for_tts into an MP3. Retries each chunk once."""
    chunks = chunk_for_tts(text, max_chars=max_chars)
    if not chunks:
        return {
            "status": "skipped",
            "skip_reason": "empty_text",
            "path": None,
            "wav_path": None,
            "duration_s": 0.0,
            "chunks": 0,
            "retries": 0,
            "error": None,
        }

    work_dir.mkdir(parents=True, exist_ok=True)
    wav_parts: list[Path] = []
    retries = 0

    for index, chunk in enumerate(chunks):
        part_wav = work_dir / f"{stem}.part{index:02d}.wav"
        try:
            wav_bytes = synthesize_wav(
                chunk, lang=lang, voice=voice, style=style, api_key=api_key, post_fn=post_fn
            )
            write_wav(part_wav, wav_bytes)
            wav_parts.append(part_wav)
        except Exception as exc:  # noqa: BLE001
            retries += 1
            try:
                wav_bytes = synthesize_wav(
                    chunk,
                    lang=lang,
                    voice=voice,
                    style=style,
                    api_key=api_key,
                    post_fn=post_fn,
                )
                write_wav(part_wav, wav_bytes)
                wav_parts.append(part_wav)
            except Exception as exc2:  # noqa: BLE001
                return {
                    "status": "error",
                    "skip_reason": None,
                    "path": None,
                    "wav_path": None,
                    "duration_s": 0.0,
                    "chunks": len(chunks),
                    "retries": retries,
                    "error": str(exc2) or str(exc),
                }

    section_wav = work_dir / f"{stem}.wav"
    duration = concat_wavs(wav_parts, section_wav)
    wav_to_mp3(section_wav, mp3_path)
    return {
        "status": "ok",
        "skip_reason": None,
        "path": str(mp3_path),
        "wav_path": str(section_wav),
        "duration_s": round(duration, 3),
        "chunks": len(chunks),
        "retries": retries,
        "error": None,
    }


def speak_lesson_script(
    script_path: Path,
    out_dir: Path,
    lang: str,
    *,
    voice: str | None = None,
    api_key: str | None = None,
    post_fn: Callable[..., bytes] | None = None,
    style: str = DEFAULT_STYLE,
) -> dict[str, Any]:
    """Speak all ok sections from narration_script JSON; build full lesson mp3."""
    started = time.perf_counter()
    if not script_path.is_file():
        raise FileNotFoundError(f"Missing narration script: {script_path}")

    data = json.loads(script_path.read_text(encoding="utf-8"))
    sections = list(data.get("sections") or [])
    chosen_voice = voice or default_voice_for(lang)
    narr_dir = out_dir / lang / "narration"
    narr_dir.mkdir(parents=True, exist_ok=True)
    work_dir = narr_dir / "_work"
    work_dir.mkdir(parents=True, exist_ok=True)

    section_rows: list[dict[str, Any]] = []
    mp3_for_concat: list[Path] = []
    silence_path: Path | None = None
    ok = 0
    skipped = 0
    failed = 0
    total_retries = 0
    cursor_s = 0.0

    total_sections = len(sections)
    stage("speak", f"lang={lang} sections={total_sections} voice={chosen_voice}")
    for index, section in enumerate(sections, start=1):
        sec_id = section.get("id") or f"sec_{index:03d}"
        slug = section.get("slug") or sec_id
        stem = f"{index:02d}-{slug}.{lang}"
        text = section.get("text_for_tts") or ""
        item("speak", index, total_sections, f"{lang}/{slug}")
        if section.get("status") != "ok" or not str(text).strip():
            reason = section.get("skip_reason") or "section_not_ok"
            section_rows.append(
                {
                    "id": sec_id,
                    "slug": slug,
                    "status": "skipped",
                    "skip_reason": reason,
                    "path": None,
                    "duration_s": 0.0,
                    "start_s": None,
                    "chunks": 0,
                    "retries": 0,
                    "error": None,
                }
            )
            skipped += 1
            item("speak", index, total_sections, f"{lang}/{slug}", "skipped")
            continue

        final_mp3 = narr_dir / f"{stem}.mp3"
        result = speak_section_text(
            str(text),
            lang=lang,
            voice=chosen_voice,
            work_dir=work_dir,
            stem=stem,
            mp3_path=final_mp3,
            api_key=api_key,
            post_fn=post_fn,
            style=style,
        )
        total_retries += int(result.get("retries") or 0)
        if result["status"] != "ok":
            failed += 1
            section_rows.append(
                {
                    "id": sec_id,
                    "slug": slug,
                    "status": result["status"],
                    "skip_reason": result.get("skip_reason"),
                    "path": None,
                    "duration_s": 0.0,
                    "start_s": None,
                    "chunks": result.get("chunks") or 0,
                    "retries": result.get("retries") or 0,
                    "error": result.get("error"),
                }
            )
            item(
                "speak",
                index,
                total_sections,
                f"{lang}/{slug}",
                f"error: {result.get('error')}",
            )
            continue

        duration = float(result.get("duration_s") or 0.0)
        section_rows.append(
            {
                "id": sec_id,
                "slug": slug,
                "status": "ok",
                "skip_reason": None,
                "path": str(final_mp3),
                "duration_s": duration,
                "start_s": round(cursor_s, 3),
                "chunks": result.get("chunks") or 0,
                "retries": result.get("retries") or 0,
                "error": None,
            }
        )
        ok += 1
        item(
            "speak",
            index,
            total_sections,
            f"{lang}/{slug}",
            f"ok {duration:.1f}s",
        )

        if mp3_for_concat:
            if silence_path is None:
                ref = Path(result.get("wav_path") or "")
                if not ref.is_file():
                    wavs = sorted(work_dir.glob("*.wav"))
                    ref = wavs[0] if wavs else Path()
                if ref.is_file():
                    silence_wav = work_dir / "_silence.wav"
                    make_silence_wav(ref, silence_wav)
                    silence_mp3 = work_dir / "_silence.mp3"
                    wav_to_mp3(silence_wav, silence_mp3)
                    silence_path = silence_mp3
            if silence_path and silence_path.is_file():
                mp3_for_concat.append(silence_path)
                cursor_s += SECTION_GAP_MS / 1000.0
        mp3_for_concat.append(final_mp3)
        cursor_s += duration

    lesson_mp3_path = out_dir / lang / f"lesson.{lang}.mp3"
    lesson_status = "ok"
    lesson_error = None
    if mp3_for_concat:
        try:
            concat_mp3s(mp3_for_concat, lesson_mp3_path)
        except Exception as exc:  # noqa: BLE001
            lesson_status = "error"
            lesson_error = str(exc)
            lesson_mp3_path = Path()
    else:
        lesson_status = "error"
        lesson_error = "no speakable sections"

    # Persist markers back onto narration script.
    by_id = {row["id"]: row for row in section_rows}
    for section in sections:
        sid = section.get("id")
        row = by_id.get(sid) if sid else None
        if not row:
            continue
        section["audio_path"] = row.get("path")
        section["duration_s"] = row.get("duration_s")
        section["start_s"] = row.get("start_s")
        section["speak_status"] = row.get("status")
        section["speak_error"] = row.get("error")
    data["sections"] = sections
    data["voice"] = chosen_voice
    data["style"] = style
    data["ai_generated"] = True
    data["ai_generated_note"] = AI_GENERATED_NOTE
    data["lesson_mp3"] = str(lesson_mp3_path) if lesson_status == "ok" else None
    script_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    status = "ok" if ok and lesson_status == "ok" else ("partial" if ok else "error")
    done("speak", lang=lang, ok=ok, skipped=skipped, failed=failed)
    return {
        "lang": lang,
        "status": status,
        "voice": chosen_voice,
        "style": style,
        "script_path": str(script_path),
        "lesson_mp3": str(lesson_mp3_path) if lesson_status == "ok" else None,
        "sections_ok": ok,
        "sections_skipped": skipped,
        "sections_failed": failed,
        "sections": section_rows,
        "retries": total_retries,
        "ai_generated": True,
        "latency_s": round(time.perf_counter() - started, 3),
        "error": lesson_error
        if status != "ok"
        else (None if failed == 0 else f"{failed} section(s) failed"),
    }


def speak_narrated_lessons(
    out_dir: Path,
    narrations: list[dict[str, Any]],
    *,
    voices: dict[str, str] | None = None,
    voices_path: Path | None = None,
    api_key: str | None = None,
    post_fn: Callable[..., bytes] | None = None,
    style: str = DEFAULT_STYLE,
) -> list[dict[str, Any]]:
    """Speak each successful narration script; isolate per-language failures."""
    voice_map = dict(DEFAULT_VOICES)
    if voices_path is not None:
        voice_map.update(load_voices_file(voices_path))
    if voices:
        voice_map.update(voices)

    work = [row for row in narrations if row.get("lang")]
    total = len(work)
    workers = get_max_lang_workers()
    stage("speak", f"{total} language(s), workers={min(workers, max(1, total))}")

    def _one(job: tuple[int, dict[str, Any]]) -> dict[str, Any]:
        index, row = job
        lang = row.get("lang")
        item("speak", index, total, str(lang))
        if row.get("status") != "ok" or not row.get("path"):
            item("speak", index, total, str(lang), "skipped")
            return {
                "lang": lang,
                "status": "skipped",
                "voice": resolve_voice(lang, voice_map),
                "lesson_mp3": None,
                "sections_ok": 0,
                "sections_skipped": 0,
                "sections_failed": 0,
                "sections": [],
                "retries": 0,
                "latency_s": 0.0,
                "error": row.get("error") or "narration not ok",
                "ai_generated": True,
            }
        try:
            result = speak_lesson_script(
                Path(row["path"]),
                out_dir,
                lang,
                voice=resolve_voice(lang, voice_map),
                api_key=api_key,
                post_fn=post_fn,
                style=style,
            )
            item("speak", index, total, str(lang), str(result.get("status")))
            return result
        except Exception as exc:  # noqa: BLE001
            item("speak", index, total, str(lang), f"error: {exc}")
            return {
                "lang": lang,
                "status": "error",
                "voice": resolve_voice(lang, voice_map),
                "lesson_mp3": None,
                "sections_ok": 0,
                "sections_skipped": 0,
                "sections_failed": 0,
                "sections": [],
                "retries": 0,
                "latency_s": 0.0,
                "error": str(exc),
                "ai_generated": True,
            }

    results = map_parallel(list(enumerate(work, start=1)), _one)
    ok = sum(1 for row in results if row.get("status") == "ok")
    done("speak", ok=ok, failed=len(results) - ok)
    return results
