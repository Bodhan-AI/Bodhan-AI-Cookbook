"""Tool implementations. Each tool: validate -> handle model limits internally -> typed result or structured error.

Tools never raise: `guarded` converts every failure into `ok=False` + ErrorInfo so a stdio client can never be crashed
by bad input. Files move by path reference only (inputs anywhere readable; outputs under BODHAN_WORKDIR).
"""
from __future__ import annotations

import array
import functools
import hashlib
import math
import re
import sys
import threading
import time
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from . import audio, config, pricing, textutil
from .cache import sha256_file
from .client import client
from .errors import ErrorInfo, ToolFailure

PREVIEW_CHARS = 600
TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".srt", ".vtt", ".csv", ".json", ".html", ".htm", ".tex"}
AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".amr", ".webm"}
VIDEO_SUFFIXES = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".3gp"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
KNOWN_FILE_SUFFIXES = TEXT_SUFFIXES | AUDIO_SUFFIXES | VIDEO_SUFFIXES | IMAGE_SUFFIXES | {".pdf"}


# ------------------------------------------------------------------------------------------------
# result models
# ------------------------------------------------------------------------------------------------
class ToolResult(BaseModel):
    ok: bool = Field(default=True, description="False when the call failed; then `error` explains why and every other field is empty.")
    error: ErrorInfo | None = Field(default=None, description="Present only when ok is false.")
    cost_inr: float = Field(default=0.0, description="Rupees spent on Bodhan models by this call (0 for cache hits and local tools).")
    latency_s: float = Field(default=0.0, description="Wall-clock seconds the call took.")
    cache_hits: int = Field(default=0, description="Number of underlying model calls answered from the on-disk cache.")
    warnings: list[str] = Field(default_factory=list, description="Non-fatal issues the caller should know about.")


def guarded(model_cls: type[ToolResult]) -> Callable:
    def deco(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            t0 = time.perf_counter()
            try:
                result = fn(*args, **kwargs)
            except ToolFailure as exc:
                result = model_cls(ok=False, error=exc.info)
            except Exception as exc:  # never crash the stdio server
                print(f"[bodhan_mcp] internal error in {fn.__name__}: {traceback.format_exc()}", file=sys.stderr)
                result = model_cls(ok=False, error=ErrorInfo(
                    error_code="INTERNAL_ERROR", message=f"Unexpected {type(exc).__name__}: {str(exc)[:200]}", retryable=False,
                    hint="Check the arguments (types, paths). If they look right, this is a bug in the tool."))
            if result.latency_s == 0.0:
                result.latency_s = round(time.perf_counter() - t0, 3)
            result.cost_inr = round(result.cost_inr, 5)
            return result
        return wrapper
    return deco


# ------------------------------------------------------------------------------------------------
# shared validation helpers
# ------------------------------------------------------------------------------------------------
_NAME_TO_CODE: dict[str, str] = {}


def normalize_language(value: str, model: str, *, allow_auto: bool = False, role: str = "language") -> str:
    """Accept a code ('ta'), a regional spelling with a hyphen suffix, or a language name ('Tamil'); return the Bodhan code or raise UNSUPPORTED_LANGUAGE."""
    if not _NAME_TO_CODE:
        for code, name in config.language_names().items():
            _NAME_TO_CODE[name.lower()] = code
            _NAME_TO_CODE[name.split(" (")[0].lower()] = code
    raw = (value or "").strip()
    if not raw:
        raise ToolFailure("INVALID_ARGUMENT", f"{role} is empty.", hint="Pass an ISO code such as 'hi', 'ta', 'bn' (or 'en').")
    if allow_auto and raw.lower() == "auto":
        return "auto"
    supported = config.languages_for(model)
    if raw in supported:
        return raw
    low = raw.lower()
    for cand in (low, low.split("-")[0], _NAME_TO_CODE.get(low, "")):
        for s in supported:
            if s.lower() == cand:
                return s
    raise ToolFailure(
        "UNSUPPORTED_LANGUAGE",
        f"{role} {raw!r} is not supported by {model}.",
        hint=f"Supported codes: {', '.join(supported)}. Languages such as French, Spanish or Chinese are not supported by any Bodhan model; "
             "do not attempt a substitute - report the limitation instead.")


def resolve_input_file(path: str, *, kinds: set[str] | None = None, what: str = "file") -> Path:
    if not isinstance(path, str) or not path.strip():
        raise ToolFailure("INVALID_ARGUMENT", f"{what} path is empty.", hint="Pass a path to an existing file.")
    p = Path(path.strip().strip('"')).expanduser()
    if not p.exists():
        raise ToolFailure("FILE_NOT_FOUND", f"No such file: {path}", hint="Check the path; use inspect_media on the input directory to list files.")
    if p.is_dir():
        raise ToolFailure("INVALID_ARGUMENT", f"{path} is a directory, not a file.", hint="Pass one file path from inside it.")
    if p.stat().st_size == 0:
        raise ToolFailure("EMPTY_INPUT", f"{p.name} is empty (0 bytes).", hint="Provide a non-empty file.")
    if kinds is not None and p.suffix.lower() not in kinds and audio.sniff_magic(p) == "unknown":
        raise ToolFailure("UNSUPPORTED_FILE_TYPE", f"{p.name} has an unsupported type ({p.suffix or 'no extension'}).",
                          hint=f"Supported extensions: {', '.join(sorted(kinds))}.")
    return p


def _read_text_file(p: Path) -> str:
    raw = p.read_bytes()
    if b"\x00" in raw[:4096]:
        raise ToolFailure("UNSUPPORTED_FILE_TYPE", f"{p.name} looks binary, not text.",
                          hint="For audio use transcribe_audio; for images/PDF use ocr_document, then pass the Markdown path on.")
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ToolFailure("UNSUPPORTED_FILE_TYPE", f"{p.name} is not valid UTF-8 text.", hint="Re-save the file as UTF-8.")


def resolve_text_input(text_or_path: str, *, what: str = "text_or_path") -> tuple[str, Path | None]:
    """Return (text, source_file). Inline text is also saved under workdir/inputs so verify tools can reference it."""
    if not isinstance(text_or_path, str) or not text_or_path.strip():
        raise ToolFailure("EMPTY_INPUT", f"{what} is empty.", hint="Pass non-empty text or the path of a text file.")
    s = text_or_path.strip()
    looks_like_path = "\n" not in s and len(s) < 400 and Path(s.strip('"')).suffix.lower() in KNOWN_FILE_SUFFIXES
    if looks_like_path or ("\n" not in s and len(s) < 260 and Path(s.strip('"')).expanduser().is_file()):
        p = resolve_input_file(s, what="text file")
        if p.suffix.lower() not in TEXT_SUFFIXES:
            raise ToolFailure("UNSUPPORTED_FILE_TYPE", f"{p.name} is not a text file.",
                              hint="For audio use transcribe_audio; for images/PDF use ocr_document and pass the Markdown path it returns.")
        text = _read_text_file(p)
        src: Path | None = p
    else:
        text, src = s, None
    if not text.strip():
        raise ToolFailure("EMPTY_INPUT", "The text is empty or whitespace only.", hint="Provide text with content.")
    if len(text) > config.MAX_TEXT_CHARS:
        raise ToolFailure("INPUT_TOO_LARGE", f"Text is {len(text):,} characters; the limit per call is {config.MAX_TEXT_CHARS:,}.",
                          hint="Split the text into parts (for example by chapter) and call the tool once per part.")
    if src is None:
        src = save_text(text, "inputs", "txt")
    return text, src


def save_text(text: str, stem: str, suffix: str = "txt") -> Path:
    d = config.work_dir() / "text"
    d.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    p = d / f"{stem}_{h}.{suffix}"
    p.write_text(text, encoding="utf-8")
    return p


def out_file(stem: str, key: str, suffix: str) -> Path:
    d = config.work_dir() / "artifacts"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{stem}_{key[:10]}{suffix}"


def preview(text: str, n: int = PREVIEW_CHARS) -> str:
    t = text.strip()
    return t if len(t) <= n else t[:n].rstrip() + f" ... [{len(t) - n} more characters in the file]"


def _parallel(fn: Callable, items: list, workers: int | None = None) -> list:
    workers = min(workers or config.max_concurrency(), max(len(items), 1))
    if workers <= 1 or len(items) <= 1:
        return [fn(x) for x in items]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(fn, items))


# ------------------------------------------------------------------------------------------------
# inspect_media
# ------------------------------------------------------------------------------------------------
class MediaInfo(BaseModel):
    path: str
    name: str
    kind: Literal["audio", "video", "image", "pdf", "text", "other"]
    size_bytes: int
    extension: str
    sniffed_format: str = Field(description="Format guessed from the file's bytes, independent of its extension.")
    extension_mismatch: bool = Field(default=False, description="True when the extension disagrees with the real format.")
    duration_s: float | None = None
    pages: int | None = None
    width: int | None = None
    height: int | None = None
    has_audio_stream: bool | None = None
    text_chars: int | None = None
    script_language: dict | None = Field(default=None, description="For text files: detect_script_language output.")
    suggested_tools: list[str] = Field(default_factory=list)


class InspectMediaResult(ToolResult):
    media: MediaInfo | None = None
    entries: list[MediaInfo] = Field(default_factory=list, description="When path is a directory: one entry per file (max 100).")


def _inspect_one(p: Path) -> MediaInfo:
    ext = p.suffix.lower()
    sniff = audio.sniff_magic(p)
    info = MediaInfo(path=str(p), name=p.name, kind="other", size_bytes=p.stat().st_size, extension=ext, sniffed_format=sniff)
    real_kind: str | None = None
    if sniff == "pdf":
        real_kind = "pdf"
    elif sniff in {"png", "jpeg", "gif", "bmp", "tiff", "webp"}:
        real_kind = "image"
    elif ext in VIDEO_SUFFIXES and sniff in {"mp4/m4a", "mkv/webm", "avi", "unknown"}:
        real_kind = "video"
    elif sniff in {"wav", "mp3", "flac", "ogg", "mp4/m4a", "mkv/webm"} or ext in AUDIO_SUFFIXES:
        real_kind = "audio"
    elif ext in TEXT_SUFFIXES:
        real_kind = "text"
    elif sniff == "unknown":
        try:
            head = p.read_bytes()[:2048]
            real_kind = "text" if b"\x00" not in head and head.decode("utf-8", errors="strict") is not None else None
        except (UnicodeDecodeError, OSError):
            real_kind = None
    expected = {"pdf": {".pdf"}, "image": IMAGE_SUFFIXES, "audio": AUDIO_SUFFIXES | VIDEO_SUFFIXES}.get(real_kind or "", None)
    if expected and ext not in expected:
        info.extension_mismatch = True
    info.kind = real_kind or "other"  # type: ignore[assignment]

    if info.kind in {"audio", "video"} and audio.have_ffmpeg():
        try:
            probe = audio.ffprobe(p)
            streams = probe.get("streams", [])
            info.has_audio_stream = any(s.get("codec_type") == "audio" for s in streams)
            has_video = any(s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic") != 1 for s in streams)
            fmt_dur = (probe.get("format") or {}).get("duration")
            info.duration_s = round(float(fmt_dur), 2) if fmt_dur else None
            if info.kind == "audio" and has_video and ext in VIDEO_SUFFIXES:
                info.kind = "video"
        except ToolFailure:
            info.kind = "other"
    elif info.kind in {"audio", "video"}:
        info.duration_s = None
    if info.kind == "pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(p))
            if reader.is_encrypted:
                info.pages = None
            else:
                info.pages = len(reader.pages)
        except Exception:
            info.pages = None
    if info.kind == "image":
        try:
            from PIL import Image

            with Image.open(p) as im:
                info.width, info.height = im.size
        except Exception:
            info.kind = "other"
    if info.kind == "text":
        try:
            text = _read_text_file(p)
            info.text_chars = len(text)
            info.script_language = textutil.detect_language(text)
        except ToolFailure:
            info.kind = "other"
    info.suggested_tools = {
        "audio": ["transcribe_audio"], "video": ["transcribe_audio (extracts the audio track)"],
        "image": ["ocr_document"], "pdf": ["ocr_document"],
        "text": ["translate_text", "synthesize_speech", "detect_script_language"], "other": [],
    }[info.kind]
    return info


@guarded(InspectMediaResult)
def inspect_media(path: str) -> InspectMediaResult:
    p = Path((path or "").strip().strip('"')).expanduser()
    if not str(path or "").strip():
        raise ToolFailure("INVALID_ARGUMENT", "path is empty.", hint="Pass a file or directory path.")
    if not p.exists():
        raise ToolFailure("FILE_NOT_FOUND", f"No such file or directory: {path}", hint="Check the path.")
    if p.is_dir():
        files = [f for f in sorted(p.rglob("*")) if f.is_file()][:100]
        return InspectMediaResult(entries=[_inspect_one(f) for f in files if f.stat().st_size > 0 or True])
    if p.stat().st_size == 0:
        return InspectMediaResult(media=MediaInfo(path=str(p), name=p.name, kind="other", size_bytes=0, extension=p.suffix.lower(),
                                                  sniffed_format="empty"), warnings=["File is empty (0 bytes)."])
    return InspectMediaResult(media=_inspect_one(p))


# ------------------------------------------------------------------------------------------------
# detect_script_language / list_voices
# ------------------------------------------------------------------------------------------------
class DetectLanguageResult(ToolResult):
    script: str | None = Field(default=None, description="Dominant Unicode script, e.g. Tamil, Devanagari, Latin.")
    language: str | None = Field(default=None, description="Most likely Bodhan language code, or null if no letters were found.")
    candidates: list[str] = Field(default_factory=list, description="All language codes that use this script, best guess first.")
    marker_score: int | None = Field(default=None, description="Shared scripts only: number of marker words found for the best guess (0 = default guess).")
    confidence: Literal["high", "medium", "low", "none"] = "none"
    script_counts: dict[str, int] = Field(default_factory=dict)
    note: str = ""


@guarded(DetectLanguageResult)
def detect_script_language(text_or_path: str) -> DetectLanguageResult:
    text, _ = resolve_text_input(text_or_path)
    d = textutil.detect_language(text[:20000])
    note = "Detected from Unicode script only (no model). "
    if d["confidence"] == "none":
        note += "No letters found."
    elif len(d["candidates"]) > 1:
        note += f"{d['script']} script is shared by {', '.join(d['candidates'])}; the best guess uses common marker words and may be wrong."
    if d["script"] == "Latin":
        note += "Latin script may be English or romanised Indic text."
    return DetectLanguageResult(note=note, **d)


class Voice(BaseModel):
    name: str
    gender: Literal["female", "male"]


class ListVoicesResult(ToolResult):
    language: str = ""
    language_name: str = ""
    voices: list[Voice] = Field(default_factory=list)
    styles: list[str] = Field(default_factory=list, description="Valid `style` values for synthesize_speech.")
    speak_languages: list[str] = Field(default_factory=list, description="All languages indic-speak can synthesise.")


@guarded(ListVoicesResult)
def list_voices(language: str, gender: Literal["female", "male"] | None = None) -> ListVoicesResult:
    lang = normalize_language(language, "indic-speak")
    if gender not in (None, "female", "male"):
        raise ToolFailure("INVALID_ARGUMENT", f"gender must be 'female' or 'male', got {gender!r}.", hint="Omit gender to list both.")
    out = [Voice(name=n, gender=g) for g, names in config.voices()[lang].items() for n in names if gender in (None, g)]  # type: ignore[arg-type]
    return ListVoicesResult(language=lang, language_name=config.language_names().get(lang, lang), voices=out,
                            styles=config.speak_styles(), speak_languages=config.languages_for("indic-speak"))


# ------------------------------------------------------------------------------------------------
# transcribe_audio
# ------------------------------------------------------------------------------------------------
class Segment(BaseModel):
    start_s: float
    end_s: float
    text: str


class TranscribeResult(ToolResult):
    transcript_path: str = Field(default="", description="UTF-8 text file with the full transcript (read it, or pass it to translate_text).")
    srt_path: str = Field(default="", description="Subtitle file in the transcript's own language, timed to the audio.")
    preview: str = ""
    segments: list[Segment] = Field(default_factory=list)
    duration_s: float = 0.0
    windows: int = 0
    language: str | None = Field(default=None, description="Language code: the one you passed, else the script-based guess of the transcript.")
    language_candidates: list[str] = Field(default_factory=list)
    detected_script: str | None = None
    skipped_silent_windows: int = 0


def _rms(wav: Path) -> float:
    import wave

    with wave.open(str(wav), "rb") as w:
        frames = w.readframes(w.getnframes())
    samples = array.array("h")
    samples.frombytes(frames[: len(frames) // 2 * 2])
    if not samples:
        return 0.0
    step = max(1, len(samples) // 20000)
    sub = samples[::step]
    return math.sqrt(sum(s * s for s in sub) / len(sub))


def _transcribe_path(src: Path, language: str | None, tmp_tag: str) -> dict:
    """Shared by transcribe_audio and verify_audio. Decodes, windows at silences, transcribes, stitches."""
    duration = audio.duration_of(src)
    if duration <= 0.05:
        raise ToolFailure("EMPTY_INPUT", f"{src.name} contains no audio.", hint="Check the file with inspect_media (has_audio_stream).")
    if duration > config.MAX_AUDIO_SECONDS:
        raise ToolFailure("INPUT_TOO_LARGE", f"Audio is {duration / 60:.0f} minutes; the limit is {config.MAX_AUDIO_SECONDS // 60} minutes.",
                          hint="Cut the file into shorter parts first.")
    tmp = config.work_dir() / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    wav = audio.decode_to_wav(src, tmp / f"{tmp_tag}.wav")
    windows = [w for w in audio.plan_windows(duration, audio.silence_midpoints(wav)) if w[1] - w[0] >= 0.3]
    parts: list[tuple[float, float, Path]] = []
    for i, (a, b) in enumerate(windows):
        parts.append((a, b, audio.write_window(wav, a, b, tmp / f"{tmp_tag}_w{i}.wav")))
    skipped = 0
    jobs = []
    for a, b, p in parts:
        if _rms(p) < 15:  # essentially digital silence: do not pay to transcribe (and avoid hallucinated text)
            skipped += 1
        else:
            jobs.append((a, b, p))

    def run(job):
        a, b, p = job
        text, info = client.transcribe(p, b - a, language)
        return a, b, text, info

    results = _parallel(run, jobs)
    segments = [Segment(start_s=round(a, 2), end_s=round(b, 2), text=t) for a, b, t, _ in results if t.strip()]
    for _, _, p in parts:
        p.unlink(missing_ok=True)
    wav.unlink(missing_ok=True)
    return {"segments": segments, "duration": duration, "windows": len(windows), "skipped": skipped,
            "cost": sum(i.cost_inr for *_, i in results), "hits": sum(1 for *_, i in results if i.cached)}


def _srt_from_segments(segments: list[Segment]) -> str:
    cues: list[textutil.Cue] = []
    for seg in segments:
        pieces = textutil.pack_sentences(seg.text, 140) or [seg.text]  # subtitle-sized, but cut only at sentence ends where the text has them
        total = sum(len(p) for p in pieces) or 1
        t = seg.start_s
        for piece in pieces:
            dur = (seg.end_s - seg.start_s) * len(piece) / total
            cues.append(textutil.Cue(len(cues) + 1, t, t + dur, textutil.split_cue_text(piece)))
            t += dur
    return textutil.build_srt(cues)


@guarded(TranscribeResult)
def transcribe_audio(audio_path: str, language: str | None = None) -> TranscribeResult:
    src = resolve_input_file(audio_path, what="audio file")
    sniff = audio.sniff_magic(src)
    # The file's bytes decide, not its name: audio hiding behind a .txt name is still audio.
    not_media = sniff in {"pdf", "png", "jpeg", "gif", "bmp", "tiff", "webp"} or (
        sniff == "unknown" and src.suffix.lower() not in AUDIO_SUFFIXES | VIDEO_SUFFIXES)
    if not_media:
        raise ToolFailure("UNSUPPORTED_FILE_TYPE", f"{src.name} is not audio or video.", hint="Use ocr_document for images/PDFs; read text files directly.")
    lang = normalize_language(language, "indic-transcribe", role="language") if language and language.lower() != "auto" else None
    h = sha256_file(src)[:16]
    r = _transcribe_path(src, lang, f"asr_{h}")
    segs: list[Segment] = r["segments"]
    warnings = []
    if not segs:
        warnings.append("No speech was recognised (silence, music or an unsupported language). Nothing was written.")
        return TranscribeResult(duration_s=round(r["duration"], 2), windows=r["windows"], skipped_silent_windows=r["skipped"],
                                cost_inr=r["cost"], cache_hits=r["hits"], warnings=warnings, language=lang)
    full = "\n".join(s.text for s in segs)
    det = textutil.detect_language(full)
    tp = out_file("transcript", f"{h}{lang or 'auto'}", ".txt")
    tp.write_text(full + "\n", encoding="utf-8")
    sp = tp.with_suffix(".srt")
    sp.write_text(_srt_from_segments(segs), encoding="utf-8")
    if r["skipped"]:
        warnings.append(f"{r['skipped']} silent window(s) were skipped without calling the model.")
    if lang is None:
        warnings.append("Language was not given, so the model auto-detected it; `language` is a script-based guess and may be ambiguous "
                        "within shared scripts (e.g. Devanagari). Pass `language` if you know it.")
    return TranscribeResult(transcript_path=str(tp), srt_path=str(sp), preview=preview(full), segments=segs, duration_s=round(r["duration"], 2),
                            windows=r["windows"], language=lang or det["language"], language_candidates=det["candidates"],
                            detected_script=det["script"], skipped_silent_windows=r["skipped"], cost_inr=r["cost"], cache_hits=r["hits"],
                            warnings=warnings)


# ------------------------------------------------------------------------------------------------
# translate_text
# ------------------------------------------------------------------------------------------------
class TranslateResult(ToolResult):
    output_path: str = Field(default="", description="File containing the full translation (same format as the input; .srt stays .srt).")
    preview: str = ""
    source_path: str = Field(default="", description="File holding the source text that was translated (pass to verify_translation).")
    target_language: str = ""
    chunks: int = 0
    model_calls: int = 0
    truncated_chunks: int = 0
    pivot_note: str = ""
    noop: bool = Field(default=False, description="True when the text was already in the target language: returned unchanged, nothing translated or charged.")


_STATS_LOCK = threading.Lock()


def _translate_one(text: str, target: str, script: str, stats: dict) -> str:
    """Translate one chunk; protects LaTeX, retries smaller if output was truncated."""
    protected, spans = textutil.protect_math(text)

    def call(piece: str) -> tuple[str, bool]:
        out_, trunc_, info_ = client.translate(piece, target, script)
        with _STATS_LOCK:
            stats["calls"] += 1
            stats["cost"] += info_.cost_inr
            stats["hits"] += int(info_.cached)
        return out_, trunc_

    out, truncated = call(protected)
    if truncated and len(protected) > 200:
        with _STATS_LOCK:
            stats["truncated"] += 1
        outs = []
        for piece in textutil.pack_sentences(protected, max(len(protected) // 2, 100)):
            o, t2 = call(piece)
            if t2:
                stats["warnings"].append("A piece of the output was still truncated after splitting; the translation may be incomplete.")
            outs.append(o)
        out = " ".join(outs)
    elif truncated:
        stats["warnings"].append("Output hit the token limit on a short chunk; the translation may be incomplete.")
    restored, lost = textutil.restore_math(out, spans)
    if lost:
        stats["warnings"].append(f"{lost} LaTeX span(s) were altered by the translator; compare with the source.")
    return restored


def _table_ok(src: str, out: str) -> bool:
    a = [ln for ln in src.splitlines() if ln.strip()]
    b = [ln for ln in out.splitlines() if ln.strip()]
    return len(a) == len(b) and all(x.count("|") == y.count("|") for x, y in zip(a, b, strict=True))


def _translate_table_cellwise(table: str, target: str, script: str, stats: dict) -> str:
    rows = []
    for ln in table.splitlines():
        if not ln.strip():
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        rows.append(cells)

    def is_sep(cells):
        return all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c) and any(cells)

    jobs = [(ri, ci) for ri, r in enumerate(rows) if not is_sep(r) for ci, c in enumerate(r) if re.search(r"[^\W\d_]", c)]
    out_cells: dict[tuple[int, int], str] = {}

    def run(job):
        ri, ci = job
        return job, _translate_one(rows[ri][ci], target, script, stats)

    for job, val in _parallel(run, jobs):
        out_cells[job] = val.replace("|", "/").replace("\n", " ").strip()
    lines = []
    for ri, r in enumerate(rows):
        lines.append("| " + " | ".join(out_cells.get((ri, ci), c) for ci, c in enumerate(r)) + " |")
    return "\n".join(lines)


def _translate_chunk(chunk: textutil.Chunk, target: str, script: str, stats: dict) -> str:
    if textutil._is_table(chunk.text):
        out = _translate_one(chunk.text, target, script, stats)
        if _table_ok(chunk.text, out):
            return out
        stats["warnings"].append("A table came back structurally altered; it was re-translated cell by cell to keep its shape.")
        return _translate_table_cellwise(chunk.text, target, script, stats)
    return _translate_one(chunk.text, target, script, stats)


def _translate_srt(text: str, target: str, script: str, stats: dict) -> str:
    cues = textutil.parse_srt(text)
    if not cues:
        raise ToolFailure("INVALID_ARGUMENT", "The .srt file has no cues.", hint="Check the file is valid SRT.")

    def run(c: textutil.Cue):
        t = _translate_one(c.text.replace("\n", " "), target, script, stats)
        return textutil.Cue(c.index, c.start, c.end, t if target in ("ur", "ks", "sd-Arab") else textutil.split_cue_text(t))

    return textutil.build_srt(_parallel(run, cues))


@guarded(TranslateResult)
def translate_text(text_or_path: str, target_lang: str, source_lang: str = "auto", script: Literal["native", "roman", "codemix"] = "native",
                   pivot_via_english: bool = False) -> TranslateResult:
    target = normalize_language(target_lang, "indic-translate", role="target_lang")
    source = normalize_language(source_lang, "indic-translate", allow_auto=True, role="source_lang")
    if script not in ("native", "roman", "codemix"):
        raise ToolFailure("INVALID_ARGUMENT", f"script must be native, roman or codemix, got {script!r}.", hint="Use 'native' for normal text.")
    text, src_file = resolve_text_input(text_or_path)
    is_srt = src_file is not None and src_file.suffix.lower() == ".srt"
    warnings: list[str] = []
    if source == target or (source == "auto" and textutil.already_in_language(text, target)):
        out_text = text
        warnings.append("The text is already in the target language: nothing to translate, returned unchanged at no cost." if source == "auto"
                        else "source_lang equals target_lang: nothing to translate, text returned unchanged at no cost.")
        op = out_file("translated", hashlib.sha256((text + target).encode()).hexdigest(), ".srt" if is_srt else ".md")
        op.write_text(out_text, encoding="utf-8")
        return TranslateResult(output_path=str(op), preview=preview(out_text), source_path=str(src_file), target_language=target, warnings=warnings, noop=True)
    pivot_note = ""
    indic_pair = target != "en" and (source not in ("en", "auto") or textutil.detect_language(text[:2000])["script"] not in (None, "Latin"))
    if indic_pair:
        pivot_note = ("Indian-language to Indian-language: translated in one hop. Quality is usually best when English is the pivot; "
                      "call again with pivot_via_english=true if the direct result is poor.")
    stats = {"calls": 0, "cost": 0.0, "hits": 0, "truncated": 0, "warnings": warnings}
    working, hops = text, [target]
    if pivot_via_english and indic_pair and source != "en":
        hops = ["en", target]
    chunks_n = 0
    for hop in hops:
        if is_srt and hop == hops[0]:
            working = _translate_srt(working, hop, script if hop != "en" else "native", stats)
            chunks_n += len(textutil.parse_srt(working))
        else:
            chunks = textutil.chunk_markdown(working, config.TRANSLATE_CHUNK_MAX_CHARS)
            chunks_n += len(chunks)
            hop_script = script if hop != "en" else "native"
            outs = _parallel(lambda ch, h=hop, s=hop_script: _translate_chunk(ch, h, s, stats), chunks)
            working = textutil.join_chunks(chunks, outs)
    key = hashlib.sha256((text + target + script + str(pivot_via_english)).encode()).hexdigest()
    op = out_file("translated", key, ".srt" if is_srt else ".md")
    op.write_text(working, encoding="utf-8")
    if not working.strip():
        raise ToolFailure("UPSTREAM_ERROR", "The translation came back empty.", retryable=True, hint="Retry; check the input text.")
    return TranslateResult(output_path=str(op), preview=preview(working), source_path=str(src_file), target_language=target, chunks=chunks_n,
                           model_calls=stats["calls"], truncated_chunks=stats["truncated"], pivot_note=pivot_note, cost_inr=stats["cost"],
                           cache_hits=stats["hits"], warnings=list(dict.fromkeys(warnings)))


# ------------------------------------------------------------------------------------------------
# synthesize_speech
# ------------------------------------------------------------------------------------------------
class SynthesizeResult(ToolResult):
    audio_path: str = ""
    duration_s: float = 0.0
    language: str = ""
    voice: str = ""
    characters: int = 0
    requests: int = Field(default=0, description="How many short model requests the text was split into.")
    input_text_path: str = Field(default="", description="The exact text that was spoken (pass to verify_audio as expected_text).")
    label: str = "AI-generated speech (Bodhan Indic-Speak); embedded in file metadata."


def _check_text_matches_language(text: str, language: str) -> None:
    d = textutil.detect_language(text)
    if d["script"] is None:
        raise ToolFailure("EMPTY_INPUT", "The text has no speakable letters.", hint="Provide text with words.")
    expected = textutil.expected_script_for(language)
    if expected and d["script"] != expected and d["confidence"] != "low":
        raise ToolFailure(
            "TEXT_LANGUAGE_MISMATCH",
            f"language={language} expects {expected} script but the text is mostly {d['script']}.",
            hint="Translate the text to the target language first (translate_text with script='native'), then pass its output_path here.")


@guarded(SynthesizeResult)
def synthesize_speech(text_or_path: str, language: str, voice: str | None = None, gender: Literal["female", "male"] | None = None,
                      style: str | None = None, output_format: Literal["mp3", "wav"] = "mp3", force_refresh: bool = False) -> SynthesizeResult:
    lang = normalize_language(language, "indic-speak")
    raw, src_file = resolve_text_input(text_or_path)
    text = textutil.speakable(raw) if (src_file and src_file.suffix.lower() in {".md", ".markdown"}) or "|" in raw or "#" in raw else raw.strip()
    if len(text) > config.MAX_TTS_CHARS:
        raise ToolFailure("INPUT_TOO_LARGE", f"{len(text):,} characters to speak would cost about Rs {pricing.speak_cost(len(text)):.2f}; "
                          f"the per-call limit is {config.MAX_TTS_CHARS:,}.", hint="Summarise or split the text into parts.")
    _check_text_matches_language(text, lang)
    table = config.voices()[lang]
    if gender not in (None, "female", "male"):
        raise ToolFailure("INVALID_ARGUMENT", f"gender must be 'female' or 'male', got {gender!r}.", hint="Omit gender to use the default voice.")
    allv = [n for names in table.values() for n in names]
    if voice:
        match = next((n for n in allv if n.lower() == voice.strip().lower()), None)
        if not match:
            raise ToolFailure("UNSUPPORTED_VOICE", f"Voice {voice!r} does not exist for {lang}.", hint=f"Voices for {lang}: {', '.join(allv)} (see list_voices).")
        voice = match
    else:
        g = gender or "female"
        voice = (table.get(g) or allv)[0]
    if style and style not in config.speak_styles():
        raise ToolFailure("INVALID_ARGUMENT", f"Unknown style {style!r}.", hint=f"Valid styles: {', '.join(config.speak_styles())}.")
    if output_format not in ("mp3", "wav"):
        raise ToolFailure("INVALID_ARGUMENT", f"output_format must be mp3 or wav, got {output_format!r}.", hint="Use 'mp3' (default) or 'wav'.")
    pieces = textutil.pack_sentences(text, config.TTS_REQUEST_MAX_CHARS)

    def speak_piece(piece: str, depth: int = 0) -> list[tuple[Path, CallInfo]]:
        """One short request; if it stalls (runaway generation) split it in half and retry the halves."""
        try:
            return [client.speak(piece, lang, voice, style, force_refresh)]
        except ToolFailure as exc:
            if exc.info.error_code != "UPSTREAM_TIMEOUT" or depth >= 2 or len(piece) < 40:
                raise
            cut = next((i for i in range(len(piece) // 2, len(piece)) if piece[i] in " ,;।.") , len(piece) // 2)
            halves = [h.strip() for h in (piece[:cut + 1], piece[cut + 1:]) if h.strip()]
            return [r for h in halves for r in speak_piece(h, depth + 1)]

    results = [r for group in _parallel(speak_piece, pieces) for r in group]
    tmp = config.work_dir() / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(f"{text}|{lang}|{voice}|{style}|{time.time() if force_refresh else ''}".encode()).hexdigest()
    joined = audio.concat_wavs([p for p, _ in results], tmp / f"tts_{key[:10]}.wav")
    dst = out_file(f"speech_{lang}", key, f".{output_format}")
    final, note = audio.finalize_audio(joined, dst, title=f"AI-generated speech ({lang})")
    joined.unlink(missing_ok=True)
    spoken = save_text(text, "spoken", "txt")
    warnings = [note] if note else []
    return SynthesizeResult(audio_path=str(final), duration_s=round(audio.duration_of(final), 2), language=lang, voice=voice, characters=len(text),
                            requests=len(results), input_text_path=str(spoken), cost_inr=sum(i.cost_inr for _, i in results),
                            cache_hits=sum(1 for _, i in results if i.cached), warnings=warnings)


# ------------------------------------------------------------------------------------------------
# ocr_document
# ------------------------------------------------------------------------------------------------
class PageInfo(BaseModel):
    page: int
    chars: int
    mean_confidence: float | None = None
    flags: list[str] = Field(default_factory=list, description="empty, low_confidence, truncated, possible_repetition")


class OcrResult(ToolResult):
    markdown_path: str = Field(default="", description="Markdown file with all pages in order, separated by '---' lines. Tables are Markdown tables.")
    preview: str = ""
    pages: list[PageInfo] = Field(default_factory=list)
    total_pages_in_file: int = 0
    pages_processed: int = 0
    language: str | None = None
    language_candidates: list[str] = Field(default_factory=list)


def _parse_page_range(spec: str | None, total: int) -> list[int]:
    if not spec:
        return list(range(1, total + 1))
    pages: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        m = re.fullmatch(r"(\d+)(?:\s*-\s*(\d+))?", part)
        if not m:
            raise ToolFailure("INVALID_ARGUMENT", f"Bad page_range {spec!r}.", hint="Use e.g. '1-3,5'.")
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        if a < 1 or b < a or b > total:
            raise ToolFailure("INVALID_ARGUMENT", f"page_range {part!r} is outside 1..{total}.", hint=f"The file has {total} pages.")
        pages += list(range(a, b + 1))
    return sorted(set(pages))


def _prepare_image(img, max_side: int = 2200) -> tuple[bytes, str]:
    import io

    from PIL import Image, ImageOps

    img = ImageOps.exif_transpose(img).convert("RGB")
    if max(img.size) > max_side:
        scale = max_side / max(img.size)
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue(), "image/jpeg"


@guarded(OcrResult)
def ocr_document(path: str, table_format: Literal["markdown", "html"] = "markdown", page_range: str | None = None) -> OcrResult:
    from PIL import Image

    p = resolve_input_file(path, what="document")
    sniff = audio.sniff_magic(p)
    if table_format not in ("markdown", "html"):
        raise ToolFailure("INVALID_ARGUMENT", f"table_format must be markdown or html, got {table_format!r}.", hint="Use 'markdown'.")
    images: list[tuple[int, Callable[[], tuple[bytes, str]]]] = []
    total = 1
    if sniff == "pdf" or p.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        from pypdf.errors import PyPdfError

        try:
            reader = PdfReader(str(p))
            if reader.is_encrypted:
                raise ToolFailure("UNREADABLE_MEDIA", "The PDF is password-protected.", hint="Provide an unlocked copy.")
            total = len(reader.pages)
        except ToolFailure:
            raise
        except (PyPdfError, ValueError, OSError) as exc:
            raise ToolFailure("UNREADABLE_MEDIA", f"Could not read the PDF: {str(exc)[:150]}", hint="The file may be corrupt.") from exc
        pages = _parse_page_range(page_range, total)
        if len(pages) > config.MAX_OCR_PAGES:
            raise ToolFailure("INPUT_TOO_LARGE", f"{len(pages)} pages requested; the limit per call is {config.MAX_OCR_PAGES} "
                              f"(about Rs {pricing.ocr_cost(len(pages)):.2f}).", hint="Pass page_range (e.g. '1-20') and call again for the rest.")
        from pdf2image import convert_from_path
        from pdf2image.exceptions import PDFInfoNotInstalledError, PDFPageCountError, PDFSyntaxError

        def render(n: int):
            def _r():
                try:
                    im = convert_from_path(str(p), dpi=170, first_page=n, last_page=n)[0]
                except PDFInfoNotInstalledError as exc:
                    raise ToolFailure("MISSING_DEPENDENCY", "poppler (pdftoppm) is not installed or not on PATH.",
                                      hint="Install poppler-utils (apt) / poppler (brew, conda) and retry.") from exc
                except (PDFPageCountError, PDFSyntaxError) as exc:
                    raise ToolFailure("UNREADABLE_MEDIA", f"Could not render page {n}: {str(exc)[:120]}", hint="The PDF may be corrupt.") from exc
                return _prepare_image(im)
            return _r

        images = [(n, render(n)) for n in pages]
    elif sniff in {"png", "jpeg", "webp", "bmp", "tiff", "gif"}:
        try:
            with Image.open(p) as im:
                im.load()
                data = _prepare_image(im)
        except Exception as exc:
            raise ToolFailure("UNREADABLE_MEDIA", f"Could not open the image: {str(exc)[:150]}", hint="The file may be corrupt.") from exc
        images = [(1, lambda d=data: d)]
    else:
        raise ToolFailure("UNSUPPORTED_FILE_TYPE", f"{p.name} is not an image or PDF (detected: {sniff}).",
                          hint="ocr_document accepts PNG/JPEG/WEBP/BMP/TIFF images and PDFs. Use transcribe_audio for audio.")

    def run(item):
        n, make = item
        data, mime = make()
        res, info = client.ocr_page(data, mime, table_format)
        return n, res, info

    results = _parallel(run, images)
    infos: list[PageInfo] = []
    md_parts: list[str] = []
    for n, res, _ in results:
        md = res["markdown"]
        flags = []
        if not md.strip():
            flags.append("empty")
        if res.get("mean_conf") is not None and res["mean_conf"] < 0.6:
            flags.append("low_confidence")
        if res.get("truncated"):
            flags.append("truncated")
        if textutil.repeated_ngrams(md, 3, 4):
            flags.append("possible_repetition")
        infos.append(PageInfo(page=n, chars=len(md), mean_confidence=round(res["mean_conf"], 3) if res.get("mean_conf") is not None else None, flags=flags))
        md_parts.append(md)
    full = "\n\n---\n\n".join(m for m in md_parts if m.strip())
    warnings = [f"Page {i.page}: {', '.join(i.flags)}" for i in infos if i.flags]
    cost, hits = sum(i.cost_inr for *_, i in results), sum(1 for *_, i in results if i.cached)
    if not full.strip():
        return OcrResult(pages=infos, total_pages_in_file=total, pages_processed=len(infos), cost_inr=cost, cache_hits=hits,
                         warnings=warnings + ["No text was found in the document."])
    det = textutil.detect_language(full[:20000])
    mp = out_file("ocr", sha256_file(p) + table_format + (page_range or ""), ".md")
    mp.write_text(full + "\n", encoding="utf-8")
    return OcrResult(markdown_path=str(mp), preview=preview(full), pages=infos, total_pages_in_file=total, pages_processed=len(infos),
                     language=det["language"], language_candidates=det["candidates"], cost_inr=cost, cache_hits=hits, warnings=warnings)


# ------------------------------------------------------------------------------------------------
# verification tools
# ------------------------------------------------------------------------------------------------
class Check(BaseModel):
    name: str
    ok: bool
    detail: str


class VerifyAudioResult(ToolResult):
    passed: bool = False
    cer: float | None = Field(default=None, description="Normalised character error rate of the back-transcription vs expected text (0 = identical).")
    threshold: float = 0.0
    flags: list[str] = Field(default_factory=list, description="repetition, omission, extra_content, empty_transcript")
    transcript_preview: str = ""
    expected_chars: int = 0
    heard_chars: int = 0


@guarded(VerifyAudioResult)
def verify_audio(audio_path: str, expected_text: str, language: str, max_cer: float = 0.35) -> VerifyAudioResult:
    src = resolve_input_file(audio_path, what="audio file")
    lang = normalize_language(language, "indic-transcribe")
    expected_raw, _ = resolve_text_input(expected_text, what="expected_text")
    expected = textutil.speakable(expected_raw)
    if not 0.01 <= max_cer <= 1.0:
        raise ToolFailure("INVALID_ARGUMENT", "max_cer must be between 0.01 and 1.0.", hint="Typical value: 0.35.")
    r = _transcribe_path(src, lang, f"vfy_{sha256_file(src)[:16]}")
    heard = " ".join(s.text for s in r["segments"])
    flags: list[str] = []
    if not heard.strip():
        flags.append("empty_transcript")
    e_n, h_n = len(textutil.normalize_for_cer(expected)), len(textutil.normalize_for_cer(heard))
    if h_n and e_n:
        if h_n / e_n < 0.75:
            flags.append("omission")
        elif h_n / e_n > 1.35:
            flags.append("extra_content")
    if textutil.repeated_ngrams(heard, 3, 3) and not textutil.repeated_ngrams(expected, 3, 3):
        flags.append("repetition")
    has_digits = bool(textutil.numbers_in(expected))
    if has_digits and "extra_content" in flags:
        flags.remove("extra_content")  # spoken number words are longer than their digits
    c = textutil.cer(expected, heard, ignore_digits=has_digits)
    notes = ["The expected text contains numerals; the ASR spells them as words, so digits and insertions were ignored in the CER."] if has_digits else []
    return VerifyAudioResult(passed=(c <= max_cer and not flags), cer=round(c, 4), threshold=max_cer, flags=flags, transcript_preview=preview(heard, 400),
                             expected_chars=e_n, heard_chars=h_n, cost_inr=r["cost"], cache_hits=r["hits"], warnings=notes)


class VerifyTranslationResult(ToolResult):
    passed: bool = False
    checks: list[Check] = Field(default_factory=list)
    length_ratio: float | None = None


_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+\S", re.M)
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\S", re.M)


@guarded(VerifyTranslationResult)
def verify_translation(source_path: str, translated_path: str, target_language: str | None = None) -> VerifyTranslationResult:
    sp = resolve_input_file(source_path, kinds=TEXT_SUFFIXES, what="source")
    tp = resolve_input_file(translated_path, kinds=TEXT_SUFFIXES, what="translation")
    src, tr = _read_text_file(sp), _read_text_file(tp)
    lang = normalize_language(target_language, "indic-translate", role="target_language") if target_language else None
    checks: list[Check] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append(Check(name=name, ok=ok, detail=detail))

    add("non_empty", bool(tr.strip()), f"{len(tr.strip())} characters")
    identical_ok = src.strip() == tr.strip() and bool(lang) and textutil.already_in_language(tr, lang)  # e.g. Bengali text "translated" to Bengali
    add("not_identical", src.strip() != tr.strip() or not src.strip() or identical_ok,
        "translation differs from source" if src.strip() != tr.strip()
        else ("identical to the source, which is already in the target language (nothing to translate)" if identical_ok
              else "translation is identical to the source (untranslated?)"))
    ratio = (len(tr.strip()) / len(src.strip())) if src.strip() else None
    if ratio is not None:
        add("length_ratio", 0.3 <= ratio <= 4.0, f"translation/source length = {ratio:.2f} (expected 0.3-4.0)")
    if tp.suffix.lower() == ".srt" or sp.suffix.lower() == ".srt":
        a, b = textutil.parse_srt(src), textutil.parse_srt(tr)
        add("srt_cue_count", len(a) == len(b), f"source {len(a)} cues, translation {len(b)} cues")
        add("srt_timestamps", [(c.start, c.end) for c in a] == [(c.start, c.end) for c in b], "timestamps match" if len(a) == len(b) else "cue counts differ")
    for name, rx in (("headings", _HEADING), ("list_items", _LIST_ITEM)):
        n_s, n_t = len(rx.findall(src)), len(rx.findall(tr))
        if n_s:
            add(name, n_s == n_t, f"source {n_s}, translation {n_t}")
    rows_s = [ln for ln in src.splitlines() if textutil._TABLE_LINE.match(ln)]
    rows_t = [ln for ln in tr.splitlines() if textutil._TABLE_LINE.match(ln)]
    if rows_s:
        cols_ok = sorted(ln.count("|") for ln in rows_s) == sorted(ln.count("|") for ln in rows_t)
        add("table_rows", len(rows_s) == len(rows_t) and cols_ok, f"source {len(rows_s)} rows, translation {len(rows_t)} rows; column structure "
            + ("matches" if cols_ok else "differs"))
    m_s, m_t = len(textutil._MATH.findall(src)), len(textutil._MATH.findall(tr))
    if m_s:
        add("latex", m_s == m_t, f"source {m_s} formulas, translation {m_t}")
    nums_s, nums_t = textutil.numbers_in(src), textutil.numbers_in(tr)
    if nums_s:
        remaining = list(nums_t)
        found = 0
        for n in nums_s:
            if n in remaining:
                remaining.remove(n)
                found += 1
        cov = found / len(nums_s)
        add("numbers_preserved", cov >= 0.85, f"{found}/{len(nums_s)} numbers from the source appear in the translation ({cov:.0%})")
    if lang:
        counts = textutil.script_counts(tr)
        total = sum(counts.values()) or 1
        want = textutil.expected_script_for(lang)
        share = counts.get(want or "", 0) / total
        need = 0.5 if lang != "en" else 0.8
        add("target_script", share >= need, f"{share:.0%} of letters are in the expected {want} script for {lang} (need >= {need:.0%})")
    passed = all(c.ok for c in checks)
    return VerifyTranslationResult(passed=passed, checks=checks, length_ratio=round(ratio, 2) if ratio is not None else None,
                                   warnings=[] if passed else ["One or more structural checks failed; see checks."])
