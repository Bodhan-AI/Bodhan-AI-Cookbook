"""Tool implementations. Each returns a pydantic model; failures raise ToolFailure."""
from __future__ import annotations

import asyncio
import io
import json
import re
import tempfile
from pathlib import Path

from . import audio, client, verify
from .config import get_settings
from .costs import tracker
from .errors import BudgetExceeded, ToolFailure
from .langs import (ALL_VOICES, CODE_NAMES, STYLES, VOICES, guess_language, normalize_lang, pick_voice,
                    script_counts, supported_list)
from .paths import read_text, resolve_input, resolve_output
from .schemas import (AudioLanguageResult, CapabilitiesResult, InspectResult, OcrResult, PageInfo, SpeechResult,
                      SubtitleResult, TextLanguageResult, TranscribeResult, TranslateResult,
                      TranslateSegmentsResult, VerifySpeechResult, VerifyTranslationResult)
from .textsplit import chunk_blocks, chunk_for_tts, split_in_half

AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".amr", ".wma"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
TEXT_EXT = {".txt", ".md", ".srt", ".vtt", ".json", ".csv", ".html", ".htm", ".tsv"}


def _preview(text: str, n: int = 300) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    return t[:n] + ("…" if len(t) > n else "")


def _need_one(text: str | None, path: str | None, what: str) -> str:
    if bool(text) == bool(path):
        raise ToolFailure("invalid_arguments", f"Provide exactly one of '{what}' or '{what}_path'.",
                          hint="Prefer the *_path form so long text never passes through the model context.")
    return text if text else read_text(path)  # type: ignore[arg-type]


# ================================================================== inspect / capabilities
def _inspect_one(p: Path) -> dict:
    ext = p.suffix.lower()
    d: dict = {"path": str(p), "size_bytes": p.stat().st_size}
    if p.is_dir():
        d["kind"] = "directory"
    elif ext in AUDIO_EXT or ext in VIDEO_EXT:
        d["kind"] = "audio" if ext in AUDIO_EXT else "video"
        try:
            d["duration_s"] = round(audio.probe_duration(p), 2)
            d["has_audio"] = audio.has_audio_stream(p)
        except ToolFailure as e:
            d["warnings"] = [e.message]
    elif ext in IMAGE_EXT:
        d["kind"] = "image"
        try:
            from PIL import Image
            with Image.open(p) as im:
                d["width"], d["height"] = im.size
        except Exception as e:  # noqa: BLE001
            d["warnings"] = [f"Could not open image: {e}"]
    elif ext == ".pdf":
        d["kind"] = "pdf"
        try:
            import pypdfium2 as pdfium
            d["pages"] = len(pdfium.PdfDocument(str(p)))
        except Exception as e:  # noqa: BLE001
            d["warnings"] = [f"Could not open PDF: {e}"]
    elif ext in TEXT_EXT:
        d["kind"] = "text"
        d["chars"] = len(p.read_text(encoding="utf-8", errors="replace"))
    else:
        d["kind"] = "other"
    return d


async def inspect_file(path: str) -> InspectResult:
    p = resolve_input(path)
    d = await asyncio.to_thread(_inspect_one, p)
    if p.is_dir():
        files = sorted(x for x in p.rglob("*") if x.is_file())[:50]
        d["entries"] = [await asyncio.to_thread(_inspect_one, f) for f in files]
    warnings = d.pop("warnings", [])
    return InspectResult(**d, warnings=warnings)


async def list_capabilities() -> CapabilitiesResult:
    s = get_settings()
    return CapabilitiesResult(
        transcribe_languages=supported_list("transcribe"), translate_languages=supported_list("translate"),
        speak_languages=supported_list("speak"), ocr="script-agnostic; PNG/JPEG pages (PDFs are rendered for you)",
        voices=VOICES, styles=STYLES, language_names=CODE_NAMES,
        limits={"asr_window_seconds": s.asr_max_window_s, "tts_chars_per_request": s.tts_max_chars,
                "translate_chars_per_request": s.translate_max_chars,
                "note": "All limits are handled inside the tools; callers pass whole files/texts."},
        prices_inr={"transcribe": "0.10 per audio minute", "translate": "0.20 per 10k output tokens",
                    "speak": "6 per 10k characters", "ocr": "0.20 per page image"})


async def detect_text_language(text: str | None = None, input_path: str | None = None) -> TextLanguageResult:
    t = _need_one(text, input_path, "text")
    return TextLanguageResult(**guess_language(t[:20000]))


# ================================================================== speech to text
def _prep_audio(path: Path, start_s: float, end_s: float | None):
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "a.wav"
        audio.decode_to_wav(path, wav, 16000, start_s or None, end_s)
        dur = audio.probe_duration(wav)
        params, frames = audio.read_wav(wav)
        silences = audio.detect_silences(wav)
    return dur, params, frames, silences


async def _asr_file(path: Path, language: str | None, start_s: float = 0.0, end_s: float | None = None):
    s = get_settings()
    dur, params, frames, silences = await asyncio.to_thread(_prep_audio, path, start_s, end_s)
    chunks = audio.plan_chunks(dur, silences, max_len=s.asr_max_window_s)
    todo = chunks
    skipped, prev = [], 0.0
    for a, b in chunks + [(dur, dur)]:  # stretches of silence that are never sent to the (per-second billed) API
        if a - prev > 1.0:
            skipped.append({"start": round(prev + start_s, 2), "end": round(a + start_s, 2), "reason": "silence"})
        prev = b
    results = await asyncio.gather(*[
        client.transcribe(audio.slice_wav(params, frames, a, b), language) for a, b in todo])
    segs = []
    for (a, b), r in zip(todo, results):
        segs.append({"id": len(segs), "start": round(a + start_s, 2), "end": round(b + start_s, 2),
                     "text": r["text"], "cached": r["cached"]})
    return segs, skipped, dur, len(chunks)


async def _detect_audio_language(path: Path) -> dict:
    """Transcribe the first window of speech with no language hint, then look at the script."""
    s = get_settings()
    dur, params, frames, sil = await asyncio.to_thread(_prep_audio, path, 0.0, None)
    start = 0.0
    for a, b in sil:  # skip leading silence
        if a <= 0.05:
            start = b
    start = min(start, max(0.0, dur - 1.0))
    end = min(dur, start + s.asr_max_window_s)
    r = await client.transcribe(audio.slice_wav(params, frames, start, end), None)
    g = guess_language(r["text"])
    g["sample_text"] = _preview(r["text"], 200)
    return g


async def detect_audio_language(audio_path: str) -> AudioLanguageResult:
    p = resolve_input(audio_path)
    if not await asyncio.to_thread(audio.has_audio_stream, p):
        raise ToolFailure("no_audio_stream", f"{audio_path} has no audio track.")
    g = await _detect_audio_language(p)
    if g["language"] is None:
        raise ToolFailure("no_speech", "No speech was recognised in the first seconds of this file.",
                          hint="The clip may be silent or non-speech audio. Report that honestly.")
    return AudioLanguageResult(language=g["language"], candidates=g["candidates"], confidence=g["confidence"],
                               note=g["note"], sample_text=g["sample_text"])


async def transcribe_audio(audio_path: str, language: str | None = None, start_s: float = 0.0,
                           end_s: float | None = None, output_path: str | None = None) -> TranscribeResult:
    p = resolve_input(audio_path)
    if not await asyncio.to_thread(audio.has_audio_stream, p):
        raise ToolFailure("no_audio_stream", f"{audio_path} has no audio track.")
    warnings: list[str] = []
    if language:
        lang, source, conf = normalize_lang(language, "transcribe"), "given", "high"
    else:
        g = await _detect_audio_language(p)
        if not g["language"]:
            raise ToolFailure("no_speech", "No speech was recognised, so the language could not be detected.",
                              hint="Pass `language` explicitly if you know it.")
        lang, source, conf = normalize_lang(g["language"], "transcribe"), "auto-detected", g["confidence"]
        if conf != "high":
            warnings.append(f"Language auto-detected as '{lang}' with {conf} confidence. {g['note']}")
    segs, skipped, dur, n_chunks = await _asr_file(p, lang, start_s, end_s)
    text = "\n".join(x["text"] for x in segs if x["text"]).strip()
    if not text:
        warnings.append("No speech was recognised in this audio.")
    key = f"{p}|{lang}|{start_s}|{end_s}"
    seg_path = resolve_output(output_path, "transcript", ".json", key)
    txt_path = seg_path.with_suffix(".txt")
    seg_path.write_text(json.dumps({"language": lang, "source": str(p), "duration_s": round(dur, 2),
                                    "segments": segs, "skipped": skipped}, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    txt_path.write_text(text, encoding="utf-8")
    return TranscribeResult(language=lang, language_source=source, language_confidence=conf,
                            duration_s=round(dur, 2), chunks=n_chunks, skipped_silent_chunks=len(skipped),
                            characters=len(text), segments_path=str(seg_path), text_path=str(txt_path),
                            preview=_preview(text), warnings=warnings)


def _ts(sec: float, vtt: bool) -> str:
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s_, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s_:02d}{'.' if vtt else ','}{ms:03d}"


async def write_subtitles(segments_path: str, output_path: str | None = None, format: str = "srt",
                          max_chars_per_cue: int = 84) -> SubtitleResult:
    fmt = format.lower()
    if fmt not in ("srt", "vtt"):
        raise ToolFailure("invalid_arguments", "format must be 'srt' or 'vtt'.")
    data = json.loads(read_text(segments_path))
    cues = []
    for seg in data.get("segments", []):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        pieces = chunk_for_tts(text, max(20, max_chars_per_cue))
        total = sum(len(x) for x in pieces) or 1
        t = float(seg["start"])
        span = float(seg["end"]) - t
        for piece in pieces:
            d = span * len(piece) / total
            cues.append((t, t + d, piece))
            t += d
    out = resolve_output(output_path, "subtitles", f".{fmt}", segments_path)
    lines = ["WEBVTT", ""] if fmt == "vtt" else []
    for i, (a, b, txt) in enumerate(cues, 1):
        if fmt == "srt":
            lines.append(str(i))
        lines += [f"{_ts(a, fmt == 'vtt')} --> {_ts(b, fmt == 'vtt')}", txt, ""]
    out.write_text("\n".join(lines), encoding="utf-8")
    warnings = [] if cues else ["No text in the segments file, so the subtitle file is empty."]
    return SubtitleResult(path=str(out), format=fmt, cues=len(cues), warnings=warnings)


# ================================================================== translation
async def _translate_chunk(c: str, target: str, script: str, stats: dict, depth: int = 0) -> str:
    r = await client.translate(c, target, script)
    if not r["truncated"]:
        return r["translation"]
    stats["retries"] += 1
    halves = split_in_half(c)
    if depth >= 3 or len(halves) < 2:
        raise ToolFailure("translation_truncated", "Indic-Translate kept truncating this passage.",
                          hint="Split the text into smaller files and retry.")
    parts = await asyncio.gather(*[_translate_chunk(h, target, script, stats, depth + 1) for h in halves])
    return "\n\n".join(parts)


async def _translate_long(text: str, target: str, script: str = "native") -> tuple[str, dict]:
    s = get_settings()
    chunks = chunk_blocks(text, s.translate_max_chars) or [text]
    stats = {"retries": 0, "chunks": len(chunks)}
    parts = await asyncio.gather(*[_translate_chunk(c, target, script, stats) for c in chunks])
    return "\n\n".join(parts), stats


async def translate_text(target_language: str, text: str | None = None, input_path: str | None = None,
                         script: str = "native", output_path: str | None = None) -> TranslateResult:
    target = normalize_lang(target_language, "translate")
    if script not in ("native", "roman", "codemix"):
        raise ToolFailure("invalid_arguments", "script must be 'native', 'roman' or 'codemix'.")
    if target == "en" and script != "native":
        raise ToolFailure("invalid_arguments", "script options do not apply when translating into English.")
    src = _need_one(text, input_path, "text")
    if not src.strip():
        raise ToolFailure("empty_input", "The text to translate is empty.",
                          hint="If the text came from OCR or a transcript, that step produced nothing; report it.")
    out, stats = await _translate_long(src, target, script)
    key = f"{input_path or src[:200]}|{target}|{script}"
    tpath = resolve_output(output_path, f"translation.{target}", ".md" if "\n|" in src or "#" in src else ".txt", key)
    spath = resolve_output(None, f"source.{target}", ".txt", key + "|source")   # sidecar: kept in intermediate/
    tpath.write_text(out, encoding="utf-8")
    spath.write_text(src, encoding="utf-8")
    chk = verify.check_translation(src, out, target)
    return TranslateResult(translation_path=str(tpath), source_path=str(spath), target_language=target,
                           chunks=stats["chunks"], truncation_retries=stats["retries"], characters_in=len(src),
                           characters_out=len(out), checks_ok=chk["ok"], issues=chk["issues"],
                           preview=_preview(out), warnings=chk["warnings"])


async def translate_segments(segments_path: str, target_language: str,
                             output_path: str | None = None) -> TranslateSegmentsResult:
    target = normalize_lang(target_language, "translate")
    data = json.loads(read_text(segments_path))
    segs = data.get("segments", [])

    async def one(seg):
        t = (seg.get("text") or "").strip()
        if not t:
            return "", None
        tr, _ = await _translate_long(t, target)
        return tr, verify.check_translation(t, tr, target)

    results = await asyncio.gather(*[one(s) for s in segs])
    out_segs, problems = [], []
    for seg, (tr, chk) in zip(segs, results):
        out_segs.append({**{k: v for k, v in seg.items() if k != "cached"}, "source_text": seg.get("text", ""), "text": tr})
        if chk and not chk["ok"]:
            problems.append(f"segment {seg['id']} ({seg['start']}s): " + "; ".join(chk["issues"]))
    out = resolve_output(output_path, f"segments.{target}", ".json", f"{segments_path}|{target}")
    out.write_text(json.dumps({**data, "language": target, "segments": out_segs}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    return TranslateSegmentsResult(segments_path=str(out), target_language=target, segments=len(out_segs),
                                   problems=problems[:20])


async def verify_translation(target_language: str, translation: str | None = None,
                             translation_path: str | None = None, source: str | None = None,
                             source_path: str | None = None, back_translate: bool = False
                             ) -> VerifyTranslationResult:
    target = normalize_lang(target_language, "translate")
    tr = _need_one(translation, translation_path, "translation")
    src = _need_one(source, source_path, "source")
    chk = verify.check_translation(src, tr, target)
    cov = None
    issues = list(chk["issues"])
    if back_translate and target != "en":
        sc = script_counts(src)
        if sc and sc.get("Latin", 0) / sum(sc.values()) > 0.8:  # only meaningful for English sources
            back, _ = await _translate_long(tr, "en")
            cov = round(verify.english_coverage(src, back), 3)
            if cov < 0.35:
                issues.append(f"Back-translation to English recovers only {cov:.0%} of the source's content words.")
        else:
            chk["warnings"].append("back_translate skipped: it needs an English source.")
    return VerifyTranslationResult(ok=not issues, issues=issues, metrics=chk["metrics"],
                                   back_translation_coverage=cov, warnings=chk["warnings"])


# ================================================================== OCR
_FURNITURE = {"Header", "Footer", "Page-number", "Folio", "Website-link", "Advertisement"}
_HEADING = {"Title": "# ", "Chapter-title": "# ", "Section-title": "## ", "Sub-section-title": "### ",
            "Sub-sub-section-title": "#### "}


def blocks_to_markdown(blocks: list[dict], fallback: str) -> str:
    out = []
    for b in sorted(blocks, key=lambda x: x.get("order", 0)):
        text = (b.get("text") or "").strip()
        if not text or b.get("label") in _FURNITURE:
            continue
        out.append(_HEADING.get(b.get("label"), "") + text)
    return "\n\n".join(out) if out else fallback.strip()


def _parse_pages(spec: str | None, total: int) -> list[int]:
    if not spec:
        return list(range(1, total + 1))
    pages: list[int] = []
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                a, b = part.split("-", 1)
                pages += list(range(int(a), int(b) + 1))
            else:
                pages.append(int(part))
        except ValueError:
            raise ToolFailure("invalid_arguments", f"Bad page spec '{spec}'.", hint="Use e.g. '1-3,5'.")
    bad = [x for x in pages if x < 1 or x > total]
    if bad:
        raise ToolFailure("invalid_arguments", f"Pages {bad} are outside 1..{total}.")
    return sorted(dict.fromkeys(pages))


def _to_jpeg(im, max_side: int = 2400) -> bytes:
    from PIL import Image
    im = im.convert("RGB")
    if max(im.size) > max_side:
        im.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def _render_inputs(p: Path, pages_spec: str | None) -> list[tuple[int, bytes]]:
    from PIL import Image
    ext = p.suffix.lower()
    if ext == ".pdf":
        try:
            import pypdfium2 as pdfium
            doc = pdfium.PdfDocument(str(p))
        except Exception as e:  # noqa: BLE001
            raise ToolFailure("pdf_error", f"Could not open PDF: {e}")
        wanted = _parse_pages(pages_spec, len(doc))
        if len(wanted) > 60:
            raise ToolFailure("too_many_pages", f"{len(wanted)} pages requested; the limit per call is 60.",
                              hint="Use the `pages` argument to process the document in parts.")
        return [(n, _to_jpeg(doc[n - 1].render(scale=200 / 72).to_pil())) for n in wanted]
    if ext in IMAGE_EXT:
        try:
            with Image.open(p) as im:
                return [(1, _to_jpeg(im))]
        except Exception as e:  # noqa: BLE001
            raise ToolFailure("image_error", f"Could not open image: {e}")
    raise ToolFailure("unsupported_file_type", f"OCR needs a PDF or an image (png/jpg/webp...), got '{p.suffix}'.",
                      hint="For audio use transcribe_audio; for text files read them directly.")


async def ocr_document(input_path: str, pages: str | None = None, table_format: str = "markdown",
                       output_path: str | None = None) -> OcrResult:
    if table_format not in ("markdown", "html"):
        raise ToolFailure("invalid_arguments", "table_format must be 'markdown' or 'html'.")
    p = resolve_input(input_path)
    if p.is_dir():
        raise ToolFailure("not_a_file", "ocr_document takes one PDF or image; call it once per file.")
    rendered = await asyncio.to_thread(_render_inputs, p, pages)

    async def one(n: int, img: bytes):
        try:
            return n, await client.ocr(img, "image/jpeg", table_format), None
        except ToolFailure as e:  # includes BudgetExceeded: keep the pages that did succeed
            return n, None, e

    results = await asyncio.gather(*[one(n, img) for n, img in rendered])
    infos: list[PageInfo] = []
    page_md: list[str] = []
    store = []
    for n, r, err in results:
        if err or r is None:
            infos.append(PageInfo(page=n, chars=0, blocks=0, low_confidence_blocks=0, empty=True, failed=True,
                                  error=err.message if err else "failed"))
            store.append({"page": n, "markdown": "", "blocks": [], "failed": True})
            continue
        md = blocks_to_markdown(r["blocks"], r["markdown"])
        low = sum(1 for b in r["blocks"] if (b.get("conf") or 1) < 0.5 and (b.get("text") or "").strip())
        infos.append(PageInfo(page=n, chars=len(md), blocks=len(r["blocks"]), low_confidence_blocks=low,
                              empty=not md.strip()))
        if md.strip():
            page_md.append(md)
        store.append({"page": n, "markdown": md, "blocks": r["blocks"]})
    budget_hit = [e for _, r, e in results if e is not None and e.code == "budget_exceeded"]
    if all(i.failed for i in infos):
        if budget_hit:
            raise budget_hit[0]
        raise ToolFailure("ocr_failed", f"Indic-OCR failed on every page: {infos[0].error}", retryable=True)
    full = "\n\n".join(page_md)
    md_path = resolve_output(output_path, "ocr", ".md", f"{p}|{pages}|{table_format}")
    pages_path = md_path.with_suffix(".pages.json")
    md_path.write_text(full, encoding="utf-8")
    pages_path.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
    review = [i.page for i in infos if i.failed or i.empty or (i.blocks and i.low_confidence_blocks / i.blocks >= 0.3)]
    g = guess_language(full[:20000]) if full else {"script": None, "language": None}
    warnings = []
    if budget_hit:
        warnings.append("The budget ran out: some pages were NOT processed (see needs_review). Do not claim the "
                        "whole document was read; finish as 'partial'.")
    if review:
        warnings.append(f"Pages needing review (empty, failed or low confidence): {review}")
    return OcrResult(markdown_path=str(md_path), pages_path=str(pages_path), page_count=len(infos), pages=infos,
                     total_chars=len(full), script=g["script"], language_guess=g["language"],
                     needs_review=review, preview=_preview(full, 400), warnings=warnings)


# ================================================================== speech synthesis
def _clean_for_speech(t: str) -> str:
    t = re.sub(r"```.*?```", " ", t, flags=re.S)
    t = re.sub(r"^\s*#{1,6}\s*", "", t, flags=re.M)
    t = re.sub(r"[*_`]{1,3}", "", t)
    t = re.sub(r"^\s*[-*+]\s+", "", t, flags=re.M)
    t = re.sub(r"\|", " ", t)
    return re.sub(r"[ \t]+", " ", t).strip()


async def _speak_chunk(c: str, voice: str, lang: str, style: str | None, stats: dict, depth: int = 0) -> bytes:
    r = await client.speak(c, voice, lang, style)
    dur = client.wav_duration(r["wav"])
    cps = len(c) / dur if dur > 0 else 999.0
    suspicious = len(c) >= 40 and (cps > 30 or cps < 3)  # far too fast = omitted words; far too slow = looping/silence
    if suspicious and depth < 1:
        halves = split_in_half(c)
        if len(halves) == 2:
            stats["recovered"] += 1
            stats["warnings"].append(f"Chunk looked wrong ({cps:.0f} chars/s); re-synthesised in two halves.")
            parts = [await _speak_chunk(h, voice, lang, style, stats, depth + 1) for h in halves]
            return audio.concat_wavs(parts, gap_ms=120)
    if suspicious:
        stats["warnings"].append(f"Chunk still looks odd ({cps:.0f} chars/s): '{c[:40]}…'")
    return r["wav"]


async def synthesize_speech(language: str, text: str | None = None, input_path: str | None = None,
                            voice: str | None = None, gender: str | None = None, style: str | None = None,
                            output_path: str | None = None, loudness_normalize: bool = True) -> SpeechResult:
    s = get_settings()
    lang = normalize_lang(language, "speak")
    v, g, assumed = pick_voice(lang, voice, gender)
    if style:
        match = {x.lower(): x for x in STYLES}.get(style.strip().lower())
        if not match:
            raise ToolFailure("invalid_style", f"Unknown style '{style}'.", hint=f"Use one of: {STYLES}")
        style = match
    raw = _need_one(text, input_path, "text")
    spoken = _clean_for_speech(raw)
    if not re.search(r"\w", spoken):
        raise ToolFailure("empty_input", "There is no speakable text.")
    chunks = chunk_for_tts(spoken, s.tts_max_chars)
    tracker.precheck(len(spoken) / 10000.0 * 6.0)  # fail before spending anything if the whole job won't fit
    ext = ".mp3"
    if output_path and Path(output_path).suffix.lower() in (".wav", ".mp3", ".ogg", ".flac"):
        ext = Path(output_path).suffix.lower()
    out = resolve_output(output_path, f"speech.{lang}", ext, f"{spoken}|{v}|{lang}|{style}")
    stats = {"recovered": 0, "warnings": []}
    wavs = await asyncio.gather(*[_speak_chunk(c, v, lang, style, stats) for c in chunks])
    joined = audio.concat_wavs(list(wavs))
    await asyncio.to_thread(audio.encode, joined, out, loudness_normalize,
                            "AI-generated speech (Bodhan Indic-Speak). Not a human recording.")
    spoken_path = resolve_output(None, f"spoken.{lang}", ".txt", str(out))     # sidecar: kept in intermediate/
    spoken_path.write_text(spoken, encoding="utf-8")
    return SpeechResult(audio_path=str(out), spoken_text_path=str(spoken_path), language=lang, voice=v, gender=g,
                        voice_assumed=assumed, duration_s=round(client.wav_duration(joined), 2), chunks=len(chunks),
                        characters=len(spoken), recovered_chunks=stats["recovered"], warnings=stats["warnings"])


async def verify_speech(audio_path: str, language: str, expected_text: str | None = None,
                        expected_path: str | None = None) -> VerifySpeechResult:
    s = get_settings()
    lang = normalize_lang(language, "transcribe")
    expected = _need_one(expected_text, expected_path, "expected_text" if expected_text else "expected")
    p = resolve_input(audio_path)
    segs, _, dur, _ = await _asr_file(p, lang)
    heard = " ".join(x["text"] for x in segs if x["text"])
    v = verify.speech_verdict(expected, heard, s.cer_threshold, dur)
    return VerifySpeechResult(ok=v["ok"], cer=v["cer"], length_ratio=v["length_ratio"], repetitions=v["repetitions"],
                              omissions=v["omissions"], issues=v["issues"], heard_preview=_preview(heard, 200))
