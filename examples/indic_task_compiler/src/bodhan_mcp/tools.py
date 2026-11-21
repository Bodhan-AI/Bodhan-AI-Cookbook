"""Capability-level MCP tool implementations wrapping Bodhan models and deterministic helpers.

Every tool validates inputs thoroughly, handles model limits internally, writes large outputs
to files (never base64 in context), and returns structured ok/error dicts without crashing.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import uuid
from pathlib import Path
from typing import Any

from bodhan_mcp.client import BodhanAPIError, BodhanClient
from bodhan_mcp.media import (
    MediaError,
    compute_audio_windows,
    concat_and_encode_audio,
    convert_to_wav_16k_mono,
    detect_file_kind,
    detect_silences,
    downscale_image,
    extract_probe_wav,
    ffprobe_info,
    image_info,
    parse_page_spec,
    pdf_page_count,
    render_pdf_pages,
    slice_wav,
    split_image_vertical,
    wav_duration_s,
)
from bodhan_mcp.pricing import (
    cost_ocr,
    cost_speak,
    cost_transcribe,
    cost_translate,
    estimate_output_tokens,
)
from bodhan_mcp.rules import (
    is_language_supported,
    language_names,
    resolve_language,
    select_voice,
    supported_languages,
    supported_styles,
)
from bodhan_mcp.schemas import (
    ChunkVerificationVerdict,
    FileFact,
    SpeechChunkInfo,
    TranscriptSegment,
    err_response,
    ok_response,
)
from bodhan_mcp.text_utils import (
    chunk_markdown_for_translation,
    compute_cer,
    detect_repetition,
    detect_text_language,
    extract_digits,
    extract_markdown_tables,
    format_subtitles,
    script_histogram,
    split_chunk_in_half,
    split_text_at_sentences,
    validate_subtitles,
)

MAX_TEXT_BYTES = 5_000_000  # 5 MB safety limit
MAX_OCR_PDF_PAGES = 100
SILENCE_RMS_THRESHOLD = 40.0


class BodhanToolkit:
    """Stateful tool container holding a BodhanClient and default scratch artifact directory."""

    def __init__(
        self,
        client: BodhanClient | None = None,
        work_dir: Path | str | None = None,
    ) -> None:
        self.client = client if client is not None else BodhanClient()
        if work_dir is None:
            self.work_dir = Path(tempfile.gettempdir()) / "bodhan_mcp_artifacts"
        else:
            self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)

    def _out_dir(self, output_dir: str | None) -> Path:
        base = Path(output_dir).expanduser().resolve() if output_dir else self.work_dir
        base.mkdir(parents=True, exist_ok=True)
        return base

    @staticmethod
    def _resolve_input_path(path: str) -> Path:
        return Path(path).expanduser().resolve()

    def _read_text_input(
        self,
        *,
        text: str | None,
        path: str | None,
        field_name: str = "text",
    ) -> tuple[str | None, dict[str, Any] | None]:
        if text is not None and path is not None:
            # Prefer explicit text if non-empty, else read path.
            if text.strip():
                return text, None
        if text is not None:
            if not text.strip():
                return None, err_response(
                    "empty_input",
                    f"Input '{field_name}' is empty.",
                    hint=f"Provide non-empty '{field_name}' or a readable file path.",
                )
            if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
                return None, err_response(
                    "input_too_large",
                    f"Input '{field_name}' exceeds {MAX_TEXT_BYTES} bytes.",
                    hint="Split the document into smaller files before processing.",
                )
            return text, None
        if path is not None:
            p = self._resolve_input_path(path)
            if not p.exists() or not p.is_file():
                return None, err_response(
                    "file_not_found",
                    f"File not found: {path}",
                    hint="Verify the file path exists and is readable.",
                )
            if p.stat().st_size > MAX_TEXT_BYTES:
                return None, err_response(
                    "input_too_large",
                    f"File {path} exceeds {MAX_TEXT_BYTES} bytes.",
                    hint="Split the file into smaller parts.",
                )
            try:
                content = p.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                return None, err_response(
                    "invalid_encoding",
                    f"File {path} is not valid UTF-8 text.",
                    hint="Convert the file to UTF-8 or use ocr_document / transcribe_audio for media files.",
                )
            except OSError as exc:
                return None, err_response("read_error", f"Could not read {path}: {exc}")
            if not content.strip():
                return None, err_response(
                    "empty_input",
                    f"File {path} is empty.",
                    hint="Provide a non-empty text file.",
                )
            return content, None

        return None, err_response(
            "missing_input",
            f"Provide either '{field_name}' or 'path'.",
            hint=f"Pass inline '{field_name}' or a file 'path'.",
        )

    @staticmethod
    def _api_err(exc: BodhanAPIError, model: str, cost_so_far: float = 0.0) -> dict[str, Any]:
        return err_response(
            exc.code,
            exc.message,
            retryable=exc.retryable,
            hint=exc.hint,
            cost_inr=cost_so_far,
            model=model,
        )

    # ------------------------------------------------------------------
    # 1. inspect_files (free)
    # ------------------------------------------------------------------
    async def inspect_files(self, paths: list[str]) -> dict[str, Any]:
        """Inspect one or more files for free: kind, duration, pages, size, script histogram, and cost estimates."""
        if not paths:
            return err_response(
                "missing_input",
                "paths must be a non-empty list of file or directory paths.",
                hint="Pass one or more file paths to inspect.",
            )

        expanded: list[Path] = []
        for raw in paths:
            p = Path(raw).expanduser()
            if p.is_dir():
                children = sorted(c for c in p.iterdir() if c.is_file() and not c.name.startswith("."))
                expanded.extend(children)
            else:
                expanded.append(p)

        facts: list[dict[str, Any]] = []
        for p in expanded:
            resolved = p.resolve()
            if not p.exists():
                facts.append(
                    FileFact(
                        path=str(resolved),
                        exists=False,
                        kind="missing",
                        error=f"File does not exist: {p}",
                    ).model_dump()
                )
                continue

            kind, mime = detect_file_kind(resolved)
            size_bytes = resolved.stat().st_size
            fact = FileFact(
                path=str(resolved),
                exists=True,
                kind=kind,
                mime_type=mime,
                size_bytes=size_bytes,
            )

            try:
                if kind in ("audio", "video"):
                    info = ffprobe_info(resolved)
                    fact.duration_s = info["duration_s"]
                    fact.sample_rate = info["sample_rate"]
                    fact.channels = info["channels"]
                    if info["has_audio"]:
                        fact.estimated_cost_inr["indic-transcribe"] = cost_transcribe(info["duration_s"])
                        fact.estimated_cost_inr["detect_language_probe"] = cost_transcribe(min(10.0, info["duration_s"]))
                    else:
                        fact.error = "Media file contains no audio track."
                elif kind == "pdf":
                    pages = pdf_page_count(resolved)
                    fact.page_count = pages
                    fact.estimated_cost_inr["indic-ocr"] = cost_ocr(pages)
                elif kind == "image":
                    w, h = image_info(resolved)
                    fact.image_width = w
                    fact.image_height = h
                    fact.page_count = 1
                    fact.estimated_cost_inr["indic-ocr"] = cost_ocr(1)
                elif kind == "text":
                    txt = resolved.read_text(encoding="utf-8", errors="replace")
                    fact.char_count = len(txt)
                    fact.script_histogram = script_histogram(txt)
                    det = detect_text_language(txt)
                    fact.detected_language_hint = det["language"] if not det["ambiguous"] else None
                    fact.estimated_cost_inr["indic-translate"] = cost_translate(estimate_output_tokens(txt))
                    fact.estimated_cost_inr["indic-speak"] = cost_speak(len(txt))
            except Exception as exc:
                fact.error = f"Inspection warning: {exc}"

            facts.append(fact.model_dump())

        return ok_response({"files": facts, "count": len(facts)}, cost_inr=0.0, untrusted=False)

    # ------------------------------------------------------------------
    # 2. detect_language (free for text; <=10s probe for audio/video)
    # ------------------------------------------------------------------
    async def detect_language(
        self,
        *,
        path: str | None = None,
        text: str | None = None,
    ) -> dict[str, Any]:
        """Detect language of a text string, text file, or audio/video clip."""
        if text is not None and text.strip():
            det = detect_text_language(text)
            return ok_response(det, cost_inr=0.0, untrusted=False)

        if not path:
            return err_response(
                "missing_input",
                "Provide either 'path' (text/audio/video file) or 'text'.",
                hint="Pass a readable file path or non-empty text.",
            )

        p = self._resolve_input_path(path)
        if not p.exists() or not p.is_file():
            return err_response("file_not_found", f"File not found: {path}", hint="Check that the file path exists.")

        kind, _ = detect_file_kind(p)
        if kind == "text":
            txt, err = self._read_text_input(text=None, path=str(p))
            if err:
                return err
            assert txt is not None
            det = detect_text_language(txt)
            det["path"] = str(p)
            return ok_response(det, cost_inr=0.0, untrusted=False)

        if kind in ("audio", "video"):
            probe_wav = self.work_dir / f"probe_{uuid.uuid4().hex[:10]}.wav"
            try:
                _, probe_dur = extract_probe_wav(p, probe_wav, max_seconds=10.0)
                if probe_dur <= 0.1:
                    return err_response(
                        "empty_audio",
                        f"Audio file {path} has zero playable duration.",
                        hint="Verify the media file has a valid non-empty audio stream.",
                    )
                audio_bytes = probe_wav.read_bytes()
                res = await self.client.transcribe(
                    audio_bytes,
                    filename=probe_wav.name,
                    duration_s=probe_dur,
                    language=None,
                )
            except MediaError as exc:
                return err_response("media_error", str(exc), hint="Ensure ffmpeg/ffprobe can decode this file.")
            except BodhanAPIError as exc:
                return self._api_err(exc, "indic-transcribe")
            finally:
                probe_wav.unlink(missing_ok=True)

            probe_text = str(res.payload.get("text", "")).strip()
            det = detect_text_language(probe_text)
            det["path"] = str(p)
            det["probe_duration_s"] = probe_dur
            det["method"] = f"audio_probe_10s+{det['method']}"
            return ok_response(
                det,
                cost_inr=res.cost_inr,
                cache_hit=res.cache_hit,
                model="indic-transcribe",
                preview=probe_text[:300] if probe_text else None,
                untrusted=True,
            )

        return err_response(
            "unsupported_file_kind",
            f"detect_language supports text, audio, and video files; got '{kind}' ({path}).",
            hint="For images or PDFs, run ocr_document first, then pass the resulting text artifact to detect_language.",
        )

    # ------------------------------------------------------------------
    # 3. transcribe_audio (indic-transcribe)
    # ------------------------------------------------------------------
    async def transcribe_audio(
        self,
        path: str,
        *,
        language: str = "auto",
        output_dir: str | None = None,
    ) -> dict[str, Any]:
        """Transcribe audio or video using indic-transcribe with silence-aware <=28s windowing."""
        p = self._resolve_input_path(path)
        if not p.exists() or not p.is_file():
            return err_response("file_not_found", f"Audio/video file not found: {path}")

        kind, _ = detect_file_kind(p)
        if kind not in ("audio", "video"):
            return err_response(
                "invalid_file_kind",
                f"Expected audio or video file, got '{kind}' for {path}.",
                hint="Pass an audio/video file (WAV, MP3, FLAC, OGG, M4A, MP4, WebM).",
            )

        resolved_lang: str | None = None
        detected_meta: dict[str, Any] | None = None
        total_cost = 0.0
        any_cache_miss = False

        if language and language.strip().lower() != "auto":
            iso, known = resolve_language(language)
            if not iso or not known or not is_language_supported("indic-transcribe", iso):
                return err_response(
                    "unsupported_language",
                    f"Language '{language}' is not supported by indic-transcribe.",
                    hint=f"Supported ISO codes: {', '.join(supported_languages('indic-transcribe'))}",
                    model="indic-transcribe",
                )
            resolved_lang = iso

        out_dir = self._out_dir(output_dir)
        run_tag = f"asr_{p.stem}_{uuid.uuid4().hex[:8]}"
        work_wav = out_dir / f".{run_tag}_16k.wav"

        try:
            convert_to_wav_16k_mono(p, work_wav)
            total_dur = wav_duration_s(work_wav)
            if total_dur <= 0.05:
                return err_response("empty_audio", f"No audio frames decoded from {path}.")

            # Auto-detect language via <=10s probe before full transcription so non-English isn't garbled.
            if resolved_lang is None:
                probe_wav = out_dir / f".{run_tag}_probe.wav"
                try:
                    _, probe_dur = extract_probe_wav(p, probe_wav, max_seconds=10.0)
                    probe_res = await self.client.transcribe(
                        probe_wav.read_bytes(),
                        filename=probe_wav.name,
                        duration_s=probe_dur,
                        language=None,
                    )
                    total_cost += probe_res.cost_inr
                    any_cache_miss |= not probe_res.cache_hit
                    probe_txt = str(probe_res.payload.get("text", "")).strip()
                    detected_meta = detect_text_language(probe_txt)
                    cand = detected_meta.get("language")
                    if isinstance(cand, str) and is_language_supported("indic-transcribe", cand):
                        resolved_lang = cand
                finally:
                    probe_wav.unlink(missing_ok=True)

            silences = detect_silences(work_wav)
            windows = compute_audio_windows(total_dur, silences, max_window_s=28.0)

            seg_dir = out_dir / f"{run_tag}_segments"
            seg_dir.mkdir(parents=True, exist_ok=True)

            segments: list[TranscriptSegment] = []
            texts: list[str] = []
            dropped_silence_count = 0

            for win in windows:
                chunk_wav = seg_dir / f"chunk_{win.index:04d}.wav"
                _, rms = slice_wav(work_wav, win.start_s, win.end_s, chunk_wav)
                if win.is_pure_silence or rms < SILENCE_RMS_THRESHOLD:
                    dropped_silence_count += 1
                    chunk_wav.unlink(missing_ok=True)
                    continue

                audio_bytes = chunk_wav.read_bytes()
                chunk_wav.unlink(missing_ok=True)
                res = await self.client.transcribe(
                    audio_bytes,
                    filename=f"{run_tag}_{win.index:04d}.wav",
                    duration_s=win.duration_s,
                    language=resolved_lang,
                )
                total_cost += res.cost_inr
                any_cache_miss |= not res.cache_hit
                seg_text = str(res.payload.get("text", "")).strip()
                seg_txt_path = seg_dir / f"seg_{win.index:04d}.txt"
                seg_txt_path.write_text(seg_text, encoding="utf-8")
                texts.append(seg_text)
                segments.append(
                    TranscriptSegment(
                        index=len(segments),
                        start_s=win.start_s,
                        end_s=win.end_s,
                        text_path=str(seg_txt_path),
                        char_count=len(seg_text),
                        duration_s=round(win.duration_s, 3),
                    )
                )

            full_text = "\n".join(t for t in texts if t).strip()
            text_path = out_dir / f"{run_tag}.txt"
            text_path.write_text(full_text, encoding="utf-8")

            segments_payload = [s.model_dump() for s in segments]
            segments_path = out_dir / f"{run_tag}_segments.json"
            segments_path.write_text(json.dumps(segments_payload, ensure_ascii=False, indent=2), encoding="utf-8")

            return ok_response(
                {
                    "text_path": str(text_path),
                    "segments_path": str(segments_path),
                    "language": resolved_lang,
                    "language_detection": detected_meta,
                    "duration_s": round(total_dur, 3),
                    "window_count": len(windows),
                    "segment_count": len(segments),
                    "dropped_silence_windows": dropped_silence_count,
                    "char_count": len(full_text),
                    "provenance": {"source_model": "indic-transcribe", "source_path": str(p)},
                },
                cost_inr=total_cost,
                cache_hit=not any_cache_miss,
                model="indic-transcribe",
                preview=full_text[:300],
                untrusted=True,
            )
        except MediaError as exc:
            return err_response("media_error", str(exc), cost_inr=total_cost, model="indic-transcribe")
        except BodhanAPIError as exc:
            return self._api_err(exc, "indic-transcribe", cost_so_far=total_cost)
        finally:
            work_wav.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # 4. translate_text (indic-translate)
    # ------------------------------------------------------------------
    async def translate_text(
        self,
        target_language: str,
        *,
        text: str | None = None,
        path: str | None = None,
        script: str = "native",
        max_output_tokens: int | None = None,
        output_dir: str | None = None,
    ) -> dict[str, Any]:
        """Translate text or a text/Markdown file with table-preserving chunking and truncation recovery."""
        iso, known = resolve_language(target_language)
        if not iso or not known or not is_language_supported("indic-translate", iso):
            return err_response(
                "unsupported_language",
                f"Target language '{target_language}' is not supported by indic-translate.",
                hint=f"Supported ISO codes: {', '.join(supported_languages('indic-translate'))}",
                model="indic-translate",
            )
        if script not in ("native", "roman", "codemix"):
            return err_response(
                "invalid_script",
                f"Script '{script}' is invalid. Allowed: native, roman, codemix.",
                model="indic-translate",
            )

        src_text, err = self._read_text_input(text=text, path=path)
        if err:
            return err
        assert src_text is not None

        chunks = chunk_markdown_for_translation(src_text, max_chars=1800)
        if not chunks:
            return err_response("empty_input", "No translatable text blocks found.")

        total_cost = 0.0
        any_cache_miss = False
        splits_triggered = 0

        async def _translate_piece(piece: str, depth: int = 0) -> str:
            nonlocal total_cost, any_cache_miss, splits_triggered
            res = await self.client.translate(
                piece,
                target_language=iso,
                script=script,
                max_output_tokens=max_output_tokens,
            )
            total_cost += res.cost_inr
            any_cache_miss |= not res.cache_hit
            truncated = bool(res.payload.get("truncated", False))
            if truncated and depth < 4 and len(piece) > 40:
                splits_triggered += 1
                left, right = split_chunk_in_half(piece)
                t_left = await _translate_piece(left, depth + 1)
                t_right = await _translate_piece(right, depth + 1)
                # Re-merge split Markdown tables cleanly by dropping duplicate header on right half.
                r_lines = t_right.splitlines()
                l_lines = t_left.splitlines()
                if (
                    len(l_lines) >= 2
                    and len(r_lines) >= 3
                    and l_lines[0].strip().startswith("|")
                    and r_lines[0].strip().startswith("|")
                ):
                    return "\n".join(l_lines + r_lines[2:])
                return f"{t_left}\n{t_right}".strip()
            return str(res.payload.get("translation", "")).strip()

        try:
            translated_chunks: list[str] = []
            for ch in chunks:
                translated_chunks.append(await _translate_piece(ch))
        except BodhanAPIError as exc:
            return self._api_err(exc, "indic-translate", cost_so_far=total_cost)

        full_translation = "\n\n".join(translated_chunks)
        out_dir = self._out_dir(output_dir)
        out_path = out_dir / f"translated_{iso}_{uuid.uuid4().hex[:8]}.md"
        out_path.write_text(full_translation, encoding="utf-8")

        # Coverage checks: chunk parity, Markdown table shape, digits preservation.
        src_tables = extract_markdown_tables(src_text)
        dst_tables = extract_markdown_tables(full_translation)
        src_digits = extract_digits(src_text)
        dst_digits = extract_digits(full_translation)
        missing_digits = [d for d in src_digits if d not in dst_digits]
        tables_preserved = len(src_tables) == len(dst_tables) and all(
            s["rows"] == d["rows"] and s["cols"] == d["cols"] for s, d in zip(src_tables, dst_tables)
        )

        return ok_response(
            {
                "text_path": str(out_path),
                "target_language": iso,
                "script": script,
                "chunks_in": len(chunks),
                "chunks_out": len(translated_chunks),
                "truncation_splits": splits_triggered,
                "source_char_count": len(src_text),
                "translated_char_count": len(full_translation),
                "coverage": {
                    "chunks_matched": len(chunks) == len(translated_chunks),
                    "source_tables": src_tables,
                    "translated_tables": dst_tables,
                    "tables_preserved": tables_preserved,
                    "source_digit_count": len(src_digits),
                    "missing_digits": missing_digits[:20],
                    "digits_preserved": len(missing_digits) == 0,
                },
                "provenance": {"source_model": "indic-translate", "target_language": iso},
            },
            cost_inr=total_cost,
            cache_hit=not any_cache_miss,
            model="indic-translate",
            preview=full_translation[:300],
            untrusted=True,
        )

    # ------------------------------------------------------------------
    # 5. synthesize_speech (indic-speak)
    # ------------------------------------------------------------------
    async def synthesize_speech(
        self,
        language: str,
        *,
        text: str | None = None,
        path: str | None = None,
        voice: str | None = None,
        gender: str | None = None,
        style: str | None = None,
        format: str = "mp3",
        output_dir: str | None = None,
    ) -> dict[str, Any]:
        """Synthesize speech with indic-speak: sentence chunking (<=250 chars), concat, loudnorm, AI-generated metadata."""
        iso, known = resolve_language(language)
        if not iso or not known or not is_language_supported("indic-speak", iso):
            return err_response(
                "unsupported_language",
                f"Language '{language}' is not supported by indic-speak.",
                hint=f"Supported ISO codes: {', '.join(supported_languages('indic-speak'))}",
                model="indic-speak",
            )

        selected_voice, voice_err = select_voice(iso, voice=voice, gender=gender)
        if voice_err or not selected_voice:
            return err_response("invalid_voice", voice_err or "Could not select voice.", model="indic-speak")

        if style:
            allowed_styles = supported_styles()
            lower_styles = {s.lower(): s for s in allowed_styles}
            if style.lower() not in lower_styles:
                return err_response(
                    "invalid_style",
                    f"Style '{style}' is not supported.",
                    hint=f"Allowed styles: {', '.join(allowed_styles)}",
                    model="indic-speak",
                )
            style = lower_styles[style.lower()]

        fmt = format.strip().lower()
        if fmt not in ("mp3", "wav"):
            return err_response("invalid_format", f"Unsupported audio format '{format}'. Use 'mp3' or 'wav'.")

        src_text, err = self._read_text_input(text=text, path=path)
        if err:
            return err
        assert src_text is not None

        chunks = split_text_at_sentences(src_text, max_chars=250)
        if not chunks:
            return err_response("empty_input", "No speakable sentences found in input.")

        out_dir = self._out_dir(output_dir)
        run_tag = f"tts_{iso}_{uuid.uuid4().hex[:8]}"
        chunk_dir = out_dir / f"{run_tag}_chunks"
        chunk_dir.mkdir(parents=True, exist_ok=True)

        total_cost = 0.0
        any_cache_miss = False
        wav_paths: list[Path] = []
        chunk_infos: list[SpeechChunkInfo] = []

        try:
            for idx, sentence in enumerate(chunks):
                res = await self.client.speak(
                    sentence,
                    voice=selected_voice,
                    language=iso.split("-")[0],
                    style=style,
                )
                total_cost += res.cost_inr
                any_cache_miss |= not res.cache_hit
                if not res.binary:
                    return err_response("empty_audio", f"indic-speak returned empty audio for chunk {idx}.")
                is_riff = len(res.binary) >= 12 and res.binary[:4] == b"RIFF" and res.binary[8:12] == b"WAVE"
                chunk_wav = chunk_dir / f"chunk_{idx:04d}.wav"
                if is_riff:
                    chunk_wav.write_bytes(res.binary)
                    dur = wav_duration_s(chunk_wav)
                else:
                    raw_chunk = chunk_dir / f"chunk_{idx:04d}.audio"
                    raw_chunk.write_bytes(res.binary)
                    convert_to_wav_16k_mono(raw_chunk, chunk_wav)
                    raw_chunk.unlink(missing_ok=True)
                    dur = wav_duration_s(chunk_wav)
                chunk_txt = chunk_dir / f"chunk_{idx:04d}.txt"
                chunk_txt.write_text(sentence, encoding="utf-8")
                wav_paths.append(chunk_wav)
                chunk_infos.append(
                    SpeechChunkInfo(
                        index=idx,
                        audio_path=str(chunk_wav),
                        text_path=str(chunk_txt),
                        char_count=len(sentence),
                        duration_s=round(dur, 3),
                    )
                )

            final_audio = out_dir / f"{run_tag}.{fmt}"
            _, total_dur = concat_and_encode_audio(
                wav_paths,
                final_audio,
                fmt=fmt,
                normalize=True,
                ai_comment="AI-generated (Bodhan Indic-Speak)",
            )
            manifest_path = out_dir / f"{run_tag}_chunks.json"
            manifest_path.write_text(
                json.dumps([c.model_dump() for c in chunk_infos], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            return ok_response(
                {
                    "audio_path": str(final_audio),
                    "chunk_manifest_path": str(manifest_path),
                    "language": iso,
                    "voice": selected_voice,
                    "style": style,
                    "format": fmt,
                    "duration_s": round(total_dur, 3),
                    "chunk_count": len(chunk_infos),
                    "input_char_count": len(src_text),
                    "ai_generated_label": "AI-generated (Bodhan Indic-Speak)",
                    "chunks": [c.model_dump() for c in chunk_infos],
                },
                cost_inr=total_cost,
                cache_hit=not any_cache_miss,
                model="indic-speak",
                untrusted=False,
            )
        except MediaError as exc:
            return err_response("media_error", str(exc), cost_inr=total_cost, model="indic-speak")
        except BodhanAPIError as exc:
            return self._api_err(exc, "indic-speak", cost_so_far=total_cost)

    # ------------------------------------------------------------------
    # 6. verify_speech (indic-transcribe back-transcription)
    # ------------------------------------------------------------------
    async def verify_speech(
        self,
        audio_path: str,
        language: str,
        *,
        expected_text: str | None = None,
        expected_path: str | None = None,
        chunk_manifest_path: str | None = None,
        cer_threshold: float = 0.45,
    ) -> dict[str, Any]:
        """Verify synthesized speech by back-transcribing every chunk with indic-transcribe.

        Checks normalized CER, n-gram repetition loops, and duration/character-rate anomalies.
        """
        iso, known = resolve_language(language)
        if not iso or not known or not is_language_supported("indic-transcribe", iso):
            return err_response(
                "unsupported_language",
                f"Language '{language}' is not supported by indic-transcribe for verification.",
                model="indic-transcribe",
            )

        p = self._resolve_input_path(audio_path)
        if not p.exists() or not p.is_file():
            return err_response("file_not_found", f"Audio file not found: {audio_path}")

        pairs: list[tuple[int, Path, str]] = []
        if chunk_manifest_path:
            mp = self._resolve_input_path(chunk_manifest_path)
            if not mp.exists():
                return err_response("file_not_found", f"Chunk manifest not found: {chunk_manifest_path}")
            try:
                manifest = json.loads(mp.read_text(encoding="utf-8"))
                for item in manifest:
                    c_audio = Path(item["audio_path"])
                    c_text = Path(item["text_path"]).read_text(encoding="utf-8")
                    if c_audio.exists():
                        pairs.append((int(item["index"]), c_audio, c_text))
            except Exception as exc:
                return err_response("invalid_manifest", f"Failed to parse chunk manifest: {exc}")

        if not pairs:
            exp_txt, err = self._read_text_input(text=expected_text, path=expected_path, field_name="expected_text")
            if err:
                return err
            assert exp_txt is not None
            pairs = [(0, p, exp_txt)]

        total_cost = 0.0
        any_cache_miss = False
        verdicts: list[ChunkVerificationVerdict] = []

        try:
            for idx, c_audio, exp in pairs:
                tmp_wav = self.work_dir / f".verify_{uuid.uuid4().hex[:8]}.wav"
                try:
                    convert_to_wav_16k_mono(c_audio, tmp_wav)
                    dur = wav_duration_s(tmp_wav)
                    # If single whole audio > 28s, chunk via transcribe_audio; else direct transcribe.
                    if dur > 28.0:
                        tr = await self.transcribe_audio(str(c_audio), language=iso)
                        if not tr["ok"]:
                            return tr
                        total_cost += float(tr["cost_inr"])
                        any_cache_miss |= not bool(tr["cache_hit"])
                        hyp = Path(tr["data"]["text_path"]).read_text(encoding="utf-8")
                    else:
                        res = await self.client.transcribe(
                            tmp_wav.read_bytes(),
                            filename=f"verify_{idx}.wav",
                            duration_s=dur,
                            language=iso,
                        )
                        total_cost += res.cost_inr
                        any_cache_miss |= not res.cache_hit
                        hyp = str(res.payload.get("text", "")).strip()
                finally:
                    tmp_wav.unlink(missing_ok=True)

                cer = compute_cer(exp, hyp)
                rep, rep_phrase = detect_repetition(hyp)
                cps = round(len(exp) / max(0.1, dur), 2)
                # Normal speech spans roughly 3–35 chars/sec; flag extreme truncation or runaway stretch.
                dur_anomaly = (dur < 0.25 and len(exp) > 8) or (cps > 45.0) or (cps < 1.5 and dur > 4.0)
                reasons: list[str] = []
                if cer > cer_threshold:
                    reasons.append(f"CER {cer:.2f} exceeds threshold {cer_threshold:.2f}")
                if rep:
                    reasons.append(f"Repetition detected ('{rep_phrase}')")
                if dur_anomaly:
                    reasons.append(f"Duration/char-rate anomaly ({dur:.2f}s for {len(exp)} chars, {cps} ch/s)")

                verdicts.append(
                    ChunkVerificationVerdict(
                        index=idx,
                        audio_path=str(c_audio),
                        expected_chars=len(exp),
                        transcribed_chars=len(hyp),
                        cer=cer,
                        repetition_detected=rep,
                        repeated_ngram=rep_phrase,
                        duration_s=round(dur, 3),
                        chars_per_second=cps,
                        duration_anomaly=dur_anomaly,
                        passed=len(reasons) == 0,
                        reasons=reasons,
                    )
                )
        except MediaError as exc:
            return err_response("media_error", str(exc), cost_inr=total_cost, model="indic-transcribe")
        except BodhanAPIError as exc:
            return self._api_err(exc, "indic-transcribe", cost_so_far=total_cost)

        failed_chunks = [v.index for v in verdicts if not v.passed]
        mean_cer = round(sum(v.cer for v in verdicts) / max(1, len(verdicts)), 4)

        return ok_response(
            {
                "passed": len(failed_chunks) == 0,
                "mean_cer": mean_cer,
                "cer_threshold": cer_threshold,
                "chunks_checked": len(verdicts),
                "failed_chunk_indices": failed_chunks,
                "verdicts": [v.model_dump() for v in verdicts],
            },
            cost_inr=total_cost,
            cache_hit=not any_cache_miss,
            model="indic-transcribe",
            untrusted=False,
        )

    # ------------------------------------------------------------------
    # 7. ocr_document (indic-ocr)
    # ------------------------------------------------------------------
    async def ocr_document(
        self,
        path: str,
        *,
        pages: str | None = None,
        table_format: str = "markdown",
        output_dir: str | None = None,
    ) -> dict[str, Any]:
        """Extract Markdown text and layout blocks from an image or multi-page PDF via indic-ocr.

        Rasterizes PDFs with pypdfium2 (no poppler), retries 502 with larger max_tokens,
        then downscaling, then vertical splitting.
        """
        p = self._resolve_input_path(path)
        if not p.exists() or not p.is_file():
            return err_response("file_not_found", f"Document file not found: {path}")

        if table_format not in ("markdown", "html"):
            return err_response("invalid_table_format", f"table_format must be 'markdown' or 'html', got '{table_format}'.")

        kind, mime = detect_file_kind(p)
        if kind not in ("image", "pdf"):
            return err_response(
                "invalid_file_kind",
                f"ocr_document requires an image (PNG/JPEG) or PDF, got '{kind}' for {path}.",
                hint="For audio/video use transcribe_audio; for plain text read it directly.",
            )

        out_dir = self._out_dir(output_dir)
        run_tag = f"ocr_{p.stem}_{uuid.uuid4().hex[:8]}"
        pages_dir = out_dir / f"{run_tag}_pages"
        pages_dir.mkdir(parents=True, exist_ok=True)

        page_images: list[tuple[int, Path, str]] = []
        if kind == "pdf":
            try:
                total_pages = pdf_page_count(p)
                if total_pages > MAX_OCR_PDF_PAGES and not pages:
                    return err_response(
                        "pdf_too_large",
                        f"PDF has {total_pages} pages (limit {MAX_OCR_PDF_PAGES}). Specify a page range.",
                        hint="Pass pages='1-10' to process a subset.",
                    )
                selected = parse_page_spec(pages, total_pages)
                for page_num, img_path in render_pdf_pages(p, pages_dir, pages=selected, dpi=175):
                    page_images.append((page_num, img_path, "image/png"))
            except ValueError as exc:
                return err_response("invalid_page_spec", str(exc), hint="Use 1-based ranges like '1-3,5'.")
            except Exception as exc:
                return err_response("pdf_render_error", f"Failed to render PDF {path}: {exc}")
        else:
            img_mime = mime if mime in ("image/png", "image/jpeg") else "image/png"
            if img_mime == "image/png" and mime not in ("image/png", "image/jpeg"):
                converted = pages_dir / f"{p.stem}_converted.png"
                downscale_image(p, converted, scale=1.0)
                page_images.append((1, converted, "image/png"))
            else:
                page_images.append((1, p, img_mime))

        total_cost = 0.0
        any_cache_miss = False

        async def _ocr_single_image(img_path: Path, img_mime: str, page_num: int) -> tuple[str, list[dict[str, Any]], str]:
            nonlocal total_cost, any_cache_miss
            img_bytes = img_path.read_bytes()
            # Attempt 1: default max_tokens.
            try:
                res = await self.client.ocr(img_bytes, mime_type=img_mime, table_format=table_format)
                total_cost += res.cost_inr
                any_cache_miss |= not res.cache_hit
                md = str(res.payload["choices"][0]["message"]["content"]).strip()
                return md, list(res.payload.get("blocks") or []), "direct"
            except BodhanAPIError as exc:
                if exc.status_code != 502:
                    raise

            # Attempt 2 (on 502): bump max_tokens to 8192.
            try:
                res = await self.client.ocr(img_bytes, mime_type=img_mime, table_format=table_format, max_tokens=8192)
                total_cost += res.cost_inr
                any_cache_miss |= not res.cache_hit
                md = str(res.payload["choices"][0]["message"]["content"]).strip()
                return md, list(res.payload.get("blocks") or []), "retry_max_tokens"
            except BodhanAPIError as exc:
                if exc.status_code != 502:
                    raise

            # Attempt 3 (on 502): downscale image to 65%.
            scaled_path = pages_dir / f"page_{page_num:03d}_scaled.png"
            downscale_image(img_path, scaled_path, scale=0.65)
            try:
                res = await self.client.ocr(scaled_path.read_bytes(), mime_type="image/png", table_format=table_format, max_tokens=8192)
                total_cost += res.cost_inr
                any_cache_miss |= not res.cache_hit
                md = str(res.payload["choices"][0]["message"]["content"]).strip()
                return md, list(res.payload.get("blocks") or []), "retry_downscale"
            except BodhanAPIError as exc:
                if exc.status_code != 502:
                    raise

            # Attempt 4 (on 502): split page into top/bottom halves.
            top_p = pages_dir / f"page_{page_num:03d}_top.png"
            bot_p = pages_dir / f"page_{page_num:03d}_bot.png"
            split_image_vertical(img_path, top_p, bot_p)
            res_top = await self.client.ocr(top_p.read_bytes(), mime_type="image/png", table_format=table_format, max_tokens=8192)
            total_cost += res_top.cost_inr
            any_cache_miss |= not res_top.cache_hit
            res_bot = await self.client.ocr(bot_p.read_bytes(), mime_type="image/png", table_format=table_format, max_tokens=8192)
            total_cost += res_bot.cost_inr
            any_cache_miss |= not res_bot.cache_hit
            md_top = str(res_top.payload["choices"][0]["message"]["content"]).strip()
            md_bot = str(res_bot.payload["choices"][0]["message"]["content"]).strip()
            blocks = list(res_top.payload.get("blocks") or []) + list(res_bot.payload.get("blocks") or [])
            return f"{md_top}\n\n{md_bot}".strip(), blocks, "retry_split_halves"

        page_summaries: list[dict[str, Any]] = []
        page_markdowns: list[str] = []

        try:
            # Run pages with bounded concurrency (client semaphore caps in-flight requests).
            async def _run_page(item: tuple[int, Path, str]) -> tuple[int, str, list[dict[str, Any]], str]:
                pnum, ipath, imime = item
                md, blocks, strategy = await _ocr_single_image(ipath, imime, pnum)
                return pnum, md, blocks, strategy

            results = await asyncio.gather(*(_run_page(item) for item in page_images))
            results.sort(key=lambda x: x[0])

            for page_num, md, blocks, strategy in results:
                page_md_path = pages_dir / f"page_{page_num:03d}.md"
                page_md_path.write_text(md, encoding="utf-8")
                page_markdowns.append(md)
                low_conf = [
                    {"order": b.get("order"), "label": b.get("label"), "conf": b.get("conf")}
                    for b in blocks
                    if isinstance(b.get("conf"), (int, float)) and float(b["conf"]) < 0.7
                ]
                page_summaries.append(
                    {
                        "page": page_num,
                        "markdown_path": str(page_md_path),
                        "char_count": len(md),
                        "block_count": len(blocks),
                        "low_conf_blocks": low_conf,
                        "table_count": len(extract_markdown_tables(md)),
                        "strategy": strategy,
                    }
                )
        except BodhanAPIError as exc:
            return self._api_err(exc, "indic-ocr", cost_so_far=total_cost)

        combined_md = "\n\n".join(page_markdowns).strip()
        combined_path = out_dir / f"{run_tag}.md"
        combined_path.write_text(combined_md, encoding="utf-8")
        det = detect_text_language(combined_md)

        return ok_response(
            {
                "text_path": str(combined_path),
                "page_count": len(page_summaries),
                "char_count": len(combined_md),
                "pages": page_summaries,
                "table_shapes": extract_markdown_tables(combined_md),
                "detected_language": det.get("language"),
                "language_ambiguous": det.get("ambiguous"),
                "script_histogram": det.get("script_histogram"),
                "provenance": {"source_model": "indic-ocr", "source_path": str(p)},
            },
            cost_inr=total_cost,
            cache_hit=not any_cache_miss,
            model="indic-ocr",
            preview=combined_md[:300],
            untrusted=True,
        )

    # ------------------------------------------------------------------
    # 8. write_subtitles (deterministic, free)
    # ------------------------------------------------------------------
    async def write_subtitles(
        self,
        segments_path: str,
        *,
        translations_path: str | None = None,
        format: str = "srt",
        output_path: str | None = None,
        output_dir: str | None = None,
    ) -> dict[str, Any]:
        """Generate SRT or WebVTT subtitles deterministically from timed segments and optional translations."""
        fmt = format.strip().lower()
        if fmt not in ("srt", "vtt"):
            return err_response("invalid_format", f"Subtitle format must be 'srt' or 'vtt', got '{format}'.")

        sp = self._resolve_input_path(segments_path)
        if not sp.exists() or not sp.is_file():
            return err_response("file_not_found", f"Segments file not found: {segments_path}")

        try:
            raw_segments = json.loads(sp.read_text(encoding="utf-8"))
            if not isinstance(raw_segments, list) or not raw_segments:
                return err_response("invalid_segments", "segments_path must contain a non-empty JSON list.")
        except Exception as exc:
            return err_response("invalid_segments", f"Could not parse segments JSON: {exc}")

        translated_lines: list[str] | None = None
        if translations_path:
            tp = self._resolve_input_path(translations_path)
            if not tp.exists() or not tp.is_file():
                return err_response("file_not_found", f"Translations file not found: {translations_path}")
            raw_t = tp.read_text(encoding="utf-8").strip()
            if tp.suffix.lower() == ".json":
                parsed = json.loads(raw_t)
                if isinstance(parsed, list):
                    translated_lines = [str(x).strip() for x in parsed]
            if translated_lines is None:
                # Split by double-newline or single-newline to align with segments.
                paras = [p.strip() for p in raw_t.split("\n\n") if p.strip()]
                lines = [ln.strip() for ln in raw_t.splitlines() if ln.strip()]
                if len(lines) == len(raw_segments):
                    translated_lines = lines
                elif len(paras) == len(raw_segments):
                    translated_lines = paras
                else:
                    # Distribute text proportionally across segments by segment char weight.
                    sentences = split_text_at_sentences(raw_t, max_chars=240)
                    units = sentences if len(sentences) >= len(raw_segments) else raw_t.split()
                    weights = [max(1, int(s.get("char_count") or 1)) for s in raw_segments]
                    total_w = sum(weights)
                    translated_lines = []
                    cursor = 0
                    for idx, w in enumerate(weights):
                        if idx == len(weights) - 1:
                            bucket = units[cursor:]
                        else:
                            remaining_segs = len(weights) - idx - 1
                            max_take = max(1, len(units) - cursor - remaining_segs)
                            take = min(max_take, max(1, round(len(units) * (w / total_w))))
                            bucket = units[cursor : cursor + take]
                            cursor += take
                        translated_lines.append(" ".join(bucket).strip())

        merged: list[dict[str, Any]] = []
        for idx, seg in enumerate(raw_segments):
            if translated_lines is not None and idx < len(translated_lines):
                seg_text = translated_lines[idx]
            elif seg.get("text"):
                seg_text = str(seg["text"])
            elif seg.get("text_path") and Path(seg["text_path"]).exists():
                seg_text = Path(seg["text_path"]).read_text(encoding="utf-8").strip()
            else:
                seg_text = ""
            merged.append(
                {
                    "start_s": float(seg["start_s"]),
                    "end_s": float(seg["end_s"]),
                    "text": seg_text,
                }
            )

        content, cue_count = format_subtitles(merged, fmt=fmt)
        validation = validate_subtitles(content)

        if output_path:
            out_p = Path(output_path).expanduser().resolve()
            out_p.parent.mkdir(parents=True, exist_ok=True)
        else:
            out_p = self._out_dir(output_dir) / f"subtitles_{uuid.uuid4().hex[:8]}.{fmt}"
        out_p.write_text(content, encoding="utf-8")

        return ok_response(
            {
                "subtitle_path": str(out_p),
                "format": fmt,
                "segment_count": len(raw_segments),
                "cue_count": cue_count,
                "validation": validation,
            },
            cost_inr=0.0,
            untrusted=True,
            preview=content[:300],
        )


def list_languages_summary() -> dict[str, Any]:
    """Summary payload for the bodhan://languages MCP resource."""
    names = language_names()
    return {
        "language_names": names,
        "by_model": {
            "indic-transcribe": supported_languages("indic-transcribe"),
            "indic-translate": supported_languages("indic-translate"),
            "indic-speak": supported_languages("indic-speak"),
            "indic-ocr": supported_languages("indic-ocr"),
        },
    }
