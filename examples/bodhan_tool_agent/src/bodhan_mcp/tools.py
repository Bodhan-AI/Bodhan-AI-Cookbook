"""Tool implementations. ``server.py`` registers these on the MCP server; tests call them directly.

Each tool owns its model's limits so callers never see them:
  transcribe_audio  -> silence-aware windows of <= 28 s (model limit 30 s), language detection probe
  translate_text    -> structure-preserving units, <= 1,800-character chunks, truncation re-split
  synthesize_speech -> sentence chunks of <= 220 characters, back-transcription check per chunk,
                       failed chunks re-generated in smaller pieces, WAVs joined, MP3 encoded
  ocr_document      -> PDFs rasterised page by page (the model takes one PNG/JPEG), blank pages
                       skipped, 502 retried with a larger max_tokens
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import costs, formats, languages, media, textproc
from .bodhan_api import BodhanAPI, wav_seconds
from .config import Settings
from .errors import (
    BUDGET_EXCEEDED,
    FILE_NOT_FOUND,
    INPUT_TOO_LARGE,
    INVALID_INPUT,
    LANGUAGE_MISMATCH,
    MEDIA_DECODE_FAILED,
    PATH_NOT_ALLOWED,
    UNSUPPORTED_FILE_TYPE,
    UNSUPPORTED_VOICE,
    UPSTREAM_ERROR,
    ToolFailure,
)
from .schemas import (
    DetectLanguageResult,
    FileInfo,
    InspectResult,
    LanguageInfo,
    OcrPage,
    OcrResult,
    ReadTextResult,
    SpeechCheck,
    SpeechResult,
    SubtitleResult,
    TranscribeResult,
    TranscriptSegment,
    TranslateResult,
    TranslationCheck,
    VerifySpeechResult,
    VoicesResult,
)

INLINE_TEXT_LIMIT = 6000
PREVIEW_CHARS = 1200
TRANSLATE_CHUNK_CHARS = 1800
TRANSLATE_BATCH_CHARS = 1200
TRANSLATE_BATCH_LINES = 25
TTS_CHUNK_CHARS = 220
TTS_MAX_CHARS = 20_000
OCR_MAX_PAGES = 100
PROBE_SECONDS = 8.0
SPEECH_PASS_SIMILARITY = 0.6


@dataclass
class Runtime:
    settings: Settings
    api: BodhanAPI


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _within(path: Path, roots: list[Path]) -> bool:
    return any(path == r or r in path.parents for r in roots)


def resolve_input(rt: Runtime, path: str, *, what: str = "path") -> Path:
    if not path or not str(path).strip():
        raise ToolFailure(INVALID_INPUT, f"'{what}' is empty.", hint="Pass the path of an existing file.")
    p = Path(str(path).strip().strip('"')).expanduser()
    p = p.resolve() if p.is_absolute() else (Path.cwd() / p).resolve()
    if rt.settings.allowed_roots and not _within(p, rt.settings.allowed_roots + [rt.settings.output_dir]):
        raise ToolFailure(PATH_NOT_ALLOWED, f"{p} is outside the folders this server may read.",
                          details={"allowed_roots": [str(r) for r in rt.settings.allowed_roots]})
    if not p.exists():
        raise ToolFailure(FILE_NOT_FOUND, f"File not found: {p}", hint="Check the path; relative paths resolve against the server's working directory.",
                          details={"path": str(p), "cwd": str(Path.cwd())})
    return p


def resolve_output(rt: Runtime, output_path: str | None, default_name: str, suffix: str | None = None) -> Path:
    base = rt.settings.output_dir
    if output_path:
        p = Path(output_path).expanduser()
        p = p if p.is_absolute() else base / p
        p = p.resolve()
        allowed = [base, *rt.settings.allowed_roots] if rt.settings.allowed_roots else [base, Path.cwd().resolve()]
        if not _within(p, allowed):
            raise ToolFailure(PATH_NOT_ALLOWED, f"Cannot write to {p}; outputs must go under {base}.",
                              hint="Omit output_path to use the default location, or pass a relative path.")
        if p.exists() and p.is_dir():
            p = p / default_name
    else:
        p = base / default_name
    if suffix and p.suffix.lower() != suffix:
        p = p.with_suffix(suffix)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _stem_for_text(text: str) -> str:
    return "text-" + hashlib.sha256(text.encode()).hexdigest()[:8]


def _lang_info(guess: textproc.LanguageGuess) -> LanguageInfo:
    code = guess.language
    support = [cap for cap, langs in languages.CAPABILITY_LANGUAGES.items() if code in langs]
    if code and "mni-Beng" == code:
        support = ["translate"]
    return LanguageInfo(
        language=code, language_name=languages.LANGUAGE_NAMES.get(code or "") or languages.KNOWN_UNSUPPORTED.get(code or ""),
        confidence=round(guess.confidence, 2), script=guess.script, candidates=guess.candidates, method=guess.method,
        supported_by=support,
    )


def _check_budget(estimate: float, max_cost_inr: float | None, what: str) -> None:
    if max_cost_inr is not None and estimate > max_cost_inr + 1e-9:
        raise ToolFailure(
            BUDGET_EXCEEDED,
            f"{what} would cost about Rs {estimate:.3f}, above the max_cost_inr of Rs {max_cost_inr:.3f}.",
            hint="Reduce the input (fewer pages, shorter text, a shorter clip) or raise the budget. Nothing was spent.",
            details={"estimated_cost_inr": round(estimate, 4), "max_cost_inr": max_cost_inr},
        )


def _text_fields(text: str) -> tuple[str | None, str]:
    if len(text) <= INLINE_TEXT_LIMIT:
        return text, text[:PREVIEW_CHARS]
    return None, text[:PREVIEW_CHARS] + f"\n... [{len(text) - PREVIEW_CHARS} more characters; use read_text]"


def _usage(meter: costs.CallMeter) -> dict:
    return meter.summary()


def _pages_arg(spec: str | None, total: int) -> list[int]:
    if not spec or spec.strip().lower() in ("all", "*"):
        return list(range(1, total + 1))
    pages: list[int] = []
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            raise ToolFailure(INVALID_INPUT, f"Bad page range '{part}'.", hint="Use forms like '1-3,5' or 'all'. Pages are 1-based.")
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        if a < 1 or b < a:
            raise ToolFailure(INVALID_INPUT, f"Bad page range '{part}'.", hint="Pages are 1-based and ranges ascend.")
        pages.extend(range(a, b + 1))
    bad = [p for p in pages if p > total]
    if bad:
        raise ToolFailure(INVALID_INPUT, f"Page {bad[0]} does not exist; the document has {total} page(s).")
    return sorted(set(pages))


# ---------------------------------------------------------------------------
# inspect_files / read_text / list_voices (free)
# ---------------------------------------------------------------------------

def _inspect_one(rt: Runtime, p: Path) -> FileInfo:
    info = FileInfo(path=str(p), exists=True, size_bytes=p.stat().st_size)
    kind = media.file_kind(p)
    info.kind = kind
    try:
        if kind in ("audio", "video"):
            pr = media.probe(p)
            info.duration_s = pr.get("duration_s")
            info.has_audio = pr.get("has_audio")
            info.has_video = pr.get("has_video", kind == "video")
            info.sample_rate = pr.get("sample_rate")
            info.channels = pr.get("channels")
            info.width, info.height = pr.get("width"), pr.get("height")
            if info.duration_s:
                info.estimated_cost_inr["transcribe_audio"] = round(costs.transcribe_cost(info.duration_s), 4)
            if not pr:
                info.notes.append("Could not probe this file (is ffmpeg installed?).")
            if kind == "video" and pr and not pr.get("has_audio"):
                info.notes.append("This video has no audio track, so there is nothing to transcribe.")
            info.suggested_tool = "transcribe_audio"
        elif kind == "pdf":
            info.page_count = media.pdf_page_count(p)
            info.estimated_cost_inr["ocr_document"] = round(costs.ocr_cost(info.page_count), 2)
            info.suggested_tool = "ocr_document"
        elif kind == "image":
            from PIL import Image

            with Image.open(p) as im:
                info.width, info.height = im.size
            info.page_count = media.image_frames(p)
            info.estimated_cost_inr["ocr_document"] = round(costs.ocr_cost(info.page_count), 2)
            info.suggested_tool = "ocr_document"
        elif kind == "text":
            text, fmt = formats.text_from_file(p)
            info.text_chars = len(text)
            info.text_preview = text[:300]
            info.language = _lang_info(textproc.detect_language(text[:20000]))
            info.suggested_tool = "read_text"
            if fmt == "segments":
                info.notes.append("Segment JSON (from transcribe_audio): usable by translate_text and make_subtitles.")
        else:
            info.notes.append("Unrecognised file type; no tool reads it.")
    except ToolFailure as exc:
        info.notes.append(f"{exc.code}: {exc.message}")
    return info


def inspect_files(rt: Runtime, paths: list[str]) -> InspectResult:
    if not paths:
        raise ToolFailure(INVALID_INPUT, "'paths' is empty.", hint="Pass one or more file or folder paths.")
    files: list[FileInfo] = []
    missing: list[str] = []
    for raw in paths:
        try:
            p = resolve_input(rt, raw)
        except ToolFailure as exc:
            if exc.code == FILE_NOT_FOUND:
                missing.append(str(raw))
                files.append(FileInfo(path=str(raw), exists=False, notes=["file not found"]))
                continue
            raise
        if p.is_dir():
            children = sorted(c for c in p.rglob("*") if c.is_file() and not any(part.startswith(".") for part in c.relative_to(p).parts))
            if not children:
                files.append(FileInfo(path=str(p), exists=True, kind="directory", notes=["folder is empty"]))
            for c in children[:200]:
                files.append(_inspect_one(rt, c))
            if len(children) > 200:
                files.append(FileInfo(path=str(p), exists=True, kind="directory", notes=[f"{len(children) - 200} more files not listed"]))
        else:
            files.append(_inspect_one(rt, p))
    return InspectResult(files=files, missing=missing)


def read_text(rt: Runtime, path: str, offset: int = 0, max_chars: int = 20000) -> ReadTextResult:
    p = resolve_input(rt, path)
    kind = media.file_kind(p)
    if kind != "text":
        tool = {"audio": "transcribe_audio", "video": "transcribe_audio", "image": "ocr_document", "pdf": "ocr_document"}.get(kind)
        raise ToolFailure(UNSUPPORTED_FILE_TYPE, f"{p.name} is a {kind} file, not text.",
                          hint=f"Use {tool} to get its text." if tool else "Only text files can be read.")
    raw = p.read_text(encoding="utf-8-sig", errors="replace")
    fmt = formats.text_from_file(p)[1]
    offset = max(0, offset)
    max_chars = max(1, min(max_chars, 50000))
    piece = raw[offset: offset + max_chars]
    return ReadTextResult(path=str(p), format=fmt, text=piece, offset=offset, returned_chars=len(piece),
                          total_chars=len(raw), has_more=offset + len(piece) < len(raw))


def list_voices(rt: Runtime, language: str | None = None) -> VoicesResult:
    lib = languages.voice_library()
    if not language:
        return VoicesResult(styles=list(languages.STYLES), library=lib, note=lib["note"])
    code = languages.resolve(language, "speak")
    groups = languages.VOICES.get(code, {})
    return VoicesResult(
        language=code,
        female=groups.get("female", [languages.ENGLISH_DEFAULT_VOICES["female"]] if code == "en" else []),
        male=groups.get("male", [languages.ENGLISH_DEFAULT_VOICES["male"]] if code == "en" else []),
        default_female=languages.pick_voice(code, "female"),
        default_male=languages.pick_voice(code, "male"),
        styles=list(languages.STYLES),
        note=lib["note"],
    )


# ---------------------------------------------------------------------------
# detect_language
# ---------------------------------------------------------------------------

async def _audio_probe(rt: Runtime, x: np.ndarray, sr: int, meter: costs.CallMeter,
                       max_cost_inr: float | None = None) -> tuple[str, float, textproc.LanguageGuess]:
    segs = media.plan_segments(x, sr, max_seconds=PROBE_SECONDS, min_seconds=3.0)
    if not segs:
        return "", 0.0, textproc.LanguageGuess(None, 0.0, None, [])
    seg = segs[0]
    wav = media.slice_wav(x, sr, seg)
    _check_budget(costs.transcribe_cost(wav_seconds(wav)), max_cost_inr, "Audio language detection")
    text = await rt.api.transcribe(wav, None, meter)
    return text, seg.duration, textproc.detect_language(text)


async def detect_language(rt: Runtime, text: str | None = None, path: str | None = None,
                          max_cost_inr: float | None = None) -> DetectLanguageResult:
    if bool(text) == bool(path):
        raise ToolFailure(INVALID_INPUT, "Pass exactly one of 'text' or 'path'.")
    meter = costs.CallMeter()
    if text:
        return DetectLanguageResult(detected=_lang_info(textproc.detect_language(text)), source="text", **_usage(meter))
    p = resolve_input(rt, path)  # type: ignore[arg-type]
    kind = media.file_kind(p)
    if kind == "text":
        body, _ = formats.text_from_file(p)
        return DetectLanguageResult(detected=_lang_info(textproc.detect_language(body[:20000])), source="text_file", **_usage(meter))
    if kind in ("audio", "video"):
        x = await asyncio.to_thread(media.load_audio, p)
        heard, secs, guess = await _audio_probe(rt, x, media.ASR_SAMPLE_RATE, meter, max_cost_inr)
        warnings = [] if heard else ["No speech found in the first part of the audio."]
        return DetectLanguageResult(detected=_lang_info(guess), source="audio_probe", probe_transcript=heard,
                                    probe_seconds=round(secs, 2), warnings=warnings, **_usage(meter))
    raise ToolFailure(UNSUPPORTED_FILE_TYPE, f"Cannot detect the language of a {kind} file directly.",
                      hint="Run ocr_document; it reports the detected language of each page.")


# ---------------------------------------------------------------------------
# transcribe_audio
# ---------------------------------------------------------------------------

def _asr_code(code: str | None) -> str | None:
    if code is None:
        return None
    code = {"sd-Arab": "sd", "mni-Beng": "mni", "ks-Deva": "ks"}.get(code, code)
    return code if code in languages.TRANSCRIBE_LANGUAGES else None


async def transcribe_audio(rt: Runtime, path: str, language: str = "auto", segment_max_seconds: float = 28.0,
                           output_path: str | None = None, max_cost_inr: float | None = None) -> TranscribeResult:
    p = resolve_input(rt, path)
    kind = media.file_kind(p)
    if kind not in ("audio", "video"):
        hint = {"image": "Use ocr_document for images.", "pdf": "Use ocr_document for PDFs.", "text": "Use read_text for text files."}.get(kind)
        raise ToolFailure(UNSUPPORTED_FILE_TYPE, f"{p.name} is not an audio or video file (detected: {kind}).", hint=hint)
    if not 3.0 <= segment_max_seconds <= 28.0:
        raise ToolFailure(INVALID_INPUT, "segment_max_seconds must be between 3 and 28.",
                          hint="Use ~8-10 for subtitles (shorter cues), 28 for plain transcripts.")
    lang_given = language and language.strip().lower() not in ("auto", "detect", "unknown", "")
    lang = languages.resolve(language, "transcribe") if lang_given else None

    x = await asyncio.to_thread(media.load_audio, p)
    sr = media.ASR_SAMPLE_RATE
    duration = len(x) / sr
    segs = media.plan_segments(x, sr, max_seconds=segment_max_seconds)
    meter = costs.CallMeter()
    warnings: list[str] = []
    stem = p.stem
    out_txt = resolve_output(rt, output_path, f"{stem}.transcript.txt", ".txt")
    if out_txt.name.endswith(".transcript.txt"):
        out_json = out_txt.with_name(out_txt.name[: -len(".transcript.txt")] + ".segments.json")
    else:
        out_json = out_txt.with_suffix(".segments.json")

    def _write(seg_texts: list[TranscriptSegment], used_lang: str | None) -> None:
        out_txt.write_text("\n".join(s.text for s in seg_texts if s.text) + "\n", encoding="utf-8")
        out_json.write_text(json.dumps({
            "source": str(p), "language": used_lang, "duration_s": round(duration, 2),
            "segments": [s.model_dump() for s in seg_texts],
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    if not segs:
        _write([], lang)
        return TranscribeResult(transcript_path=str(out_txt), segments_path=str(out_json), language=lang,
                                language_source="given" if lang else "detected", duration_s=round(duration, 2), speech_s=0.0,
                                segment_count=0, no_speech=True, text="", text_preview="",
                                warnings=["No speech detected; nothing was sent to the model."], **_usage(meter))

    speech_s = sum(s.duration for s in segs)
    probe_cost = costs.transcribe_cost(PROBE_SECONDS) if not lang else 0.0
    _check_budget(sum(costs.transcribe_cost(s.duration) for s in segs) + probe_cost, max_cost_inr, "Transcribing this audio")

    detection = None
    reuse_first: str | None = None
    if not lang:
        heard, secs, guess = await _audio_probe(rt, x, sr, meter)
        detection = DetectLanguageResult(detected=_lang_info(guess), source="audio_probe", probe_transcript=heard,
                                         probe_seconds=round(secs, 2), **_usage(meter))
        code = _asr_code(guess.language)
        if guess.language and code is None:
            name = languages.KNOWN_UNSUPPORTED.get(guess.language, guess.language)
            _write([], None)
            return TranscribeResult(
                transcript_path=str(out_txt), segments_path=str(out_json), language=guess.language, language_name=name,
                language_source="detected", language_supported=False, detection=detection, duration_s=round(duration, 2),
                speech_s=round(secs, 2), segment_count=0, text=None, text_preview=heard[:PREVIEW_CHARS],
                warnings=[f"The speech appears to be {name}, which Indic-Transcribe does not support; the rest was not transcribed."],
                **_usage(meter))
        if code is None:
            warnings.append("Could not detect the language from the probe; transcribing with the model's auto-detection.")
        elif guess.confidence < 0.5:
            warnings.append(f"Language detection is uncertain ({languages.describe(code)}, confidence {guess.confidence:.2f}; "
                            f"candidates {guess.candidates}). Pass 'language' explicitly if you know it.")
        lang = code
        # The probe already covers a short clip entirely; reuse it rather than resending the same audio.
        if len(segs) == 1 and abs(segs[0].duration - secs) < 0.05:
            reuse_first = heard

    async def run(i: int, seg: media.Segment) -> TranscriptSegment:
        if i == 0 and reuse_first is not None:
            text = reuse_first
        else:
            text = await rt.api.transcribe(media.slice_wav(x, sr, seg), lang, meter)
        return TranscriptSegment(index=i, start=seg.start, end=seg.end, text=text.strip())

    results = await asyncio.gather(*(run(i, s) for i, s in enumerate(segs)))
    for r in results:
        if not r.text and (r.end - r.start) > 2:
            warnings.append(f"Segment {r.index} ({r.start:.1f}-{r.end:.1f}s) returned no text.")
        elif textproc.repeated_ngram_ratio(r.text) > 0.5:
            warnings.append(f"Segment {r.index} looks repetitive; the transcript may contain a repetition loop.")
    _write(results, lang)
    full = "\n".join(r.text for r in results if r.text)
    if not lang_given and lang is None:
        lang = _asr_code(textproc.detect_language(full).language)
    text, preview = _text_fields(full)
    return TranscribeResult(
        transcript_path=str(out_txt), segments_path=str(out_json), language=lang, language_name=languages.LANGUAGE_NAMES.get(lang or ""),
        language_source="given" if lang_given else "detected", detection=detection, duration_s=round(duration, 2),
        speech_s=round(speech_s, 2), segment_count=len(results), text=text, text_preview=preview,
        segments=list(results) if len(results) <= 40 else None, warnings=warnings, **_usage(meter),
    )


# ---------------------------------------------------------------------------
# translate_text
# ---------------------------------------------------------------------------

async def _translate_unit(rt: Runtime, text: str, target: str, script: str, meter: costs.CallMeter, depth: int = 0) -> tuple[str, bool, int]:
    """Translate one unit; re-split and retry pieces if the output was truncated. Returns (text, truncated, requests)."""
    pieces = [text] if len(text) <= TRANSLATE_CHUNK_CHARS else textproc.chunk_paragraphs(text, TRANSLATE_CHUNK_CHARS)
    if len(pieces) > 1:
        res = await asyncio.gather(*(_translate_unit(rt, piece, target, script, meter, depth) for piece in pieces))
        sep = "\n\n" if "\n\n" in text else " "
        return sep.join(r[0] for r in res), any(r[1] for r in res), sum(r[2] for r in res)
    r = await rt.api.translate(text, target, script, meter=meter)
    if r["truncated"] and depth < 2 and len(text) > 200:
        halves = textproc.chunk_sentences(text, max(100, len(text) // 2 + 1))
        if len(halves) > 1:
            res = await asyncio.gather(*(_translate_unit(rt, h, target, script, meter, depth + 1) for h in halves))
            return " ".join(x[0] for x in res), any(x[1] for x in res), 1 + sum(x[2] for x in res)
    return r["translation"], r["truncated"], 1


async def _translate_batch(rt: Runtime, texts: list[str], target: str, script: str, meter: costs.CallMeter) -> tuple[list[str], list[bool], int]:
    """Translate short lines together (one request), falling back to one request per line if alignment breaks."""
    if len(texts) == 1:
        t, tr, n = await _translate_unit(rt, texts[0], target, script, meter)
        return [t], [tr], n
    joined = "\n".join(t.replace("\n", " ") for t in texts)
    r = await rt.api.translate(joined, target, script, meter=meter)
    lines = [ln.strip() for ln in r["translation"].strip().split("\n") if ln.strip()]
    if len(lines) == len(texts) and not r["truncated"]:
        return lines, [False] * len(texts), 1
    res = await asyncio.gather(*(_translate_unit(rt, t, target, script, meter) for t in texts))
    return [x[0] for x in res], [x[1] for x in res], 1 + sum(x[2] for x in res)


def _batches(indices: list[int], texts: list[str]) -> list[list[int]]:
    out, cur, size = [], [], 0
    for i in indices:
        n = len(texts[i])
        if cur and (size + n > TRANSLATE_BATCH_CHARS or len(cur) >= TRANSLATE_BATCH_LINES):
            out.append(cur)
            cur, size = [], 0
        cur.append(i)
        size += n + 1
    if cur:
        out.append(cur)
    return out


async def translate_text(rt: Runtime, target_language: str, text: str | None = None, input_path: str | None = None,
                         script: str = "native", output_path: str | None = None, max_cost_inr: float | None = None) -> TranslateResult:
    if bool(text) == bool(input_path):
        raise ToolFailure(INVALID_INPUT, "Pass exactly one of 'text' or 'input_path'.",
                          hint="Use input_path for files produced by other tools (transcripts, OCR Markdown, segment JSON, SRT).")
    target = languages.resolve(target_language, "translate")
    script = (script or "native").lower()
    if script not in ("native", "roman", "codemix"):
        raise ToolFailure(INVALID_INPUT, f"Unknown script '{script}'.", hint="Use native, roman or codemix.")
    if target == "en" and script != "native":
        raise ToolFailure(INVALID_INPUT, "script must be 'native' when translating into English.")
    if script == "codemix" and target in ("ks-Deva", "sd-Arab"):
        raise ToolFailure(INVALID_INPUT, f"codemix is not available for {target}.")

    if input_path:
        p = resolve_input(rt, input_path, what="input_path")
        if media.file_kind(p) != "text":
            raise ToolFailure(UNSUPPORTED_FILE_TYPE, f"{p.name} is not a text file.",
                              hint="Get text first: transcribe_audio for audio/video, ocr_document for images/PDFs.")
        raw = p.read_text(encoding="utf-8-sig", errors="replace")
        ext = p.suffix.lower()
        stem = p.name[: -len(p.suffix)] if p.suffix else p.name
        stem = re.sub(r"\.(transcript|segments|ocr)$", "", stem)
    else:
        raw, ext, stem = text or "", ".md", _stem_for_text(text or "")

    if ext == ".json":
        try:
            data = formats.load_segments(p)
        except (ValueError, json.JSONDecodeError) as exc:
            raise ToolFailure(INVALID_INPUT, f"{p.name} is not a segment file: {exc}") from exc
        tmpl, fmt, out_ext = formats.parse_segments(data), "segments", ".segments.json"
    elif ext in (".srt", ".vtt"):
        tmpl, fmt, out_ext = formats.parse_subtitle_template(raw, ext[1:]), ext[1:], ext
    else:
        tmpl, fmt, out_ext = formats.parse_markdown(raw), "markdown", (".md" if ext in (".md", ".markdown") or not input_path else ext)

    slots = tmpl.slots
    source_text = "\n".join(s.text for s in slots)
    if not source_text.strip():
        raise ToolFailure(INVALID_INPUT, "There is no translatable text in the input.",
                          hint="The input is empty or contains only numbers/symbols.")
    if len(source_text) > 200_000:
        raise ToolFailure(INPUT_TOO_LARGE, f"Input has {len(source_text)} characters; the limit per call is 200,000.",
                          hint="Split the document and translate it in parts.")
    src_guess = textproc.detect_language(source_text[:20000])
    _check_budget(costs.translate_cost(costs.estimate_translate_tokens(len(source_text))), max_cost_inr, "Translating this text")

    meter = costs.CallMeter()
    warnings: list[str] = []
    out_name = f"{stem}.{target}{out_ext}"
    out = resolve_output(rt, output_path, out_name)

    translations: dict[int, str] = {}
    truncated: dict[int, bool] = {}
    requests = 0
    same_language = src_guess.language == target and src_guess.confidence >= 0.8 and src_guess.script not in (None, "Devanagari", "Arabic", "Bengali")
    if same_language:
        warnings.append(f"The input already appears to be {languages.describe(target)}; it was copied without a model call.")
        translations = {i: s.text for i, s in enumerate(slots)}
        truncated = {i: False for i in translations}
    else:
        short = [i for i, s in enumerate(slots) if s.kind in ("cell", "line") and len(s.text) <= 300]
        long_ = [i for i in range(len(slots)) if i not in set(short)]
        texts = [s.text for s in slots]
        # Identical cells/lines are translated once.
        uniq: dict[str, int] = {}
        for i in short:
            uniq.setdefault(texts[i], i)
        batch_jobs = [_translate_batch(rt, [texts[i] for i in b], target, script, meter) for b in _batches(list(uniq.values()), texts)]
        unit_jobs = [_translate_unit(rt, texts[i], target, script, meter) for i in long_]
        batch_res = await asyncio.gather(*batch_jobs)
        unit_res = await asyncio.gather(*unit_jobs)
        by_text: dict[str, tuple[str, bool]] = {}
        for b, (outs, truncs, n) in zip(_batches(list(uniq.values()), texts), batch_res, strict=True):
            requests += n
            for i, o, tr in zip(b, outs, truncs, strict=True):
                by_text[texts[i]] = (o, tr)
        for i in short:
            translations[i], truncated[i] = by_text[texts[i]]
        for i, (o, tr, n) in zip(long_, unit_res, strict=True):
            translations[i], truncated[i] = o, tr
            requests += n

    if fmt == "segments":
        result_text = formats.fill_segments(tmpl, translations, target)
        plain_out = "\n".join(translations[i] for i in sorted(translations))
    elif fmt in ("srt", "vtt"):
        result_text = formats.fill_subtitles(tmpl, translations)
        plain_out = "\n".join(translations[i] for i in sorted(translations))
    else:
        result_text = tmpl.fill(translations)
        plain_out = result_text
    out.write_text(result_text, encoding="utf-8")

    # verification: coverage, truncation, script
    outs = [translations[i] for i in range(len(slots))]
    empty = sum(1 for s, o in zip(slots, outs, strict=True) if s.text.strip() and not o.strip())
    unchanged = sum(1 for s, o in zip(slots, outs, strict=True) if not same_language and s.text.strip() == o.strip()
                    and textproc.indic_letter_count(s.text) + len(re.findall(r"[A-Za-z]", s.text)) > 3)
    expected_script = "Latin" if (target == "en" or script == "roman") else languages.LANGUAGE_SCRIPT.get(target)
    prof = textproc.script_profile("\n".join(outs))
    letters = sum(prof.values())
    ratio = (prof.get(expected_script, 0) / letters) if letters and expected_script else None
    if script == "codemix" and ratio is not None:
        ratio = min(1.0, ratio + prof.get("Latin", 0) / letters)
    n_trunc = sum(1 for v in truncated.values() if v)
    issues = []
    if n_trunc:
        issues.append(f"{n_trunc} unit(s) were truncated by the model even after re-splitting.")
    if empty:
        issues.append(f"{empty} unit(s) came back empty.")
    if unchanged > max(1, len(slots) // 10):
        issues.append(f"{unchanged} unit(s) came back unchanged (possibly untranslated).")
    if ratio is not None and letters > 20 and ratio < 0.6:
        issues.append(f"Only {ratio:.0%} of output letters are in {expected_script} script.")
    length_ratio = len(plain_out) / max(1, len(source_text))
    if length_ratio < 0.3 and len(source_text) > 200:
        issues.append(f"Output is much shorter than the input (ratio {length_ratio:.2f}); content may be missing.")
    check = TranslationCheck(
        passed=not issues, units_total=len(slots), units_translated=len(slots) - empty, truncated_units=n_trunc,
        empty_units=empty, unchanged_units=unchanged, target_script=expected_script,
        target_script_ratio=round(ratio, 3) if ratio is not None else None, length_ratio=round(length_ratio, 3), issues=issues,
    )
    full, preview = _text_fields(plain_out if fmt != "markdown" else result_text)
    tables = len(re.findall(r"(?m)^\s*\|?\s*:?-{2,}", result_text)) + result_text.lower().count("<table")
    return TranslateResult(
        output_path=str(out), format=fmt, target_language=target, target_language_name=languages.LANGUAGE_NAMES[target], script=script,
        source_language_detected=_lang_info(src_guess), chars_in=len(source_text), chars_out=len(plain_out), api_requests=requests,
        tables_preserved=tables, verification=check, text=full, text_preview=preview, warnings=warnings, **_usage(meter),
    )


# ---------------------------------------------------------------------------
# synthesize_speech
# ---------------------------------------------------------------------------

def _to_asr_wav(wav: bytes) -> bytes:
    x, sr = media.wav_bytes_to_array(wav)
    return media.to_wav_bytes(media._resample(x, sr, media.ASR_SAMPLE_RATE), media.ASR_SAMPLE_RATE)


async def _check_clip(rt: Runtime, wav: bytes, expected: str, lang: str, meter: costs.CallMeter) -> tuple[float, float, str]:
    heard = await rt.api.transcribe(_to_asr_wav(wav), _asr_code(lang), meter)
    return textproc.similarity(expected, heard), textproc.length_ratio(expected, heard), heard


def _clip_ok(sim: float, lr: float) -> bool:
    return sim >= SPEECH_PASS_SIMILARITY and 0.6 <= lr <= 1.45


def _silent(wav: bytes) -> bool:
    x, _ = media.wav_bytes_to_array(wav)
    return len(x) == 0 or float(np.sqrt(np.mean(x * x))) < 1e-3


async def synthesize_speech(rt: Runtime, language: str, text: str | None = None, input_path: str | None = None,
                            voice: str | None = None, gender: str | None = None, style: str | None = None,
                            output_format: str = "mp3", output_path: str | None = None, verify: bool = True,
                            max_cost_inr: float | None = None) -> SpeechResult:
    if bool(text) == bool(input_path):
        raise ToolFailure(INVALID_INPUT, "Pass exactly one of 'text' or 'input_path'.",
                          hint="Use input_path for text produced by translate_text.")
    lang = languages.resolve(language, "speak")
    fmt = (output_format or "mp3").lower().lstrip(".")
    if fmt not in ("mp3", "wav", "ogg", "flac"):
        raise ToolFailure(INVALID_INPUT, f"Unsupported output_format '{output_format}'.", hint="Use mp3, wav, ogg or flac.")
    warnings: list[str] = []
    names = languages.all_voice_names()
    if voice:
        canonical = names.get(voice.strip().lower())
        if not canonical:
            raise ToolFailure(UNSUPPORTED_VOICE, f"Unknown voice '{voice}'.", hint="Call list_voices, or omit voice and pass gender.",
                              details={"voices": sorted(names.values())})
        voice = canonical
        vg = languages.voice_gender(voice)
        if gender and vg and gender.lower() != vg:
            warnings.append(f"Voice {voice} is {vg}; the requested gender '{gender}' was ignored.")
    else:
        if gender and gender.lower() not in ("female", "male"):
            raise ToolFailure(INVALID_INPUT, f"gender must be 'female' or 'male', got '{gender}'.")
        voice = languages.pick_voice(lang, gender)
    if style:
        match = next((s for s in languages.STYLES if s.lower() == style.strip().lower()), None)
        if not match:
            raise ToolFailure(INVALID_INPUT, f"Unknown style '{style}'.", details={"styles": list(languages.STYLES)})
        style = match

    if input_path:
        p = resolve_input(rt, input_path, what="input_path")
        if media.file_kind(p) != "text":
            raise ToolFailure(UNSUPPORTED_FILE_TYPE, f"{p.name} is not a text file.")
        body, src_fmt = formats.text_from_file(p)
        stem = p.name.split(".")[0]
    else:
        body, src_fmt, stem = text or "", "markdown", _stem_for_text(text or "")
    spoken = formats.speech_text_from_markdown(body) if src_fmt in ("markdown", "text") else body
    spoken = spoken.strip()
    if not spoken:
        raise ToolFailure(INVALID_INPUT, "There is no text to speak.")
    if len(spoken) > TTS_MAX_CHARS:
        raise ToolFailure(INPUT_TOO_LARGE, f"Text has {len(spoken)} characters; the limit per call is {TTS_MAX_CHARS}.",
                          hint="Summarise or split the text. Speech costs Rs 6 per 10,000 characters.")
    ok, found, share = textproc.script_matches_language(spoken, lang, languages.LANGUAGE_SCRIPT)
    if not ok and share > 0.5:
        raise ToolFailure(LANGUAGE_MISMATCH,
                          f"The text is mostly {found} script, but language is {languages.describe(lang)} "
                          f"({languages.LANGUAGE_SCRIPT.get(lang)} script).",
                          hint=f"Translate the text into {languages.describe(lang)} with translate_text first, then synthesise that file.",
                          details={"text_script": found, "expected_script": languages.LANGUAGE_SCRIPT.get(lang)})

    chunks = textproc.chunk_sentences(spoken, TTS_CHUNK_CHARS)
    est = costs.speak_cost(len(spoken))
    if verify:
        est += costs.transcribe_cost(costs.speech_seconds_estimate(len(spoken)) * 1.2)
    _check_budget(est, max_cost_inr, "Synthesising this text")
    meter = costs.CallMeter()

    async def synth(t: str) -> bytes:
        wav = await rt.api.speak(t, voice, lang, style, meter)
        return wav

    clips = list(await asyncio.gather(*(synth(c) for c in chunks)))
    check = None
    if verify:
        results = list(await asyncio.gather(*(_check_clip(rt, w, c, lang, meter) for w, c in zip(clips, chunks, strict=True))))
        resynth: list[int] = []
        issues: list[str] = []
        for i, (sim, lr, _heard) in enumerate(results):
            if _clip_ok(sim, lr) and not _silent(clips[i]):
                continue
            # Indic-Speak can repeat or drop words on longer inputs: regenerate this chunk as smaller pieces.
            pieces = textproc.chunk_sentences(chunks[i], max(40, len(chunks[i]) // 2 + 1))
            if len(pieces) < 2:
                pieces = textproc._hard_wrap(chunks[i], max(20, len(chunks[i]) // 2 + 1))
            if len(pieces) >= 2:
                sub = list(await asyncio.gather(*(synth(pc) for pc in pieces)))
                joined = media.concat_wavs(sub, gap_s=0.12)
                sim2, lr2, _ = await _check_clip(rt, joined, chunks[i], lang, meter)
                resynth.append(i)
                if sim2 > sim:
                    clips[i], results[i] = joined, (sim2, lr2, "")
                    sim, lr = sim2, lr2
            if not _clip_ok(sim, lr):
                kind = "repetition" if lr > 1.45 else "omission" if lr < 0.6 else "low similarity"
                issues.append(f"Chunk {i}: {kind} (similarity {sim:.2f}, length ratio {lr:.2f}).")
        weights = [len(c) for c in chunks]
        overall = sum(r[0] * w for r, w in zip(results, weights, strict=True)) / max(1, sum(weights))
        failed = sum(1 for r in results if not _clip_ok(r[0], r[1]))
        check = SpeechCheck(passed=failed == 0, similarity=round(overall, 3), chunks_checked=len(chunks), chunks_failed=failed,
                            resynthesized_chunks=resynth, issues=issues)
        if failed:
            warnings.append(f"{failed} of {len(chunks)} chunk(s) failed the back-transcription check; listen before relying on them.")
    wav = media.concat_wavs(clips, gap_s=0.3)
    out = resolve_output(rt, output_path, f"{stem}.{lang}.speech.{fmt}")
    written, w2 = await asyncio.to_thread(media.write_audio, wav, out, fmt, f"AI-generated speech ({languages.describe(lang)})")
    warnings += w2
    return SpeechResult(
        audio_path=str(written), format=written.suffix.lstrip("."), duration_s=round(media.wav_duration(wav), 2), language=lang,
        voice=voice, voice_gender=languages.voice_gender(voice), style=style, characters=len(spoken), chunks=len(chunks),
        verification=check, warnings=warnings, **_usage(meter),
    )


async def verify_speech(rt: Runtime, audio_path: str, language: str, expected_text: str | None = None,
                        expected_text_path: str | None = None, max_cost_inr: float | None = None) -> VerifySpeechResult:
    if bool(expected_text) == bool(expected_text_path):
        raise ToolFailure(INVALID_INPUT, "Pass exactly one of 'expected_text' or 'expected_text_path'.")
    lang = _asr_code(languages.normalize_code(language)) or languages.resolve(language, "transcribe")
    p = resolve_input(rt, audio_path, what="audio_path")
    if media.file_kind(p) not in ("audio", "video"):
        raise ToolFailure(UNSUPPORTED_FILE_TYPE, f"{p.name} is not audio.")
    if expected_text_path:
        expected, fmt = formats.text_from_file(resolve_input(rt, expected_text_path, what="expected_text_path"))
        expected = formats.speech_text_from_markdown(expected) if fmt in ("markdown", "text") else expected
    else:
        expected = formats.speech_text_from_markdown(expected_text or "")
    x = await asyncio.to_thread(media.load_audio, p)
    sr = media.ASR_SAMPLE_RATE
    segs = media.plan_segments(x, sr)
    _check_budget(sum(costs.transcribe_cost(s.duration) for s in segs), max_cost_inr, "Verifying this audio")
    meter = costs.CallMeter()
    heard_parts = await asyncio.gather(*(rt.api.transcribe(media.slice_wav(x, sr, s), lang, meter) for s in segs))
    heard = " ".join(h.strip() for h in heard_parts)
    sim, lr = textproc.similarity(expected, heard), textproc.length_ratio(expected, heard)
    issues = []
    if lr > 1.45:
        issues.append("The audio seems to contain more speech than the text (repetition?).")
    if lr < 0.6:
        issues.append("The audio seems to be missing part of the text (omission?).")
    if sim < SPEECH_PASS_SIMILARITY:
        issues.append(f"Low similarity between text and audio ({sim:.2f}).")
    return VerifySpeechResult(passed=not issues, similarity=round(sim, 3), length_ratio=round(lr, 3), heard_text_preview=heard[:PREVIEW_CHARS],
                              duration_s=round(len(x) / sr, 2), issues=issues, **_usage(meter))


# ---------------------------------------------------------------------------
# ocr_document
# ---------------------------------------------------------------------------

def _is_blank(data: bytes) -> bool:
    """True only for pages with essentially no ink, so a sparse title page is never skipped."""
    import io

    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        g = im.convert("L")
        if max(g.size) > 1600:
            g.thumbnail((1600, 1600))
        a = np.asarray(g, dtype=np.int16)
    background = int(np.median(a))
    ink = int(np.count_nonzero(a < background - 60))
    return ink < 30


async def ocr_document(rt: Runtime, path: str, pages: str | None = None, table_format: str = "markdown",
                       output_path: str | None = None, max_cost_inr: float | None = None) -> OcrResult:
    p = resolve_input(rt, path)
    kind = media.file_kind(p)
    if kind not in ("image", "pdf"):
        hint = {"text": "This is already text; use read_text.", "audio": "Use transcribe_audio.", "video": "Use transcribe_audio."}.get(kind)
        raise ToolFailure(UNSUPPORTED_FILE_TYPE, f"{p.name} is not an image or PDF (detected: {kind}).", hint=hint)
    if table_format not in ("markdown", "html"):
        raise ToolFailure(INVALID_INPUT, "table_format must be 'markdown' or 'html'.",
                          hint="markdown reads well and translates cleanly; html keeps merged cells.")
    total = media.pdf_page_count(p) if kind == "pdf" else media.image_frames(p)
    page_list = _pages_arg(pages, total)
    if len(page_list) > OCR_MAX_PAGES:
        raise ToolFailure(INPUT_TOO_LARGE, f"{len(page_list)} pages requested; the limit per call is {OCR_MAX_PAGES}.",
                          hint="Pass a page range such as '1-50' and call again for the rest.")
    _check_budget(costs.ocr_cost(len(page_list)), max_cost_inr, f"OCR of {len(page_list)} page(s)")

    if kind == "pdf":
        images = await asyncio.to_thread(media.render_pdf_pages, p, page_list)
    else:
        images = []
        for pg in page_list:
            data, mime, _ = await asyncio.to_thread(media.prepare_image, p, pg - 1)
            images.append((pg, data, mime))

    meter = costs.CallMeter()

    async def one(pg: int, data: bytes, mime: str) -> tuple[int, dict | None, OcrPage]:
        if await asyncio.to_thread(_is_blank, data):
            return pg, None, OcrPage(page=pg, chars=0, skipped="blank page")
        r: dict | None = None
        err: ToolFailure | None = None
        # A 502 means a block exceeded max_tokens (or the image did not decode): retry with a larger limit.
        for max_tokens in (None, 4096, 8192):
            try:
                r = await rt.api.ocr(data, mime, table_format, max_tokens, meter)
                err = None
                break
            except ToolFailure as exc:
                err = exc
                if exc.details.get("status") != 502:
                    break
        if r is None:
            return pg, None, OcrPage(page=pg, chars=0, error=f"{err.code}: {err.message}" if err else "unknown error")
        md = r["markdown"]
        labels: dict[str, int] = {}
        for b in r["blocks"]:
            lab = str(b.get("label", "?"))
            labels[lab] = labels.get(lab, 0) + 1
        g = textproc.detect_language(md)
        return pg, r, OcrPage(page=pg, chars=len(md), language=g.language, script=g.script,
                              tables=labels.get("Table", 0), block_labels=labels)

    results = await asyncio.gather(*(one(pg, d, m) for pg, d, m in images))
    failed = [pg for pg, r, info in results if info.error]
    if failed and len(failed) == len([1 for _, _, i in results if not i.skipped]):
        first = next(i.error for _, _, i in results if i.error)
        raise ToolFailure(MEDIA_DECODE_FAILED if "decode" in (first or "") else UPSTREAM_ERROR,
                          f"OCR failed on every page: {first}", retryable=True, details={"failed_pages": failed})
    md_parts, blocks_out = [], {}
    for pg, r, _ in sorted(results, key=lambda t: t[0]):
        if r is None:
            continue
        md_parts.append(f"<!-- page {pg} -->\n{r['markdown'].strip()}\n")
        blocks_out[str(pg)] = r["blocks"]
    markdown = "\n".join(md_parts)
    out = resolve_output(rt, output_path, f"{p.stem}.ocr.md", ".md")
    out.write_text(markdown, encoding="utf-8")
    blocks_path = out.with_name(out.name[:-3] + ".blocks.json")
    blocks_path.write_text(json.dumps({"source": str(p), "pages": blocks_out}, ensure_ascii=False, indent=1), encoding="utf-8")
    warnings = []
    skipped = [i.page for _, _, i in results if i.skipped]
    if skipped:
        warnings.append(f"Skipped blank page(s): {skipped}.")
    if failed:
        warnings.append(f"OCR failed on page(s) {failed}; the output is incomplete.")
    if not markdown.strip():
        warnings.append("No text was found.")
    full, preview = _text_fields(markdown)
    return OcrResult(
        output_path=str(out), blocks_path=str(blocks_path), pages_requested=len(page_list),
        pages_processed=len([1 for _, r, _ in results if r is not None]), failed_pages=failed,
        detected_language=_lang_info(textproc.detect_language(markdown)) if markdown.strip() else None,
        pages=[i for _, _, i in sorted(results, key=lambda t: t[0])], text=full, text_preview=preview, warnings=warnings, **_usage(meter),
    )


# ---------------------------------------------------------------------------
# make_subtitles (free)
# ---------------------------------------------------------------------------

def make_subtitles(rt: Runtime, segments_path: str, format: str = "srt", output_path: str | None = None,
                   max_chars_per_cue: int = 84) -> SubtitleResult:
    fmt = format.lower().lstrip(".")
    if fmt not in ("srt", "vtt"):
        raise ToolFailure(INVALID_INPUT, "format must be 'srt' or 'vtt'.")
    if not 20 <= max_chars_per_cue <= 200:
        raise ToolFailure(INVALID_INPUT, "max_chars_per_cue must be between 20 and 200.")
    p = resolve_input(rt, segments_path, what="segments_path")
    if p.suffix.lower() in (".srt", ".vtt"):
        cues_in = formats.parse_subtitles(p.read_text(encoding="utf-8-sig"))
        lang = None
    else:
        try:
            data = formats.load_segments(p)
        except (ValueError, json.JSONDecodeError) as exc:
            raise ToolFailure(INVALID_INPUT, f"{p.name} is not a segment file: {exc}",
                              hint="Pass the segments_path from transcribe_audio, or its translation from translate_text.") from exc
        cues_in, lang = data["segments"], data.get("language")
    cues = []
    for s in cues_in:
        text = str(s["text"]).strip()
        if not text:
            continue
        start, end = float(s["start"]), float(s["end"])
        pieces = textproc.chunk_sentences(text, max_chars_per_cue) if len(text) > max_chars_per_cue else [text]
        total = sum(len(x) for x in pieces) or 1
        t = start
        for piece in pieces:  # split time in proportion to characters
            dur = (end - start) * len(piece) / total
            cues.append({"start": t, "end": t + dur, "text": piece})
            t += dur
    if not cues:
        raise ToolFailure(INVALID_INPUT, "The segments contain no text, so there is nothing to subtitle.")
    body = formats.format_subtitles(cues, fmt)
    stem = p.name.split(".segments")[0].split(".")[0]
    suffix_lang = f".{lang}" if lang else ""
    out = resolve_output(rt, output_path, f"{stem}{suffix_lang}.{fmt}", "." + fmt)
    out.write_text(body, encoding="utf-8")
    return SubtitleResult(subtitle_path=str(out), format=fmt, cue_count=len(cues), duration_s=round(cues[-1]["end"], 2),
                          language=lang, preview=body[:600])
