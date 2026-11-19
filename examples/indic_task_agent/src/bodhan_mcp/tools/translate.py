"""translate_text: structure-preserving translation through Indic-Translate.

Inputs: inline text, a text/Markdown/.docx file, a segments JSON from transcribe_audio, or an
SRT/VTT file. Markdown is parsed so that only content is translated: table pipes, heading marks,
list markers and code stay literal, and each table cell is translated on its own, so tables
survive by construction. Long paragraphs are split at sentence boundaries to stay inside the
model's context window; a truncated response is retried as two smaller pieces.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from ..catalog import TRANSLATE_LANGS, catalog, require_language
from ..errors import BodhanToolError
from ..media import read_text_file, resolve_input
from ..models import TranslateResult
from ..text import (
    LANGUAGE_SCRIPT,
    Segment,
    chunk_for_translation,
    detect_script,
    has_repetition,
    length_ratio,
    needs_translation,
    parse_markdown,
    render_markdown,
    script_share,
    split_in_two,
    table_shape,
)
from ..usage import estimate_translate_tokens, translate_cost
from .common import get_context, new_ledger, output_dir, output_path, preview, report_spend_on_error, write_text

MAX_INPUT_CHARS = 400_000
UNIQUE_SCRIPT_TARGETS = {"ta", "te", "kn", "ml", "gu", "pa", "or", "sat", "mni"}
_SRT_TIME = re.compile(r"(\d+:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d+:\d{2}:\d{2}[,.]\d{3})")


class _Translator:
    def __init__(self, target: str, script: str, ledger) -> None:
        self.target, self.script, self.ledger = target, script, ledger
        self.client = get_context().client
        self.expected_script = "Latin" if (target == "en" or script == "roman") else LANGUAGE_SCRIPT.get(target)
        self.warnings: list[str] = []
        self.truncated_left = 0
        self.resplit = 0

    def already_target(self, text: str) -> bool:
        if self.script != "native":
            return False
        if self.target == "en":
            return script_share(text, "Latin") >= 0.95
        if self.target in UNIQUE_SCRIPT_TARGETS and self.expected_script:
            return script_share(text, self.expected_script) >= 0.85
        return False

    async def _piece(self, text: str, depth: int = 0) -> str:
        res = await self.client.translate(text, target=self.target, script=self.script, ledger=self.ledger)
        out = str(res.get("translation") or "").strip()
        if res.get("truncated"):
            halves = split_in_two(text)
            if depth < 3 and len(halves) == 2:
                self.resplit += 1
                parts = await asyncio.gather(*(self._piece(h, depth + 1) for h in halves))
                return " ".join(p for p in parts if p)
            self.truncated_left += 1
            self.warnings.append(
                f"Translation still truncated for a {len(text)}-char span that cannot be split further."
            )
        return out

    async def unit(self, text: str) -> str:
        pieces = chunk_for_translation(text)
        outs = await asyncio.gather(*(self._piece(p) for p in pieces))
        return " ".join(o for o in outs if o)


def _parse_srt(raw: str) -> list[dict]:
    cues = []
    for block in re.split(r"\n\s*\n", raw.replace("\r\n", "\n").strip()):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        for idx, ln in enumerate(lines):
            m = _SRT_TIME.search(ln)
            if m:
                cues.append({"start": m.group(1), "end": m.group(2), "text": " ".join(lines[idx + 1 :]).strip()})
                break
    return cues


def _srt_ts(value: str) -> str:
    hours, rest = value.replace(".", ",").split(":", 1)
    return f"{int(hours):02d}:{rest}"


@report_spend_on_error
async def translate_text(
    target_language: str,
    text: str | None = None,
    input_path: str | None = None,
    script: str = "native",
    output_dir_: str | None = None,
    max_cost_inr: float | None = None,
) -> TranslateResult:
    target = require_language(target_language, TRANSLATE_LANGS, "translation")
    if script not in {"native", "roman", "codemix"}:
        raise BodhanToolError("invalid_input", f"script must be native, roman or codemix, got {script!r}.")
    if script == "codemix" and target in set(catalog()["translation"]["codemix_unavailable_for"]):
        raise BodhanToolError(
            "unsupported_language", f"codemix script is not available for {target}.", hint="Use script='native'."
        )
    if bool(text) == bool(input_path):
        raise BodhanToolError("invalid_input", "Pass exactly one of `text` or `input_path`.")

    mode = "markdown"
    stem = "text"
    if input_path:
        src = resolve_input(input_path)
        stem = src.stem
        ext = src.suffix.lower()
        if ext in {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}:
            raise BodhanToolError(
                "unsupported_file_type",
                f"{src.name} is a document image; extract its text first.",
                hint="Call ocr_document on it, then translate the output_path it returns.",
            )
        if ext in {".wav", ".mp3", ".mp4", ".m4a", ".ogg", ".flac", ".mkv", ".mov", ".webm", ".aac"}:
            raise BodhanToolError(
                "unsupported_file_type",
                f"{src.name} is audio/video; transcribe it first.",
                hint="Call transcribe_audio, then translate its transcript_path or segments_path.",
            )
        raw = read_text_file(src)
        if ext == ".json":
            mode = "segments"
        elif ext in {".srt", ".vtt"}:
            mode = "srt"
    else:
        raw = text or ""
    if not raw.strip():
        raise BodhanToolError("invalid_input", "The input text is empty.")
    if len(raw) > MAX_INPUT_CHARS:
        raise BodhanToolError(
            "invalid_input",
            f"Input is {len(raw)} characters; the limit is {MAX_INPUT_CHARS}.",
            hint="Split the document.",
        )

    ledger = new_ledger(max_cost_inr)
    tr = _Translator(target, script, ledger)

    # Build the list of spans to translate for each mode.
    if mode == "segments":
        try:
            doc = json.loads(raw)
            segments = doc["segments"] if isinstance(doc, dict) else doc
            texts = [str(s.get("text", "")) for s in segments]
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise BodhanToolError(
                "invalid_input",
                "JSON input must be a segments file from transcribe_audio ({segments: [{start, end, text}]}).",
            ) from exc
        spans = [Segment(t, needs_translation(t)) for t in texts]
    elif mode == "srt":
        cues = _parse_srt(raw)
        if not cues:
            raise BodhanToolError("invalid_input", "No subtitle cues found in the file.")
        spans = [Segment(c["text"], needs_translation(c["text"])) for c in cues]
    else:
        lines = parse_markdown(raw)
        spans = [seg for line in lines for seg in line.parts]

    todo = [s for s in spans if s.translate]
    kept = [s for s in todo if tr.already_target(s.text)]
    for s in kept:
        s.translate = False
    todo = [s for s in todo if s.translate]
    unique = list(dict.fromkeys(s.text for s in todo))
    ledger.reserve(
        sum(translate_cost(estimate_translate_tokens(t)) for t in unique),
        f"Translating {sum(map(len, unique))} characters",
    )

    results = await asyncio.gather(*(tr.unit(t) for t in unique), return_exceptions=True)
    translated: dict[str, str] = {}
    failures: list[BodhanToolError] = []
    for src_text, res in zip(unique, results, strict=True):
        if isinstance(res, BodhanToolError):
            failures.append(res)
        elif isinstance(res, BaseException):
            raise res
        else:
            translated[src_text] = res
    if failures and not translated:
        raise failures[0]

    empty = 0
    out_fragments: list[str] = []
    for s in todo:
        out = " ".join(translated.get(s.text, "").split())  # one line per span keeps tables and cues intact
        if not out:
            empty += 1
            continue
        out_fragments.append(out)
        ratio = length_ratio(s.text, out)
        if len(s.text) > 40 and (ratio < 0.25 or ratio > 4.0):
            tr.warnings.append(f"Suspicious length ratio {ratio:.2f} for span starting {s.text[:30]!r}.")
        if has_repetition(out) and not has_repetition(s.text):
            tr.warnings.append(f"Repeated text in translation of span starting {s.text[:30]!r}.")
        s.text = out
    if failures:
        tr.warnings.append(
            f"{len(failures)} span(s) failed after retries ({failures[0].code}); they are left in the source language."
        )

    out_dir = output_dir(output_dir_)
    fp = {"src": raw[:2000], "len": len(raw), "target": target, "script": script, "mode": mode}
    structure_ok = True
    if mode == "segments":
        new_segments = []
        for seg, span in zip(segments, spans, strict=True):
            new_segments.append({**seg, "source_text": seg.get("text", ""), "text": span.text})
        result_text = "\n".join(s["text"] for s in new_segments if s["text"])
        path = write_text(
            output_path(out_dir, stem, f"{target}", "json", fp),
            json.dumps({"language": target, "segments": new_segments}, ensure_ascii=False, indent=1),
        )
    elif mode == "srt":
        blocks = [
            f"{i}\n{_srt_ts(c['start'])} --> {_srt_ts(c['end'])}\n{span.text}"
            for i, (c, span) in enumerate(zip(cues, spans, strict=True), 1)
        ]
        result_text = "\n\n".join(blocks) + "\n"
        path = write_text(output_path(out_dir, stem, f"{target}", "srt", fp), result_text)
    else:
        result_text = render_markdown(lines)
        structure_ok = table_shape(raw) == table_shape(result_text) and len(raw.split("\n")) >= len(
            result_text.split("\n")
        )
        ext = (
            "md"
            if (
                input_path is None
                or Path(input_path).suffix.lower() in {".md", ".markdown"}
                or "|" in raw
                or "#" in raw
            )
            else "txt"
        )
        path = write_text(output_path(out_dir, stem, f"{target}", ext, fp), result_text.rstrip("\n") + "\n")

    share = script_share(" ".join(out_fragments), tr.expected_script) if (tr.expected_script and out_fragments) else 1.0
    if out_fragments and share < 0.6:
        tr.warnings.append(
            f"Only {share:.0%} of output letters are in {tr.expected_script} script; the translation may be wrong or partial."
        )
    coverage_ok = empty == 0 and tr.truncated_left == 0 and not failures
    return TranslateResult(
        output_path=str(path),
        preview=preview(result_text),
        target_language=target,
        script=script,
        source_script=detect_script(raw).dominant,
        units_total=len(todo) + len(kept),
        units_translated=len(todo) - empty,
        units_kept=len(kept),
        coverage_ok=coverage_ok,
        target_script_share=round(share, 3),
        structure_preserved=structure_ok,
        warnings=tr.warnings
        + ([f"{tr.resplit} truncated span(s) were re-translated in smaller pieces."] if tr.resplit else []),
        usage=ledger.usage(),
    )
