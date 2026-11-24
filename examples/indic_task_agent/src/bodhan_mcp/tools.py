"""The tool implementations behind the MCP server.

Each tool takes a Services object and file paths, handles the model's limits itself (windows, chunks,
pages, re-splits) and returns a ToolResult, or raises ToolFailure.

- text: translate_text, transliterate_text, read_text, detect_language
- audio: transcribe_audio, synthesize_speech, make_subtitles
- documents: ocr_document, inspect_media, estimate_cost
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from . import catalog, files
from .client import SPEAK, TRANSCRIBE, TRANSLATE, TRANSLITERATE, BodhanClient, Cache, Code, Settings, ToolFailure
from .files import Store, classify
from .text import (
    LANGUAGE_SCRIPT,
    Block,
    detect_language,
    halve,
    is_separator_row,
    join_windows,
    looks_like_markdown,
    markdown_blocks,
    pack,
    render_table,
    script_counts,
    script_fraction,
    split_sentences,
    strip_markdown_for_speech,
    table_rows,
    table_structure,
    text_windows,
)


class ToolResult(BaseModel):
    ok: bool = Field(True, description="Always true here; failures are returned as MCP errors instead.")
    tool: str = Field(description="Name of the tool that produced this result.")
    result_path: str | None = Field(None, description="Main output file. Pass this path to the next tool.")
    preview: str = Field("", description="First few hundred characters of the output, for a quick look.")
    text: str | None = Field(None, description="Full text, only for tools that read text (read_text).")
    language: str | None = Field(None, description="Language code of the output, when known.")
    meta: dict[str, Any] = Field(default_factory=dict, description="Tool-specific details: sizes, durations, checks.")
    cost_inr: float = Field(0.0, description="Bodhan cost of this call in INR (0 when served from cache).")
    cached: bool = Field(False, description="True when every model call was served from the local cache.")
    warnings: list[str] = Field(default_factory=list, description="Non-fatal problems the caller should know about.")


@dataclass
class Services:
    settings: Settings
    store: Store
    cache: Cache
    client: BodhanClient

    @classmethod
    def create(cls, settings: Settings | None = None, http: httpx.AsyncClient | None = None) -> Services:
        settings = settings or Settings.from_env()
        cache = Cache(settings.cache_dir / "responses.sqlite3")
        return cls(settings=settings, store=Store(settings.workdir), cache=cache,
                   client=BodhanClient(settings, cache, http=http))


MAX_TEXT_CHARS = 200_000


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def translate_window(svc: Services, text: str, target: str, script: str, depth: int = 0,
                           model: str = TRANSLATE) -> tuple[str, int, bool, bool]:
    """Translate one window. Returns (output, billed_output_tokens, cached, truncated).
    A `finish_reason` of "length" (silent truncation) triggers a re-split, up to 3 levels."""
    res = await svc.client.chat(model, text, {"target_language_code": target, "target_script": script},
                                text.encode("utf-8"))
    tokens = 0 if res.cached else res.completion_tokens
    if res.finish_reason == "length":
        if depth < 3 and len(text) > 80:
            parts = await asyncio.gather(*(translate_window(svc, p, target, script, depth + 1, model)
                                           for p in halve(text)))
            return (" ".join(p[0].strip() for p in parts), tokens + sum(p[1] for p in parts),
                    res.cached and all(p[2] for p in parts), any(p[3] for p in parts))
        return res.content, tokens, res.cached, True
    return res.content, tokens, res.cached, False


def _expected_script(target: str, script: str) -> str | None:
    if script == "roman":
        return "Latin"
    if script == "codemix":
        return None
    return LANGUAGE_SCRIPT.get(target)


def _restore_table(source: str, translated: str) -> str | None:
    """Return the translated table with the source's separator rows restored, or None if the content
    rows don't line up (different row count or cells per row). The model tends to drop `|---|` rows."""
    src_rows = table_rows(source)
    out_rows = [r for r in table_rows(translated) if not is_separator_row(r)]
    content = [r for r in src_rows if not is_separator_row(r)]
    if [len(r) for r in content] != [len(r) for r in out_rows]:
        return None
    rebuilt, it = [], iter(out_rows)
    for row in src_rows:
        rebuilt.append(row if is_separator_row(row) else next(it))  # keep the source's alignment markers
    return render_table(rebuilt)


async def _translate_table(svc: Services, table: str, target: str, script: str, max_chars: int):
    """Translate a Markdown table in one request; if the model changes its shape, rebuild it cell by cell."""
    if len(table) <= max_chars:
        out, tokens, cached, truncated = await translate_window(svc, table, target, script)
        restored = None if truncated else _restore_table(table, out)
        if restored is not None:
            return restored, tokens, cached, False, "preserved"
    else:
        tokens, cached = 0, True
    rows = table_rows(table)

    async def cell(value: str):
        if not value or not any(ch.isalpha() for ch in value):
            return value, 0, True
        out, t, c, _ = await translate_window(svc, value, target, script)
        return out.strip().replace("|", "/").replace("\n", " "), t, c

    new_rows = []
    for row in rows:
        if is_separator_row(row):
            new_rows.append(row)
            continue
        results = await asyncio.gather(*(cell(v) for v in row))
        new_rows.append([r[0] for r in results])
        tokens += sum(r[1] for r in results)
        cached = cached and all(r[2] for r in results)
    return render_table(new_rows), tokens, cached, False, "rebuilt_per_cell"


async def translate_text(svc: Services, target_language: str, path: str | None = None, text: str | None = None,
                         source_language: str | None = None, script: str = "native", format: str = "auto",
                         max_window_chars: int = 1500) -> ToolResult:
    target = catalog.require_language(TRANSLATE, target_language, field="target_language")
    source = catalog.require_language(TRANSLATE, source_language, field="source_language") if source_language else None
    if script not in catalog.scripts(TRANSLATE):
        raise ToolFailure(Code.INVALID_ARGUMENT, f"Unknown script '{script}'.",
                          f"Use one of: {', '.join(catalog.scripts(TRANSLATE))} (native is the default).")
    if format not in ("auto", "plain", "markdown"):
        raise ToolFailure(Code.INVALID_ARGUMENT, f"Unknown format '{format}'.", "Use auto, plain or markdown.")
    content, stem = svc.store.text_input(path, text)
    content = content.strip()
    if not content:
        raise ToolFailure(Code.EMPTY_TEXT, "There is no text to translate.")
    if len(content) > MAX_TEXT_CHARS:
        raise ToolFailure(Code.INPUT_TOO_LARGE, f"Text has {len(content)} characters; the limit is {MAX_TEXT_CHARS}.",
                          "Split the document and translate the parts separately.")
    max_chars = max(200, min(int(max_window_chars), 3000))
    markdown = format == "markdown" or (format == "auto" and ((path or "").lower().endswith((".md", ".markdown"))
                                                              or looks_like_markdown(content)))
    detected = detect_language(content)
    warnings: list[str] = []
    if not source and detected.get("confidence") == "high" and detected.get("best_guess") == target:
        source = target  # already in the target language: no model call needed

    if source and source == target and script == "native":
        output, tokens, all_cached, truncated_windows, windows = content, 0, True, 0, 0
        tables = tables_preserved = tables_rebuilt = 0
        warnings.append(f"Source and target language are both '{target}'; returned the text unchanged.")
    elif markdown:
        blocks = markdown_blocks(content)
        units: list[Block] = []
        for block in blocks:  # pack consecutive text blocks into windows
            if block.kind == "text" and units and units[-1].kind == "text" and \
                    len(units[-1].text) + 2 + len(block.text) <= max_chars:
                units[-1] = Block("text", units[-1].text + "\n\n" + block.text)
            else:
                units.append(block)

        async def do_unit(unit: Block):
            if unit.kind == "code":
                return unit.text, 0, True, 0, None
            if unit.kind == "table":
                out, t, c, trunc, how = await _translate_table(svc, unit.text, target, script, max_chars)
                return out, t, c, int(trunc), how
            wins = text_windows(unit.text, max_chars)
            res = await asyncio.gather(*(translate_window(svc, w.text, target, script) for w in wins))
            return (join_windows(wins, [r[0] for r in res]), sum(r[1] for r in res), all(r[2] for r in res),
                    sum(int(r[3]) for r in res), None)

        results = await asyncio.gather(*(do_unit(u) for u in units))
        output = "\n\n".join(r[0].strip() for r in results)
        tokens = sum(r[1] for r in results)
        all_cached = all(r[2] for r in results)
        truncated_windows = sum(r[3] for r in results)
        windows = len(units)
        tables = sum(1 for u in units if u.kind == "table")
        tables_preserved = sum(1 for r in results if r[4] == "preserved")
        tables_rebuilt = sum(1 for r in results if r[4] == "rebuilt_per_cell")
    else:
        wins = text_windows(content, max_chars)
        res = await asyncio.gather(*(translate_window(svc, w.text, target, script) for w in wins))
        output = join_windows(wins, [r[0] for r in res])
        tokens = sum(r[1] for r in res)
        all_cached = all(r[2] for r in res)
        truncated_windows = sum(int(r[3]) for r in res)
        windows = len(wins)
        tables = tables_preserved = tables_rebuilt = 0

    if truncated_windows:
        warnings.append(f"{truncated_windows} window(s) were still truncated after re-splitting; output may be incomplete.")
    source_tables = table_structure(content)
    tables_match = table_structure(output) == source_tables
    if source_tables and not tables_match:
        warnings.append("The translation's tables differ in rows or columns from the source's.")
    expected = _expected_script(target, script)
    fraction = round(script_fraction(output, expected), 3) if expected else None
    if expected and fraction is not None and fraction < 0.6:
        warnings.append(f"Only {fraction:.0%} of the output is in the {expected} script expected for '{target}'.")

    suffix = ("md" if markdown else "txt")
    script_tag = "" if script == "native" else f".{script}"
    out_path = svc.store.output_path(stem, f"{target}{script_tag}.{suffix}",
                                     json.dumps([_digest(content), target, script, markdown, max_chars]))
    svc.store.write_text(out_path, output + "\n")
    return ToolResult(
        tool="translate_text",
        result_path=str(out_path),
        preview=output[:300],
        language=target,
        meta={
            "mode": "markdown" if markdown else "plain",
            "script": script,
            "windows": windows,
            "source_chars": len(content),
            "output_chars": len(output),
            "length_ratio": round(len(output) / max(1, len(content)), 3),
            "expected_script": expected,
            "target_script_fraction": fraction,
            "truncated_windows": truncated_windows,
            "tables": tables,
            "tables_preserved": tables_preserved,
            "tables_rebuilt_per_cell": tables_rebuilt,
            "table_structure_matches": tables_match if source_tables else None,
            "source_detected": {k: detected[k] for k in ("script", "best_guess", "confidence")},
            "output_tokens": tokens,
        },
        cost_inr=round(catalog.translate_cost(tokens), 5),
        cached=all_cached,
        warnings=warnings,
    )


async def transliterate_text(svc: Services, language: str, target_script: str, path: str | None = None,
                             text: str | None = None) -> ToolResult:
    lang = catalog.require_language(TRANSLITERATE, language)
    allowed = catalog.scripts(TRANSLITERATE) or ["roman", "native"]
    if target_script not in allowed:
        raise ToolFailure(Code.INVALID_ARGUMENT, f"Unknown target_script '{target_script}'.",
                          f"Use one of: {', '.join(allowed)}.")
    svc.settings.key_for(TRANSLITERATE)  # fail fast if the optional key is missing
    content, stem = svc.store.text_input(path, text)
    content = content.strip()
    if not content:
        raise ToolFailure(Code.EMPTY_TEXT, "There is no text to transliterate.")
    if len(content) > MAX_TEXT_CHARS:
        raise ToolFailure(Code.INPUT_TOO_LARGE, f"Text has {len(content)} characters; the limit is {MAX_TEXT_CHARS}.")
    wins = text_windows(content, 1500)
    res = await asyncio.gather(*(translate_window(svc, w.text, lang, target_script, model=TRANSLITERATE) for w in wins))
    output = join_windows(wins, [r[0] for r in res])
    tokens = sum(r[1] for r in res)
    expected = "Latin" if target_script == "roman" else LANGUAGE_SCRIPT.get(lang)
    fraction = round(script_fraction(output, expected), 3) if expected else None
    out_path = svc.store.output_path(stem, f"{lang}.{target_script}.txt", json.dumps([_digest(content), lang, target_script]))
    svc.store.write_text(out_path, output + "\n")
    warnings = []
    if any(r[3] for r in res):
        warnings.append("Some windows were truncated; output may be incomplete.")
    return ToolResult(
        tool="transliterate_text", result_path=str(out_path), preview=output[:300], language=lang,
        meta={"target_script": target_script, "windows": len(wins), "source_chars": len(content),
              "output_chars": len(output), "expected_script": expected, "target_script_fraction": fraction,
              "output_tokens": tokens},
        cost_inr=round(catalog.transliterate_cost(tokens), 5), cached=all(r[2] for r in res), warnings=warnings,
    )


async def read_text(svc: Services, path: str, max_chars: int = 12000, offset: int = 0) -> ToolResult:
    content, _ = svc.store.text_input(path, None)
    max_chars = max(100, min(int(max_chars), 50_000))
    offset = max(0, int(offset))
    chunk = content[offset:offset + max_chars]
    detected = detect_language(content)
    return ToolResult(
        tool="read_text", result_path=path, preview=chunk[:300], text=chunk, language=detected["best_guess"],
        meta={"total_chars": len(content), "offset": offset, "returned_chars": len(chunk),
              "truncated": offset + len(chunk) < len(content),
              "next_offset": offset + len(chunk) if offset + len(chunk) < len(content) else None,
              "detected": detected},
    )


async def detect_language_tool(svc: Services, path: str | None = None, text: str | None = None) -> ToolResult:
    content, _ = svc.store.text_input(path, text)
    detected = detect_language(content)
    warnings = []
    if detected["confidence"] == "low":
        warnings.append(f"The {detected['script']} script is shared by {', '.join(detected['candidates'])}; "
                        f"'{detected['best_guess']}' is a guess. State the language if you know it.")
    return ToolResult(tool="detect_language", result_path=path, preview=content[:120],
                      language=detected["best_guess"], meta=detected, warnings=warnings)


MAX_AUDIO_SECONDS = 2 * 60 * 60
MAX_SPEECH_CHARS = 20_000
SPEECH_CAP_SECONDS = 29.0  # indic-speak silently cuts audio at about 30 s


def _stat_key(p: Path) -> str:
    st = p.stat()
    return f"{p}:{st.st_size}:{st.st_mtime_ns}"


async def transcribe_audio(svc: Services, path: str, language: str | None = None,
                           max_segment_seconds: float = 25.0) -> ToolResult:
    src = svc.store.resolve_input(path)
    kind = classify(src)
    if kind not in ("audio", "video"):
        raise ToolFailure(Code.UNSUPPORTED_FORMAT, f"{src.name} is {kind}, not audio or video.",
                          "Use ocr_document for images/PDFs and read_text for text files.")
    lang = catalog.require_language(TRANSCRIBE, language) if language else None
    max_seg = max(3.0, min(float(max_segment_seconds), 28.0))

    info = await asyncio.to_thread(files.probe, src)
    if not info["has_audio"]:
        raise ToolFailure(Code.NO_AUDIO_TRACK, f"{src.name} has no audio track.", "Nothing to transcribe.")
    wav = await asyncio.to_thread(files.to_pcm_wav, src)
    duration = files.wav_duration(wav)
    if duration < 0.3:
        raise ToolFailure(Code.EMPTY_AUDIO, f"{src.name} has {duration:.2f}s of audio.", "Nothing to transcribe.")
    if duration > MAX_AUDIO_SECONDS:
        raise ToolFailure(Code.INPUT_TOO_LARGE, f"Audio is {duration / 60:.0f} minutes; the limit is 120.",
                          "Trim the audio first.")
    silences = await asyncio.to_thread(files.detect_silences, wav)
    chunks = files.plan_chunks(duration, silences, max_len=max_seg)

    async def one(index: int, start: float, end: float):
        piece = files.slice_wav(wav, start, end)
        try:
            text, cached, calls = await svc.client.transcribe(piece, lang)
            return {"index": index, "start": start, "end": end, "text": text.strip(), "cached": cached,
                    "calls": calls, "error": None}
        except ToolFailure as exc:
            return {"index": index, "start": start, "end": end, "text": "", "cached": False,
                    "calls": getattr(exc, "billed_calls", 0), "error": exc}

    results = await asyncio.gather(*(one(i, s, e) for i, (s, e) in enumerate(chunks)))
    failed = [r for r in results if r["error"]]
    if failed and len(failed) == len(results):
        err = failed[0]["error"]
        spent = catalog.transcribe_cost(sum((r["end"] - r["start"]) * r["calls"] for r in results))
        if spent:
            err.message += f" (Rs {spent:.4f} was spent on these attempts.)"
        raise err
    # Every upstream call is billed, including a retry after garbled output.
    billed_seconds = sum((r["end"] - r["start"]) * r["calls"] for r in results)
    full_text = " ".join(r["text"] for r in results if r["text"]).strip()
    segments = [{"index": r["index"], "start": r["start"], "end": r["end"], "text": r["text"],
                 **({"failed": r["error"].code} if r["error"] else {})} for r in results]

    key = json.dumps([_stat_key(src), lang, max_seg])
    stem = src.stem
    transcript_path = svc.store.output_path(stem, "transcript.txt", key)
    segments_path = svc.store.output_path(stem, "segments.json", key)
    svc.store.write_text(transcript_path, full_text + "\n")
    svc.store.write_json(segments_path, segments)
    detected = detect_language(full_text) if full_text else None
    warnings = [f"Segment {r['start']:.1f}-{r['end']:.1f}s failed: {r['error']}" for r in failed]
    if not full_text:
        warnings.append("No speech was recognised (silence, music, or an unsupported language).")
    return ToolResult(
        tool="transcribe_audio",
        result_path=str(transcript_path),
        preview=full_text[:300],
        language=lang or (detected or {}).get("best_guess"),
        meta={
            "duration_s": round(duration, 2),
            "segments": len(segments),
            "failed_segments": len(failed),
            "segments_path": str(segments_path),
            "max_segment_seconds": max_seg,
            "language_requested": lang,
            "detected": detected,
            "source_kind": kind,
        },
        cost_inr=round(catalog.transcribe_cost(billed_seconds), 5),
        cached=all(r["cached"] for r in results if not r["error"]),
        warnings=warnings,
    )


async def synthesize_speech(svc: Services, language: str, path: str | None = None, text: str | None = None,
                            gender: str | None = None, voice: str | None = None, style: str | None = None,
                            output_format: str = "mp3", max_chars_per_request: int = 200) -> ToolResult:
    lang = catalog.require_language(SPEAK, language)
    voice_name, voice_gender = catalog.pick_voice(lang, gender, voice)
    style_name = catalog.check_style(style)
    if output_format not in ("mp3", "wav"):
        raise ToolFailure(Code.INVALID_ARGUMENT, f"output_format must be mp3 or wav, not '{output_format}'.")
    content, stem = svc.store.text_input(path, text)
    if path and path.lower().endswith((".md", ".markdown")):
        content = strip_markdown_for_speech(content)
    content = " ".join(content.split())
    if not content:
        raise ToolFailure(Code.EMPTY_TEXT, "There is no text to speak.")
    if len(content) > MAX_SPEECH_CHARS:
        raise ToolFailure(Code.INPUT_TOO_LARGE,
                          f"Text has {len(content)} characters (about Rs {catalog.speak_cost(len(content)):.2f}); "
                          f"the limit is {MAX_SPEECH_CHARS}.", "Summarise or split the text first.")
    expected_script = LANGUAGE_SCRIPT.get(lang, "Latin")
    fraction = script_fraction(content, expected_script)
    if fraction < 0.5:
        found = script_counts(content).most_common(1)
        found_script = found[0][0] if found else "no"
        raise ToolFailure(Code.TEXT_LANGUAGE_MISMATCH,
                          f"The text is mostly {found_script} script, but '{lang}' is written in {expected_script}.",
                          f"Translate it first with translate_text(target_language='{lang}') and pass the output path.")
    max_chars = max(60, min(int(max_chars_per_request), 250))
    pieces = pack(split_sentences(content), max_chars)
    warnings: list[str] = []
    billed = {"chars": 0}
    flags = {"cached": True}

    async def synth(piece: str, depth: int = 0) -> list[bytes]:
        audio, cached = await svc.client.speak(piece, voice_name, lang, style_name)
        if not cached:
            billed["chars"] += len(piece)
        flags["cached"] = flags["cached"] and cached
        seconds = files.wav_duration(audio)
        if seconds >= SPEECH_CAP_SECONDS and len(piece) > 40 and depth < 3:
            halves = await asyncio.gather(*(synth(h, depth + 1) for h in halve(piece)))
            return [clip for part in halves for clip in part]
        expected = len(piece) / catalog.SPEECH_CHARS_PER_SECOND
        if seconds < 0.35 * expected:
            warnings.append(f"Chunk of {len(piece)} chars produced only {seconds:.1f}s of audio (possible omission).")
        return [audio]

    clips_nested = await asyncio.gather(*(synth(p) for p in pieces))
    clips = [clip for group in clips_nested for clip in group]
    combined = await asyncio.to_thread(files.concat_wavs, clips)
    duration = files.wav_duration(combined)

    key = json.dumps([content, lang, voice_name, style_name, output_format, max_chars])
    out_path = svc.store.output_path(stem, f"{lang}.{voice_name}.{output_format}", key)
    spoken_path = svc.store.output_path(stem, f"{lang}.{voice_name}.spoken.txt", key)
    svc.store.write_text(spoken_path, content + "\n")
    label = f"AI-generated speech (Bodhan Indic-Speak, voice {voice_name})"
    await asyncio.to_thread(files.encode, combined, out_path, output_format,
                            {"title": f"AI-generated speech ({lang})", "artist": "AI-generated (Bodhan Indic-Speak)",
                             "comment": label})
    return ToolResult(
        tool="synthesize_speech",
        result_path=str(out_path),
        preview=f"{duration:.1f}s of {lang} speech, voice {voice_name} ({voice_gender}). {label}.",
        language=lang,
        meta={
            "duration_s": round(duration, 2),
            "expected_duration_s": round(len(content) / catalog.SPEECH_CHARS_PER_SECOND, 1),
            "voice": voice_name,
            "gender": voice_gender,
            "style": style_name,
            "requests": len(clips),
            "chars": len(content),
            "source_text_path": str(spoken_path),
            "format": output_format,
            "ai_generated": True,
            "label": label,
        },
        cost_inr=round(catalog.speak_cost(billed["chars"]), 5),
        cached=flags["cached"],
        warnings=warnings,
    )


def _srt_time(seconds: float) -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_cues(segments: list[dict], max_cue_chars: int = 84) -> list[tuple[float, float, str]]:
    """Split each timed segment into subtitle cues, sharing its time span in proportion to text length."""
    cues: list[tuple[float, float, str]] = []
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        start, end = float(seg["start"]), float(seg["end"])
        pieces = pack(split_sentences(text), max_cue_chars)
        total = sum(len(p) for p in pieces) or 1
        t = start
        for piece in pieces:
            span = (end - start) * len(piece) / total
            cues.append((t, t + span, piece))
            t += span
    return cues


def render_srt(cues: list[tuple[float, float, str]]) -> str:
    blocks = []
    for i, (start, end, text) in enumerate(cues, 1):
        lines = textwrap.wrap(text, width=42) or [text]
        blocks.append(f"{i}\n{_srt_time(start)} --> {_srt_time(end)}\n" + "\n".join(lines))
    return "\n\n".join(blocks) + "\n"


async def make_subtitles(svc: Services, segments_path: str, translate_to: str | None = None,
                         script: str = "native") -> ToolResult:
    src = svc.store.resolve_input(segments_path)
    try:
        segments = json.loads(svc.store.read_text(src))
        assert isinstance(segments, list)
        for seg in segments:
            float(seg["start"])
            float(seg["end"])
            seg.setdefault("text", "")
    except (ValueError, AssertionError, KeyError, TypeError):
        raise ToolFailure(Code.INVALID_ARGUMENT, f"{src.name} is not a segments file.",
                          "Pass meta.segments_path from a transcribe_audio result.") from None
    tokens = 0
    all_cached = True
    language = None
    if translate_to:
        language = catalog.require_language(TRANSLATE, translate_to, field="translate_to")
        if script not in catalog.scripts(TRANSLATE):
            raise ToolFailure(Code.INVALID_ARGUMENT, f"Unknown script '{script}'.")

        async def tr(seg: dict) -> dict:
            if not seg["text"].strip():
                return {**seg}
            out, t, cached, _ = await translate_window(svc, seg["text"], language, script)
            return {**seg, "text": out.strip(), "_tokens": t, "_cached": cached}

        translated = await asyncio.gather(*(tr(s) for s in segments))
        tokens = sum(s.get("_tokens", 0) for s in translated)
        all_cached = all(s.get("_cached", True) for s in translated)
        segments = [{k: v for k, v in s.items() if not k.startswith("_")} for s in translated]
    cues = build_cues(segments)
    if not cues:
        raise ToolFailure(Code.EMPTY_TEXT, "The segments contain no text, so there is nothing to subtitle.")
    srt = render_srt(cues)
    tag = language or (detect_language(" ".join(s["text"] for s in segments)).get("best_guess") or "und")
    out_path = svc.store.output_path(src.stem.split(".")[0], f"{tag}.srt",
                                     json.dumps([str(src), src.stat().st_mtime_ns, language, script]))
    svc.store.write_text(out_path, srt)
    return ToolResult(
        tool="make_subtitles", result_path=str(out_path), preview=srt[:300], language=tag,
        meta={"cues": len(cues), "segments": len(segments), "translated": bool(language),
              "translated_segments": sum(1 for s in segments if s["text"]) if language else 0,
              "duration_s": round(max(c[1] for c in cues), 2), "output_tokens": tokens},
        cost_inr=round(catalog.translate_cost(tokens), 5), cached=all_cached,
    )


MAX_PAGES = 60


async def ocr_document(svc: Services, path: str, pages: str | None = None,
                       table_format: str = "markdown") -> ToolResult:
    src = svc.store.resolve_input(path)
    kind = classify(src)
    if kind not in ("pdf", "image"):
        raise ToolFailure(Code.UNSUPPORTED_FORMAT, f"{src.name} is {kind}, not an image or PDF.",
                          "Use read_text for text files and transcribe_audio for audio/video.")
    if table_format not in ("markdown", "html"):
        raise ToolFailure(Code.INVALID_ARGUMENT, "table_format must be 'markdown' or 'html'.")
    if kind == "pdf":
        total = await asyncio.to_thread(files.pdf_page_count, src)
        wanted = files.parse_pages(pages, total)
        if len(wanted) > MAX_PAGES:
            raise ToolFailure(Code.INPUT_TOO_LARGE, f"{len(wanted)} pages requested; the limit per call is {MAX_PAGES}.",
                              "Pass `pages`, e.g. '1-30', and call again for the rest.")
        images = await asyncio.to_thread(files.render_pdf_pages, src, wanted)
    else:
        total = 1
        data, mime, _ = await asyncio.to_thread(files.prepare_image, src)
        images = [(1, data, mime)]

    async def one(number: int, data: bytes, mime: str):
        try:
            res = await svc.client.ocr(data, mime, table_format)
            return {"page": number, "markdown": res.content.strip(), "blocks": res.extra.get("blocks") or [],
                    "cached": res.cached, "error": None}
        except ToolFailure as exc:
            return {"page": number, "markdown": "", "blocks": [], "cached": False, "error": exc}

    results = await asyncio.gather(*(one(*img) for img in images))
    failed = [r for r in results if r["error"]]
    if failed and len(failed) == len(results):
        raise failed[0]["error"]
    parts = []
    for r in results:
        header = f"<!-- page {r['page']} -->\n\n" if len(results) > 1 else ""
        body = r["markdown"] if not r["error"] else f"<!-- OCR failed for page {r['page']}: {r['error'].code} -->"
        parts.append(header + body)
    markdown = "\n\n".join(parts).strip()
    key = json.dumps([str(src), src.stat().st_mtime_ns, pages, table_format])
    out_path = svc.store.output_path(src.stem, "ocr.md", key)
    blocks_path = svc.store.output_path(src.stem, "ocr_blocks.json", key)
    svc.store.write_text(out_path, markdown + "\n")
    svc.store.write_json(blocks_path, [{"page": r["page"], "blocks": r["blocks"]} for r in results])
    billed_pages = sum(1 for r in results if not r["cached"] and not r["error"])
    detected = detect_language(markdown) if markdown else None
    warnings = [f"Page {r['page']} failed: {r['error']}" for r in failed]
    if not markdown.replace("<!--", "").strip():
        warnings.append("No text was found on the page(s).")
    return ToolResult(
        tool="ocr_document", result_path=str(out_path), preview=markdown[:300],
        language=(detected or {}).get("best_guess"),
        meta={"pages_total": total, "pages_processed": len(results), "pages_failed": len(failed),
              "tables": sum(1 for r in results for b in r["blocks"] if str(b.get("label", "")).lower() == "table"),
              "blocks_path": str(blocks_path), "detected": detected, "chars": len(markdown)},
        cost_inr=round(catalog.ocr_cost(billed_pages), 5),
        cached=all(r["cached"] for r in results if not r["error"]),
        warnings=warnings,
    )


async def inspect_media(svc: Services, path: str) -> ToolResult:
    src = svc.store.resolve_input(path)
    kind = classify(src)
    meta: dict = {"kind": kind, "name": src.name, "size_bytes": src.stat().st_size}
    warnings: list[str] = []
    preview = ""
    if kind in ("audio", "video"):
        try:
            meta.update(await asyncio.to_thread(files.probe, src))
        except ToolFailure as exc:
            warnings.append(str(exc))
    elif kind == "image":
        meta.update(files.image_info(src))
    elif kind == "pdf":
        meta["pages"] = await asyncio.to_thread(files.pdf_page_count, src)
    elif kind == "text":
        content = svc.store.read_text(src)
        meta.update({"chars": len(content), "lines": content.count("\n") + 1,
                     "markdown": looks_like_markdown(content), "detected": detect_language(content)})
        preview = content[:300]
    else:
        warnings.append("Unrecognised file type. Supported: audio, video, images, PDF and UTF-8 text.")
    return ToolResult(tool="inspect_media", result_path=str(src), preview=preview,
                      language=(meta.get("detected") or {}).get("best_guess"), meta=meta, warnings=warnings)


OPERATIONS = ("transcribe", "translate", "transliterate", "speak", "ocr", "subtitles")


async def estimate_cost(svc: Services, operation: str, path: str | None = None, chars: int | None = None,
                        seconds: float | None = None, pages: int | None = None,
                        include_verification: bool = False) -> ToolResult:
    if operation not in OPERATIONS:
        raise ToolFailure(Code.INVALID_ARGUMENT, f"Unknown operation '{operation}'.", f"Use one of: {', '.join(OPERATIONS)}.")
    basis = {"chars": int(chars or 0), "seconds": float(seconds or 0), "pages": int(pages or 0)}
    if path:
        src = svc.store.resolve_input(path)
        kind = classify(src)
        if kind in ("audio", "video"):
            basis["seconds"] = (await asyncio.to_thread(files.probe, src))["duration_s"]
        elif kind == "pdf":
            basis["pages"] = await asyncio.to_thread(files.pdf_page_count, src)
        elif kind == "image":
            basis["pages"] = 1
        elif kind == "text":
            content = svc.store.read_text(src)
            if src.suffix.lower() == ".json" and operation == "subtitles":
                try:
                    content = " ".join(s.get("text", "") for s in json.loads(content))
                except (ValueError, AttributeError):
                    pass
            if operation == "speak" and src.suffix.lower() in (".md", ".markdown"):
                content = strip_markdown_for_speech(content)
            basis["chars"] = len(" ".join(content.split()))
    if not any(basis.values()):
        raise ToolFailure(Code.INVALID_ARGUMENT, "Nothing to estimate.", "Pass `path`, or `chars`/`seconds`/`pages`.")
    value = catalog.estimate(operation, chars=basis["chars"], seconds=basis["seconds"], pages=basis["pages"],
                             include_verification=include_verification)
    return ToolResult(tool="estimate_cost", result_path=path, preview=f"Upper-bound estimate: Rs {value:.4f}",
                      meta={"operation": operation, "estimate_inr": round(value, 5), "basis": basis,
                            "includes_verification": include_verification,
                            "rates": {"transcribe_per_min": catalog.TRANSCRIBE_PER_MINUTE,
                                      "translate_per_10k_output_tokens": catalog.TRANSLATE_PER_10K_OUTPUT_TOKENS,
                                      "speak_per_10k_chars": catalog.SPEAK_PER_10K_CHARS,
                                      "ocr_per_page": catalog.OCR_PER_IMAGE}})
