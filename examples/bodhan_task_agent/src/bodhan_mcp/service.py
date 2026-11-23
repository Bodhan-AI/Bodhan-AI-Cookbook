"""Tool logic, independent of transport.

Each public coroutine is one MCP tool. Model limits (30 s audio windows, short TTS
inputs, the translation context, multi-page documents) are handled here so that
callers never have to. Every tool that produces language output also verifies it.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

from . import audio, documents, errors, languages, pricing, text
from .client import BodhanClient, Ledger
from .config import Settings
from .models import (
    Check,
    FileInfo,
    InspectResult,
    LowConfidenceBlock,
    ModelCall,
    OCRResult,
    PageSummary,
    PieceCheck,
    SpeechResult,
    SpeechVerification,
    SubtitleResult,
    TranscribeResult,
    TranscriptSegment,
    TranslateResult,
    Verification,
    VoicesResult,
)
from .paths import output_path, resolve_input, safe_stem, short_hash

log = logging.getLogger("bodhan_mcp.service")

INLINE_TEXT_LIMIT = 4000
PREVIEW_CHARS = 500
TEXT_EXTS = {".txt", ".md", ".markdown", ".csv", ".tsv", ".srt", ".vtt", ".json", ".html", ".htm", ".rst", ".log"}
MAX_TEXT_CHARS = 400_000
MAX_TTS_CHARS = 30_000
TTS_PIECE_CHARS = 220
TRANSLATE_CHUNK_TOKENS = 1500
OCR_LOW_CONF = 0.6

T = TypeVar("T")


def _calls(ledger: Ledger) -> list[ModelCall]:
    return [ModelCall(**r.to_dict()) for r in ledger.records]


def _inline(s: str) -> tuple[str | None, str]:
    return (s if len(s) <= INLINE_TEXT_LIMIT else None), s[:PREVIEW_CHARS]


def _read_text_file(p: Path) -> str:
    try:
        content = p.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise errors.BodhanError(
            errors.UNSUPPORTED_MEDIA, f"{p.name} is not UTF-8 text.",
            hint="For scanned documents or images use ocr_document; for audio use transcribe_audio.",
        ) from exc
    if len(content) > MAX_TEXT_CHARS:
        raise errors.BodhanError(errors.INVALID_INPUT, f"{p.name} has {len(content)} characters (limit {MAX_TEXT_CHARS}).",
                                 hint="Split the document or pass a smaller excerpt.")
    return content


def _exactly_one(text_value: str | None, file_value: str | None) -> None:
    if (text_value is None or text_value == "") == (file_value is None or file_value == ""):
        raise errors.BodhanError(errors.INVALID_INPUT, "Pass exactly one of `text` or `text_file`.",
                                 hint="Use text_file for anything long or produced by another tool.")


async def _gather_partial[T](coros: list[Awaitable[T]]) -> list[T | errors.BodhanError]:
    """Run concurrently; anticipated failures come back as values, budget errors propagate after all settle."""
    results = await asyncio.gather(*coros, return_exceptions=True)
    out: list[T | errors.BodhanError] = []
    budget: errors.BodhanError | None = None
    for r in results:
        if isinstance(r, errors.BodhanError):
            if r.code in (errors.BUDGET_EXCEEDED, errors.MISSING_API_KEY, errors.AUTH_FAILED):
                budget = budget or r
            out.append(r)
        elif isinstance(r, BaseException):
            raise r
        else:
            out.append(r)
    if budget:
        raise budget
    return out


def _upfront_budget(ledger: Ledger, estimate: float, what: str) -> None:
    """Refuse the whole job before any request if it cannot fit, rather than failing halfway."""
    if ledger.max_cost_inr is not None and ledger.spent + estimate > ledger.max_cost_inr + 1e-9:
        raise errors.BodhanError(
            errors.BUDGET_EXCEEDED,
            f"{what} is estimated at ₹{estimate:.4f}, more than the ₹{ledger.max_cost_inr - ledger.spent:.4f} budget left.",
            hint="Raise max_cost_inr, shorten the input (e.g. summarise first), or report partial results.",
            details={"estimate_inr": round(estimate, 6)},
        )


class BodhanService:
    def __init__(self, settings: Settings, client: BodhanClient | None = None) -> None:
        self.settings = settings
        self.client = client or BodhanClient(settings)

    async def aclose(self) -> None:
        await self.client.aclose()

    def _out_dir(self, output_dir: str | None) -> Path:
        return Path(output_dir).expanduser() if output_dir else self.settings.workdir

    def _input(self, raw: str, what: str = "file") -> Path:
        return resolve_input(raw, self.settings.allowed_dirs, what=what)

    # ---------------------------------------------------------------------------------
    # inspect_media
    # ---------------------------------------------------------------------------------

    async def inspect_media(self, paths: list[str]) -> InspectResult:
        if not paths:
            raise errors.BodhanError(errors.INVALID_INPUT, "Pass at least one path.")
        files: list[Path] = []
        infos: list[FileInfo] = []
        for raw in paths[:200]:
            p = Path(str(raw).replace("file://", "", 1)).expanduser()
            if not p.exists():
                infos.append(FileInfo(path=str(p), name=p.name, kind="missing", error="file not found"))
                continue
            if p.is_dir():
                files.extend(sorted(c for c in p.rglob("*") if c.is_file() and not c.name.startswith("."))[:200])
            else:
                files.append(p)
        for p in files[:200]:
            try:
                resolved = self._input(str(p))
                infos.append(await asyncio.to_thread(self._inspect_one, resolved))
            except errors.BodhanError as exc:
                infos.append(FileInfo(path=str(p), name=p.name, kind="other", error=str(exc)))
        return InspectResult(files=infos, ffmpeg_available=audio.have_ffmpeg())

    def _inspect_one(self, p: Path) -> FileInfo:
        ext = p.suffix.lower()
        base = {"path": str(p), "name": p.name, "size_bytes": p.stat().st_size}
        if ext in documents.PDF_EXTS:
            return FileInfo(kind="pdf", pages=documents.pdf_page_count(p), **base)
        if ext in documents.IMAGE_EXTS:
            info = documents.image_info(p)
            return FileInfo(kind="image", width=info["width"], height=info["height"], **base)
        if ext in audio.AUDIO_EXTS | audio.VIDEO_EXTS:
            meta = audio.probe(p)
            kind = "video" if meta.get("has_video") else "audio"
            err = None if meta.get("has_audio") else "no audio track"
            return FileInfo(kind=kind, duration_s=meta.get("duration_s"), has_audio=meta.get("has_audio"), error=err, **base)
        if ext in TEXT_EXTS or base["size_bytes"] < 2_000_000:
            try:
                content = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                return FileInfo(kind="other", error="unrecognised binary file", **base)
            return FileInfo(
                kind="text", chars=len(content), detected_script=languages.detect_script(content),
                language_candidates=languages.language_candidates(content)[:4], preview=content[:300], **base,
            )
        return FileInfo(kind="other", error="unrecognised file type", **base)

    # ---------------------------------------------------------------------------------
    # transcribe_audio
    # ---------------------------------------------------------------------------------

    async def transcribe_audio(self, path: str, language: str | None, *, output_dir: str | None, ledger: Ledger) -> TranscribeResult:
        p = self._input(path, "audio file")
        lang = languages.require_language(language, "transcribe") if language else None
        meta = await asyncio.to_thread(audio.probe, p)
        if not meta.get("has_audio"):
            raise errors.BodhanError(errors.UNSUPPORTED_MEDIA, f"{p.name} has no audio track.",
                                     hint="Pass an audio or video file that contains speech.")
        pcm = await asyncio.to_thread(audio.decode_to_pcm, p, audio.TRANSCRIBE_SR)
        sr = audio.TRANSCRIBE_SR
        segs = audio.segment_speech(pcm, sr)
        duration = round(pcm.size / sr, 3)
        speech_s = round(sum(s.duration for s in segs), 3)
        _upfront_budget(ledger, pricing.transcribe_cost(speech_s), f"Transcribing {speech_s:.0f}s of speech")

        async def one(i: int, seg: audio.Segment) -> str:
            wav = audio.pcm_to_wav_bytes(audio.slice_pcm(pcm, sr, seg), sr)
            r = await self.client.transcribe(wav, duration_s=seg.duration, language=lang, ledger=ledger,
                                             filename=f"{safe_stem(p.stem)}-{i:03d}.wav")
            return r["text"]

        results = await _gather_partial([one(i, s) for i, s in enumerate(segs)])
        warnings: list[str] = []
        segments: list[TranscriptSegment] = []
        failures = 0
        for i, (seg, r) in enumerate(zip(segs, results, strict=True)):
            if isinstance(r, errors.BodhanError):
                failures += 1
                warnings.append(f"segment {i} ({seg.start:.1f}-{seg.end:.1f}s) failed: {r.code}: {r.message}")
                continue
            if text.repeated_ngrams(r):
                warnings.append(f"segment {i} ({seg.start:.1f}-{seg.end:.1f}s) contains repeated phrases; check it")
            segments.append(TranscriptSegment(index=i, start=seg.start, end=seg.end, text=r))
        if segs and failures == len(segs):
            first = next(r for r in results if isinstance(r, errors.BodhanError))
            raise first
        if not segs:
            warnings.append("no speech detected; nothing was sent to the model")
        full = " ".join(s.text for s in segments if s.text).strip()
        if segs and not full:
            warnings.append("the model returned no text for any speech segment")
        detected = languages.detect_script(full)
        share = languages.script_profile(full).get(detected, 0.0) if detected else None
        if lang and detected and full and detected != languages.expected_script(lang):
            warnings.append(
                f"language hint '{lang}' expects {languages.expected_script(lang)} script but the transcript is mostly "
                f"{detected}; the audio may be in another language — re-run without `language` to auto-detect"
            )

        out_dir = self._out_dir(output_dir)
        stem = safe_stem(p.stem)
        tag = lang or "auto"
        transcript_file = output_path(out_dir, f"{stem}.{tag}.transcript.txt")
        segments_file = output_path(out_dir, f"{stem}.{tag}.segments.json")
        transcript_file.write_text(full + "\n", encoding="utf-8")
        segments_file.write_text(json.dumps({
            "source_file": str(p), "language_hint": lang, "duration_s": duration,
            "segments": [s.model_dump() for s in segments],
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        inline, preview = _inline(full)
        return TranscribeResult(
            transcript_file=str(transcript_file), segments_file=str(segments_file), text=inline, preview=preview,
            language_hint=lang, detected_script=detected, script_share=share,
            language_candidates=languages.language_candidates(full)[:4], duration_s=duration, speech_s=speech_s,
            segments=len(segments), warnings=warnings, cost_inr=ledger.spent, calls=_calls(ledger),
        )

    # ---------------------------------------------------------------------------------
    # translate_text
    # ---------------------------------------------------------------------------------

    @staticmethod
    def _check_translation(src: str, out: str, truncated: bool, target: str, script: str) -> list[Check]:
        checks = [
            Check(name="not_truncated", passed=not truncated, detail="model output hit its token limit" if truncated else ""),
            Check(name="non_empty", passed=bool(out.strip())),
        ]
        if not out.strip():
            return checks
        if len(src) >= 40:
            ratio = len(out) / max(len(src), 1)
            checks.append(Check(name="length_ratio", passed=0.3 <= ratio <= 4.0,
                                detail=f"output/input characters = {ratio:.2f} (expected 0.3–4.0; outside suggests omission or addition)"))
        if script != "codemix":
            want = languages.expected_script(target, script)
            frac = languages.script_profile(out).get(want, 0.0)
            checks.append(Check(name="target_script", passed=frac >= 0.4,
                                detail=f"{frac:.0%} of letters are {want} (need ≥40%)"))
        if len(src) > 20 and text.normalize_for_compare(src) == text.normalize_for_compare(out) and target != "en":
            checks.append(Check(name="actually_translated", passed=False, detail="output equals input"))
        missing = text.numbers(src) - text.numbers(out)
        checks.append(Check(name="numbers_preserved", passed=not missing, severity="warning",
                            detail=f"missing numbers: {sorted(missing)}" if missing else "all numbers present"))
        src_tables, out_tables = text.table_shapes(src), text.table_shapes(out)
        if src_tables:
            checks.append(Check(name="tables_preserved", passed=src_tables == out_tables,
                                detail=f"source tables {src_tables}, output tables {out_tables}"))
        hs, ho = text.heading_count(src), text.heading_count(out)
        if hs:
            checks.append(Check(name="headings_preserved", passed=hs == ho, severity="warning", detail=f"{hs} → {ho} headings"))
        return checks

    @staticmethod
    def _halves(chunk: str) -> list[str] | None:
        blocks = text.markdown_blocks(chunk)
        if len(blocks) > 1:
            mid = len(blocks) // 2
            return ["\n\n".join(blocks[:mid]), "\n\n".join(blocks[mid:])]
        sents = text.split_sentences(chunk)
        if len(sents) > 1:
            mid = len(sents) // 2
            return [" ".join(sents[:mid]), " ".join(sents[mid:])]
        return None

    async def _translate_chunk(self, chunk: str, target: str, script: str, ledger: Ledger, depth: int = 0) -> tuple[str, list[Check]]:
        r = await self.client.translate(chunk, target_language=target, script=script, ledger=ledger)
        checks = self._check_translation(chunk, r["translation"], r["truncated"], target, script)
        failed = [c for c in checks if not c.passed and c.severity == "error" and c.name != "tables_preserved"]
        if failed and depth == 0 and (halves := self._halves(chunk)):
            # Recovery: re-send as two smaller requests (a different input, so not a duplicate call).
            log.info(json.dumps({"event": "translate_retry_split", "failed": [c.name for c in failed]}))
            parts = await asyncio.gather(*(self._translate_chunk(h, target, script, ledger, 1) for h in halves))
            joiner = "\n\n" if "\n\n" in chunk else " "
            merged = joiner.join(p[0] for p in parts)
            new_checks = self._check_translation(chunk, merged, False, target, script)
            new_checks.append(Check(name="recovered_by_split", passed=True, severity="warning",
                                    detail=f"first attempt failed {[c.name for c in failed]}; re-sent as 2 parts"))
            return merged, new_checks
        return r["translation"], checks

    async def _translate_table(self, table: str, target: str, script: str, ledger: Ledger) -> tuple[str, list[Check]]:
        """Translate a pipe table whole; if the model changes its shape, rebuild it cell by cell.

        The whole-table attempt keeps context between cells (usually better wording). The cell-wise
        fallback guarantees the structure by construction: the table skeleton is never sent to the
        model, only the text inside cells. Numeric/blank cells are copied through unchanged.
        """
        r = await self.client.translate(table, target_language=target, script=script, ledger=ledger)
        out = (r["translation"] or "").strip()
        checks = self._check_translation(table, out, r["truncated"], target, script)
        hard_fail = [c.name for c in checks if not c.passed and c.severity == "error"]
        if text.table_structure(table) == text.table_structure(out) and not hard_fail:
            return out, checks
        rows = text.parse_pipe_table(table)
        cells = list(dict.fromkeys(c for row in rows if row for c in row if text.needs_translation(c)))
        log.info(json.dumps({"event": "table_cellwise_fallback", "cells": len(cells), "failed": hard_fail or ["shape"]}))
        results = await asyncio.gather(*(
            self.client.translate(c, target_language=target, script=script, ledger=ledger) for c in cells
        ))
        mapping = {c: " ".join((res["translation"] or "").split()).replace("|", "/") for c, res in zip(cells, results, strict=True)}
        rebuilt = text.render_pipe_table([None if row is None else [mapping.get(c, c) for c in row] for row in rows])
        checks = self._check_translation(table, rebuilt, any(res["truncated"] for res in results), target, script)
        checks.append(Check(name="table_rebuilt_cellwise", passed=True, severity="warning",
                            detail=f"whole-table translation changed the table's shape; re-translated {len(cells)} cells "
                                   "individually and rebuilt the original structure"))
        return rebuilt, checks

    @staticmethod
    def _merge_checks(per_chunk: list[list[Check]]) -> Verification:
        by_name: dict[str, Check] = {}
        for idx, checks in enumerate(per_chunk):
            for c in checks:
                prev = by_name.get(c.name)
                if prev is None:
                    by_name[c.name] = c.model_copy()
                    if not c.passed:
                        by_name[c.name].detail = f"chunk {idx}: {c.detail}"
                elif not c.passed:
                    note = f"chunk {idx}: {c.detail}"
                    by_name[c.name] = prev.model_copy(update={
                        "passed": False,
                        "detail": note if prev.passed else f"{prev.detail}; {note}"[:600],
                    })
        checks = list(by_name.values())
        return Verification(passed=all(c.passed or c.severity == "warning" for c in checks), checks=checks)

    async def translate_text(self, target_language: str, text_value: str | None, text_file: str | None, script: str, *,
                             output_dir: str | None, ledger: Ledger) -> TranslateResult:
        _exactly_one(text_value, text_file)
        target = languages.require_language(target_language, "translate")
        if script not in {"native", "roman", "codemix"}:
            raise errors.BodhanError(errors.INVALID_INPUT, f"Unknown script {script!r}.", hint="Use native, roman or codemix.")
        out_dir = self._out_dir(output_dir)

        if text_file:
            p = self._input(text_file, "text file")
            content = _read_text_file(p)
            stem, ext = safe_stem(p.stem.replace(".segments", "")), (p.suffix if p.suffix in {".md", ".markdown"} else ".txt")
            if p.name.endswith(".segments.json") or (p.suffix == ".json" and '"segments"' in content[:2000]):
                return await self._translate_segments(p, content, target, script, out_dir, ledger)
        else:
            content = text_value or ""
            stem, ext = f"text-{short_hash(content)}", ".md" if text.markdown_blocks(content) and "|" in content else ".txt"
        if not content.strip():
            raise errors.BodhanError(errors.INVALID_INPUT, "The text to translate is empty.")

        source_script = languages.detect_script(content)
        out_file = output_path(out_dir, f"{stem}.{target}{ext}")
        if target == "en" and source_script == "Latin" and languages.indic_fraction(content) == 0 and script == "native":
            out_file.write_text(content, encoding="utf-8")
            inline, preview = _inline(content)
            return TranslateResult(
                output_file=str(out_file), text=inline, preview=preview, target_language=target, script=script,
                source_chars=len(content), chunks=0, source_script=source_script,
                verification=Verification(passed=True, checks=[Check(name="already_in_target_language", passed=True,
                                                                     severity="warning", detail="Latin-script input; no call made")]),
                cost_inr=0.0, calls=[],
            )
        # Pipe tables are translated as units (and rebuilt cell by cell if the model breaks them);
        # prose is chunked at paragraph boundaries. Order is preserved on reassembly.
        layout: list[tuple[str, list[str]]] = []
        for kind, block in text.split_tables(content):
            layout.append((kind, [block] if kind == "table" else text.chunk_for_translation(block, TRANSLATE_CHUNK_TOKENS)))
        n_chunks = sum(len(chunks) for _, chunks in layout)
        est = sum(pricing.translate_cost(pricing.estimate_translation_output_tokens(c, target))
                  for _, chunks in layout for c in chunks)
        # Cell-wise table fallback can re-send a table once; budget for it up front.
        est += sum(pricing.translate_cost(pricing.estimate_translation_output_tokens(c[0], target))
                   for kind, c in layout if kind == "table")
        _upfront_budget(ledger, est, f"Translating {len(content)} characters")

        async def part(kind: str, chunks: list[str]) -> tuple[str, list[list[Check]]]:
            if kind == "table":
                out, checks = await self._translate_table(chunks[0], target, script, ledger)
                return out, [checks]
            res = await asyncio.gather(*(self._translate_chunk(c, target, script, ledger) for c in chunks))
            return "\n\n".join(r[0] for r in res), [r[1] for r in res]

        parts = await asyncio.gather(*(part(kind, chunks) for kind, chunks in layout))
        translation = "\n\n".join(p[0] for p in parts)
        verification = self._merge_checks([c for p in parts for c in p[1]])
        chunks = [c for _, cs in layout for c in cs]
        assert len(chunks) == n_chunks
        # Whole-document structure check, in addition to per-chunk checks.
        if text.table_shapes(content):
            whole = text.table_shapes(content) == text.table_shapes(translation)
            verification.checks.append(Check(name="document_tables_preserved", passed=whole,
                                             detail=f"{len(text.table_shapes(content))} table(s) in source"))
            verification.passed = verification.passed and whole
        out_file.write_text(translation + "\n", encoding="utf-8")
        inline, preview = _inline(translation)
        return TranslateResult(
            output_file=str(out_file), text=inline, preview=preview, target_language=target, script=script,
            source_chars=len(content), chunks=len(chunks), source_script=source_script, verification=verification,
            cost_inr=ledger.spent, calls=_calls(ledger),
        )

    async def _translate_segments(self, p: Path, content: str, target: str, script: str, out_dir: Path,
                                  ledger: Ledger) -> TranslateResult:
        try:
            data = json.loads(content)
            segments = [TranscriptSegment(**s) for s in data["segments"]]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise errors.BodhanError(errors.INVALID_INPUT, f"{p.name} is not a segments file from transcribe_audio: {exc}") from exc
        todo = [s for s in segments if s.text.strip()]
        est = sum(pricing.translate_cost(pricing.estimate_translation_output_tokens(s.text, target)) for s in todo)
        _upfront_budget(ledger, est, f"Translating {len(todo)} segments")
        results = await asyncio.gather(*(self._translate_chunk(s.text, target, script, ledger) for s in todo))
        for seg, (tr, _) in zip(todo, results, strict=True):
            seg.translation = tr
        verification = self._merge_checks([r[1] for r in results]) if results else Verification(passed=True, checks=[])
        stem = safe_stem(p.name.removesuffix(".segments.json").removesuffix(".json"))
        seg_file = output_path(out_dir, f"{stem}.{target}.segments.json")
        txt_file = output_path(out_dir, f"{stem}.{target}.txt")
        data["segments"] = [s.model_dump() for s in segments]
        data["target_language"] = target
        seg_file.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        joined = " ".join(s.translation or "" for s in segments).strip()
        txt_file.write_text(joined + "\n", encoding="utf-8")
        inline, preview = _inline(joined)
        src_text = " ".join(s.text for s in segments)
        return TranslateResult(
            output_file=str(txt_file), text=inline, preview=preview, target_language=target, script=script,
            source_chars=len(src_text), chunks=len(todo), source_script=languages.detect_script(src_text),
            verification=verification, segments_file=str(seg_file), cost_inr=ledger.spent, calls=_calls(ledger),
        )

    # ---------------------------------------------------------------------------------
    # synthesize_speech
    # ---------------------------------------------------------------------------------

    @staticmethod
    def _classify_piece(ref: str, hyp: str, digit_ratio: float) -> tuple[float, str]:
        c = text.cer(ref, hyp)
        r, h = text.normalize_for_compare(ref).replace(" ", ""), text.normalize_for_compare(hyp).replace(" ", "")
        ratio = len(h) / max(len(r), 1)
        new_repeats = set(text.repeated_ngrams(hyp, 3, 2)) - set(text.repeated_ngrams(ref, 3, 2))
        # Numbers are spoken as words, so the transcript legitimately diverges from digit-bearing text.
        threshold = 0.35 + min(0.3, 3 * digit_ratio)
        if ratio > 1.5 + digit_ratio * 3 or (new_repeats and ratio > 1.15):
            return c, "repetition"
        if ratio < 0.6:
            return c, "omission"
        if c > threshold:
            return c, "mismatch"
        return c, "ok"

    async def _speak_and_check(self, piece: str, *, voice: str, lang: str, style: str | None, verify: bool,
                               ledger: Ledger) -> tuple[bytes, float | None, str]:
        wav = await self.client.speak(piece, voice=voice, lang=lang, style=style, ledger=ledger)
        if not verify:
            return wav, None, "unverified"
        x, sr = audio.wav_bytes_to_pcm(wav)
        r = await self.client.transcribe(wav, duration_s=x.size / sr, language=lang, ledger=ledger, filename="tts.wav")
        digits = sum(ch.isdigit() for ch in text.ascii_digits(piece)) / max(len(piece), 1)
        c, status = self._classify_piece(piece, r["text"], digits)
        return wav, c, status

    async def synthesize_speech(self, language: str, text_value: str | None, text_file: str | None, voice: str | None,
                                gender: str | None, style: str | None, output_format: str, output_name: str | None,
                                verify: bool, *, output_dir: str | None, ledger: Ledger) -> SpeechResult:
        _exactly_one(text_value, text_file)
        lang = languages.require_language(language, "speak")
        speech_warnings: list[str] = []
        voice_lang = lang
        if lang not in languages.voices():
            # Indic-Speak lists the language (e.g. English) but the library has no voice for it:
            # use a listed voice of the requested gender rather than failing, and say so.
            voice_lang = "hi"
            speech_warnings.append(f"the voice library lists no {languages.name_of(lang)} voice; used a Hindi-library "
                                   f"voice speaking {languages.name_of(lang)}")
        voice_name = languages.validate_voice(voice_lang, voice) if voice else languages.default_voice(voice_lang, gender)
        if style:
            match = next((s for s in languages.styles() if s.lower() == style.strip().lower()), None)
            if not match:
                raise errors.BodhanError(errors.INVALID_INPUT, f"Unknown style {style!r}.",
                                         hint=f"Styles: {', '.join(languages.styles())}.")
            style = match
        fmt = output_format.lower().lstrip(".")
        if fmt not in {"mp3", "wav"}:
            raise errors.BodhanError(errors.INVALID_INPUT, f"Unsupported output_format {output_format!r}.", hint="Use mp3 or wav.")

        if text_file:
            p = self._input(text_file, "text file")
            raw = _read_text_file(p)
            stem = safe_stem(p.stem)
        else:
            raw = text_value or ""
            stem = f"speech-{short_hash(raw)}"
        spoken = text.markdown_to_speech(raw) if (text_file and Path(text_file).suffix in {".md", ".markdown"}) or "|" in raw or "#" in raw else raw.strip()
        if not spoken:
            raise errors.BodhanError(errors.INVALID_INPUT, "There is no text to speak.")
        if len(spoken) > MAX_TTS_CHARS:
            raise errors.BodhanError(
                errors.INVALID_INPUT, f"Text has {len(spoken)} characters; the limit per call is {MAX_TTS_CHARS}.",
                hint="Summarise or split the text first; speech costs ₹6 per 10,000 characters.",
            )
        want = languages.expected_script(lang)
        frac = languages.script_profile(spoken).get(want, 0.0)
        if frac < 0.5:
            got = languages.detect_script(spoken)
            raise errors.BodhanError(
                errors.INVALID_INPUT,
                f"The text is mostly {got} script, but {languages.name_of(lang)} speech needs {want} text ({frac:.0%} found).",
                hint=f"Translate it to '{lang}' with translate_text first, then pass the translation's output_file as text_file.",
            )

        pieces = text.split_for_tts(spoken, TTS_PIECE_CHARS)
        est = pricing.speak_cost(len(spoken)) + (pricing.transcribe_cost(len(spoken) / 10) if verify else 0)
        _upfront_budget(ledger, est, f"Speaking {len(spoken)} characters")

        results = await _gather_partial([
            self._speak_and_check(pc, voice=voice_name, lang=lang, style=style, verify=verify, ledger=ledger) for pc in pieces
        ])
        if failed := [r for r in results if isinstance(r, errors.BodhanError)]:
            raise failed[0]
        wavs: list[bytes] = []
        piece_checks: list[PieceCheck] = []
        for i, (pc, (wav, c, status)) in enumerate(zip(pieces, results, strict=True)):  # type: ignore[misc]
            retried = False
            if status not in {"ok", "unverified"}:
                # Recovery for Indic-Speak repeating/omitting: re-synthesise as smaller sub-pieces (new inputs).
                subs = text.split_for_tts(pc, max(40, len(pc) // 2))
                if len(subs) > 1:
                    retried = True
                    try:
                        sub_res = await asyncio.gather(*(
                            self._speak_and_check(s, voice=voice_name, lang=lang, style=style, verify=True, ledger=ledger)
                            for s in subs
                        ))
                        sub_cers = [r[1] or 0.0 for r in sub_res]
                        sub_status = [r[2] for r in sub_res]
                        weighted = sum(cv * len(s) for cv, s in zip(sub_cers, subs, strict=True)) / max(len(pc), 1)
                        if all(s == "ok" for s in sub_status) or (c is not None and weighted < c):
                            pcm_parts = [audio.wav_bytes_to_pcm(r[0])[0] for r in sub_res]
                            sr = audio.wav_bytes_to_pcm(sub_res[0][0])[1]
                            wav = audio.pcm_to_wav_bytes(audio.concat(pcm_parts, sr, 0.1), sr)
                            c = round(weighted, 4)
                            status = "ok" if all(s == "ok" for s in sub_status) else next(s for s in sub_status if s != "ok")
                    except errors.BodhanError as exc:
                        if exc.code == errors.BUDGET_EXCEEDED:
                            raise
                        log.warning(json.dumps({"event": "tts_retry_failed", "piece": i, "error": exc.code}))
            wavs.append(wav)
            piece_checks.append(PieceCheck(index=i, chars=len(pc), cer=c, status=status, retried=retried))  # type: ignore[arg-type]

        decoded = [audio.wav_bytes_to_pcm(w) for w in wavs]
        sr = decoded[0][1]
        pcm = audio.concat([audio._resample(x, s, sr) for x, s in decoded], sr)
        name = output_name or f"{stem}.{lang}.{voice_name.lower()}.{fmt}"
        if not name.lower().endswith(f".{fmt}"):
            name = f"{Path(name).stem}.{fmt}"
        out = output_path(self._out_dir(output_dir), name)
        await asyncio.to_thread(audio.encode, pcm, sr, out, title=f"{languages.name_of(lang)} speech ({voice_name}, AI-generated)")
        st = audio.stats(pcm, sr)

        verification = None
        if verify:
            cers = [pc.cer for pc in piece_checks if pc.cer is not None]
            sane = st["clipping_ratio"] < 0.01 and st["silence_ratio"] < 0.7
            verification = SpeechVerification(
                passed=sane and all(pc.status == "ok" for pc in piece_checks),
                mean_cer=round(sum(cers) / len(cers), 4) if cers else None,
                max_cer=max(cers) if cers else None,
                pieces=piece_checks,
            )
        return SpeechResult(
            audio_file=str(out), format=fmt, duration_s=st["duration_s"], language=lang, voice=voice_name, style=style,
            chars=len(spoken), pieces=len(pieces), verification=verification, audio_stats=st, label=audio.AI_LABEL,
            warnings=speech_warnings,
            cost_inr=ledger.spent, calls=_calls(ledger),
        )

    # ---------------------------------------------------------------------------------
    # ocr_document
    # ---------------------------------------------------------------------------------

    async def ocr_document(self, path: str, pages: str | None, table_format: str, *, output_dir: str | None,
                           ledger: Ledger) -> OCRResult:
        p = self._input(path, "document")
        if table_format not in {"markdown", "html"}:
            raise errors.BodhanError(errors.INVALID_INPUT, f"Unknown table_format {table_format!r}.", hint="Use markdown or html.")
        ext = p.suffix.lower()
        if ext in documents.PDF_EXTS:
            count = await asyncio.to_thread(documents.pdf_page_count, p)
            selected = documents.parse_page_spec(pages, count)
            if len(selected) > documents.MAX_PAGES:
                raise errors.BodhanError(errors.INVALID_INPUT, f"{len(selected)} pages selected; limit is {documents.MAX_PAGES} per call.",
                                         hint="Pass a page range such as pages='1-30' and call again for the rest.")
            _upfront_budget(ledger, pricing.ocr_cost(len(selected)), f"OCR of {len(selected)} pages")
            rendered = await asyncio.to_thread(documents.render_pdf_pages, p, selected)
        elif ext in documents.IMAGE_EXTS:
            count, selected = 1, [1]
            _upfront_budget(ledger, pricing.ocr_cost(1), "OCR of one image")
            data, mime = await asyncio.to_thread(documents.load_image_page, p)
            rendered = [(1, data, mime)]
        else:
            hint = "Use transcribe_audio for audio/video." if ext in audio.AUDIO_EXTS | audio.VIDEO_EXTS else "Pass a PDF or an image."
            raise errors.BodhanError(errors.UNSUPPORTED_MEDIA, f"{p.name} is not a PDF or image.", hint=hint)

        results = await _gather_partial([
            self.client.ocr(data, mime=mime, table_format=table_format, ledger=ledger) for _, data, mime in rendered
        ])
        warnings: list[str] = []
        parts: list[str] = []
        per_page: list[PageSummary] = []
        low: list[LowConfidenceBlock] = []
        failures = 0
        for (page_no, _, _), r in zip(rendered, results, strict=True):
            if isinstance(r, errors.BodhanError):
                failures += 1
                warnings.append(f"page {page_no} failed: {r.code}: {r.message}")
                per_page.append(PageSummary(page=page_no, chars=0, blocks=0, mean_conf=None, empty=True))
                continue
            md = (r.get("markdown") or "").strip()
            blocks = r.get("blocks") or []
            confs = [float(b["conf"]) for b in blocks if isinstance(b, dict) and isinstance(b.get("conf"), (int, float))]
            for b in blocks:
                if isinstance(b, dict) and isinstance(b.get("conf"), (int, float)) and b["conf"] < OCR_LOW_CONF and len(low) < 50:
                    low.append(LowConfidenceBlock(page=page_no, order=b.get("order"), label=b.get("label"),
                                                  conf=round(float(b["conf"]), 3), text_preview=str(b.get("text", ""))[:80]))
            if not md:
                warnings.append(f"page {page_no}: no text found")
            per_page.append(PageSummary(page=page_no, chars=len(md), blocks=len(blocks),
                                        mean_conf=round(sum(confs) / len(confs), 3) if confs else None, empty=not md))
            parts.append(f"<!-- page {page_no} -->\n{md}")
        if failures == len(rendered):
            raise next(r for r in results if isinstance(r, errors.BodhanError))
        if low:
            warnings.append(f"{len(low)} low-confidence block(s) (conf < {OCR_LOW_CONF}); treat their text with caution")
        markdown = "\n\n".join(parts).strip()
        out = output_path(self._out_dir(output_dir), f"{safe_stem(p.stem)}.ocr.md")
        out.write_text(markdown + "\n", encoding="utf-8")
        inline, preview = _inline(markdown)
        plain = text.HTML_TABLE.sub(" ", markdown)
        return OCRResult(
            markdown_file=str(out), text=inline, preview=preview, page_count=count, pages_processed=selected,
            detected_script=languages.detect_script(plain), language_candidates=languages.language_candidates(plain)[:4],
            tables=len(text.table_shapes(markdown)), low_confidence_blocks=low, per_page=per_page, warnings=warnings,
            cost_inr=ledger.spent, calls=_calls(ledger),
        )

    # ---------------------------------------------------------------------------------
    # make_subtitles / list_voices
    # ---------------------------------------------------------------------------------

    async def make_subtitles(self, segments_file: str, field: str, fmt: str, output_name: str | None, *,
                             output_dir: str | None) -> SubtitleResult:
        p = self._input(segments_file, "segments file")
        try:
            data = json.loads(_read_text_file(p))
            segments = [TranscriptSegment(**s) for s in data["segments"]]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise errors.BodhanError(errors.INVALID_INPUT, f"{p.name} is not a segments file: {exc}",
                                     hint="Pass segments_file from transcribe_audio or translate_text.") from exc
        if fmt not in {"srt", "vtt"}:
            raise errors.BodhanError(errors.INVALID_INPUT, f"Unknown format {fmt!r}.", hint="Use srt or vtt.")
        if field == "auto":
            field = "translation" if any(s.translation for s in segments) else "text"
        if field not in {"text", "translation"}:
            raise errors.BodhanError(errors.INVALID_INPUT, f"Unknown field {field!r}.", hint="Use auto, text or translation.")
        if field == "translation" and not any(s.translation for s in segments):
            raise errors.BodhanError(errors.INVALID_INPUT, "This segments file has no translations.",
                                     hint="Run translate_text with this file as text_file first, then use its segments_file.")
        cues: list[tuple[float, float, str]] = []
        for s in segments:
            line = (getattr(s, field) or "").strip()
            if not line:
                continue
            # Long segments become several timed cues, allotted by character share.
            parts = text.split_for_tts(line, 84) if len(line) > 84 else [line]
            total = sum(len(x) for x in parts) or 1
            t = s.start
            for part in parts:
                d = (s.end - s.start) * len(part) / total
                cues.append((t, t + d, part))
                t += d
        body = text.to_srt(cues) if fmt == "srt" else text.to_vtt(cues)
        stem = p.name.removesuffix(".segments.json").removesuffix(".json")
        name = output_name or f"{stem}.{fmt}"
        if not name.endswith(f".{fmt}"):
            name += f".{fmt}"
        out = output_path(self._out_dir(output_dir), name)
        out.write_text(body, encoding="utf-8")
        return SubtitleResult(subtitle_file=str(out), format=fmt, cues=len(cues), field_used=field,
                              duration_s=float(data.get("duration_s") or (segments[-1].end if segments else 0)))

    def list_voices(self, language: str | None = None) -> VoicesResult:
        all_voices = languages.voices()
        if language:
            lang = languages.require_language(language, "speak")
            all_voices = {lang: all_voices.get(lang, {})}
        return VoicesResult(voices=all_voices, styles=languages.styles())


async def run_with_ledger[T](fn: Callable[[Ledger], Awaitable[T]], max_cost_inr: float | None) -> T:
    """Run a tool body with a fresh ledger; attach spend to any anticipated error so callers can account for it."""
    ledger = Ledger(max_cost_inr)
    try:
        return await fn(ledger)
    except errors.BodhanError as exc:
        exc.details.setdefault("cost_inr", ledger.spent)
        exc.details.setdefault("calls", [r.to_dict() for r in ledger.records])
        raise


def describe_capabilities() -> dict[str, Any]:
    return {
        "transcribe": languages.supported("transcribe"),
        "translate": languages.supported("translate"),
        "speak": languages.supported("speak"),
        "ocr": "any script (Indic-OCR); one page image per request",
        "language_names": languages.language_names(),
        "pricing_inr": {
            "indic-transcribe": f"{pricing.TRANSCRIBE_INR_PER_MIN} per audio minute",
            "indic-translate": f"{pricing.TRANSLATE_INR_PER_10K_OUT_TOKENS} per 10k output tokens",
            "indic-speak": f"{pricing.SPEAK_INR_PER_10K_CHARS} per 10k input characters",
            "indic-ocr": f"{pricing.OCR_INR_PER_IMAGE} per page",
        },
    }
