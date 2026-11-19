"""Text tools: translate_text, verify_translation, detect_language, read_text, write_text, list_voices."""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .. import errors as E
from ..documents import read_text_file
from ..languages import (
    ENGLISH_DEFAULT_VOICES,
    KNOWN_UNSUPPORTED,
    LANGUAGE_NAMES,
    SPEAK_LANGUAGES,
    SPEAK_STYLES,
    TRANSCRIBE_LANGUAGES,
    TRANSLATE_LANGUAGES,
    VOICES,
    detect_language,
    normalize_language,
)
from ..runtime import Artifact, RunContext, settle, sha256_bytes
from ..textutil import preview
from .common import authored_indic, frame_untrusted, output_name, quoted_from, resolve_text
from .subtitles import parse_srt, render_srt
from .translation import check_source, check_target, detect_source, translate_markdown, verify_units


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
class TranslateIn(_Strict):
    target_language: str = Field(description="ISO code of the target language, e.g. 'ta', 'hi', 'en', 'bn', 'mr'. English names like 'Tamil' are accepted.")
    path: str | None = Field(None, description="Path of a text/Markdown/SRT file or an earlier tool artifact to translate. Preferred over `text`.")
    text: str | None = Field(None, description="Short inline text to translate (use `path` for anything long).")
    source_language: str = Field("auto", description="Source language code if known; 'auto' detects it from the script.")
    script: Literal["native", "roman", "codemix"] = Field("native", description="Output script: native script, romanized, or code-mixed. Use 'native' unless asked.")
    verify: bool = Field(True, description="Back-translate and run structure checks before returning (recommended; costs a little extra).")
    output_name: str | None = Field(None, description="File name (without folder) for the translation artifact.")


async def translate_text(ctx: RunContext, a: TranslateIn) -> dict:
    target = check_target(a.target_language, script=a.script)
    check_source(a.source_language)
    source, src_label = resolve_text(ctx, a.text, a.path)
    src_lang, detection = detect_source(source, a.source_language)
    is_srt = src_label.lower().endswith(".srt")
    if src_lang in KNOWN_UNSUPPORTED:  # e.g. French function words: say so rather than translate it anyway
        raise E.ToolError(E.UNSUPPORTED_LANGUAGE, f"The source text appears to be {KNOWN_UNSUPPORTED[src_lang]}, which Indic-Translate does not support.",
                          hint="Report the source language as unsupported; do not guess a translation.", details={"source_detection": detection})
    if src_lang is None and detection.get("script") and (detection.get("confidence") or 0) >= 0.5:
        raise E.ToolError(E.UNSUPPORTED_LANGUAGE,
                          f"The source text is written in {detection['script']} script, which no Bodhan-supported language uses, "
                          "so Indic-Translate cannot translate it.",
                          hint="Report the source language as unsupported; do not guess a translation.", details={"source_detection": detection})

    # No whole-document shortcut: each paragraph is checked on its own (translation.plan_units), so a
    # minority-language paragraph inside a mostly-target-language document is still translated.
    if is_srt:
        cues = parse_srt(source)
        if not cues:
            raise E.ToolError(E.INVALID_ARGUMENT, f"{src_label} has no subtitle cues.")

        from .translation import _translate_piece  # cue-by-cue keeps timing aligned

        outs = await settle(*[_translate_piece(ctx, c["text"], target, a.script) for c in cues])
        new_cues = [{**c, "text": o} for c, (o, _) in zip(cues, outs)]
        output = render_srt(new_cues)
        from .translation import Unit

        units = [Unit("text", c["text"], o) for c, (o, _) in zip(cues, outs)]
        suffix = ".srt"
    else:
        output, units = await translate_markdown(ctx, source, target, a.script, normalize_language(a.source_language) if a.source_language != "auto" else None)
        suffix = ".md"

    verification = None
    if a.verify:
        verification = await verify_units(ctx, units, source, output, target, a.script, src_lang)
        ctx.emit({"type": "verification", "tool": "translate_text", "status": verification["status"],
                  "problems": verification["problems"], "warnings": verification["warnings"]})

    dest = ctx.artifact_path(output_name(a.output_name, src_label, f"_{target}"), suffix)
    dest.write_text(output, encoding="utf-8")
    notes = [n for u in units for n in u.notes]
    lineage = src_label if src_label != "inline" else (quoted_from(ctx, source) or "inline")  # text copied from an artifact keeps its origin
    deps = [lineage] if lineage in ctx.artifacts else []
    art = ctx.register(Artifact(dest, "subtitles" if is_srt else "translation", "translate_text", target, "indic-translate", verification, notes,
                                depends_on=deps, source_digest=sha256_bytes(source.encode("utf-8")), source_language=src_lang,
                                origin=ctx.origin_of(lineage)))
    if ctx.provenance_guard is not None:
        ctx.provenance_guard.add(output)
    result = {
        "path": str(dest), "target_language": target, "script": a.script, "source_detection": detection,
        "source_chars": len(source), "output_chars": len(output), "passages": len(units),
        "model_calls": sum(u.calls for u in units), "verification": verification, "notes": art.notes,
        "translation": frame_untrusted(output, str(dest), 500),
    }
    if target == "en":
        result["english_preview"] = preview(output, 1200)
    elif verification and src_lang == "en":
        backs = [u.back for u in units if u.back]
        if backs:
            result["back_translation_english_preview"] = preview("\n".join(backs), 1200)
    return result


# ---------------------------------------------------------------------------
class VerifyTranslationIn(_Strict):
    translation_path: str = Field(description="Path of the translated text/Markdown file to check.")
    target_language: str = Field(description="Language code the translation is supposed to be in.")
    source_path: str | None = Field(None, description="Path of the original text. Provide this or `source_text`.")
    source_text: str | None = Field(None, description="Original text inline (short texts only).")
    source_language: str = Field("auto", description="Source language code, or 'auto'.")


async def verify_translation(ctx: RunContext, a: VerifyTranslationIn) -> dict:
    target = check_target(a.target_language)
    check_source(a.source_language)
    source, _ = resolve_text(ctx, a.source_text, a.source_path, "source_text")
    trans_path = ctx.resolve_input(a.translation_path)
    output = read_text_file(trans_path)
    art = ctx.artifacts.get(str(trans_path))
    if art is not None and art.source_digest:
        # A translation made in this session is always checked against what it was made from:
        # checking it against itself or another text could relabel a failed translation as passed.
        if sha256_bytes(source.encode("utf-8")) != art.source_digest:
            raise E.ToolError(E.INVALID_ARGUMENT, f"{trans_path.name} was not translated from this source text.",
                              hint="Pass the original source the translation was made from, or translate again to repair it.")
        if art.language and target != art.language:
            raise E.ToolError(E.INVALID_ARGUMENT, f"{trans_path.name} is a {art.language} translation, not {target}.")
        if art.source_language:  # the language recorded when it was made, not a caller-supplied hint
            src_lang, detection = art.source_language, {"language": art.source_language, "source": "recorded at translation time"}
    if not (art is not None and art.source_language):
        src_lang, detection = detect_source(source, a.source_language)
    from .translation import plan_units

    src_units = [u for u, _ in plan_units(source)]
    out_units = [u for u, _ in plan_units(output)]
    if len(src_units) == len(out_units):
        for s, o in zip(src_units, out_units):
            s.output = o.source
        units = src_units
    else:  # layouts differ: compare as one passage
        from .translation import Unit

        units = [Unit("text", source, output)]
    verification = await verify_units(ctx, units, source, output, target, "native", src_lang)
    key = str(trans_path)
    if art is not None:
        # history is kept; the effective status never improves on an earlier failure
        verification = {**verification, "effective_status": art.record_verification(verification)["status"]}
    return {"translation_path": key, "source_detection": detection, "verification": verification}


# ---------------------------------------------------------------------------
class DetectLanguageIn(_Strict):
    path: str | None = Field(None, description="Text file or artifact to inspect.")
    text: str | None = Field(None, description="Inline text to inspect.")


async def detect_language_tool(ctx: RunContext, a: DetectLanguageIn) -> dict:
    if bool(a.text) == bool(a.path):
        raise E.ToolError(E.INVALID_ARGUMENT, "Provide exactly one of `text` or `path`.")
    text = a.text if a.text else read_text_file(ctx.resolve_input(a.path or ""))
    guess = detect_language(text)
    return {"detection": guess.as_dict(), "method": "Unicode script + function-word heuristics (no model call, free)",
            "supported_by": {"transcribe": guess.language in TRANSCRIBE_LANGUAGES, "translate": guess.language in TRANSLATE_LANGUAGES,
                             "speak": guess.language in SPEAK_LANGUAGES} if guess.language else None}


# ---------------------------------------------------------------------------
class ReadTextIn(_Strict):
    path: str = Field(description="Text, Markdown, SRT or JSON file (input or artifact) to read.")
    offset: int = Field(0, ge=0, description="Character offset to start from (for paging through long files).")
    max_chars: int = Field(6000, ge=200, le=20000, description="Maximum characters to return.")


async def read_text(ctx: RunContext, a: ReadTextIn) -> dict:
    p = ctx.resolve_input(a.path)
    from ..documents import classify

    kind = classify(p)
    if kind != "text":
        raise E.ToolError(E.UNSUPPORTED_FILE_TYPE, f"{p.name} is a {kind} file; read_text only reads text.",
                          hint="Use ocr_document for images/PDFs and transcribe_audio for audio/video.")
    content = read_text_file(p)
    if ctx.provenance_guard is not None:
        ctx.provenance_guard.add(content)
    size = a.max_chars
    while True:
        chunk = content[a.offset : a.offset + size]
        framed = frame_untrusted(chunk, str(p), size + 50)
        framed.pop("text_preview")
        framed["text"] = chunk  # the exact slice: pages joined in order reproduce the file (a preview would trim their edges)
        out = {"path": str(p), "total_chars": len(content), "offset": a.offset, "returned_chars": len(chunk),
               "next_offset": a.offset + len(chunk) if a.offset + len(chunk) < len(content) else None,
               "language": detect_language(chunk).as_dict(), **framed}
        n = len(json.dumps(out, ensure_ascii=False, default=str))
        # shrink the page until the whole result reaches the reader (JSON escaping can make text longer than its length)
        if ctx.max_result_chars is None or n <= ctx.max_result_chars or size <= 200:
            break
        size = max(200, int(size * (ctx.max_result_chars / n) * 0.95))
    if size < a.max_chars:
        out["note"] = f"{out['note']} Page shortened to {len(chunk)} characters so it arrives whole; continue at next_offset."
    ctx.note_read(p, content, a.offset, a.offset + len(chunk))
    return out


# ---------------------------------------------------------------------------
class WriteTextIn(_Strict):
    name: str = Field(description="File name for the artifact, e.g. 'summary_en.md' or 'telugu_clips.md'.")
    content: str = Field(description="English text or Markdown to save. May embed earlier artifacts with {{artifact:<path>}}, which is replaced by that file's content.")
    language: str = Field("en", description="Language of `content` you wrote. Must be 'en': write English and use translate_text for Indian languages.")
    source_paths: list[str] = Field(default_factory=list, max_length=50,
                                    description="Files this text is based on (e.g. the English translation you summarised), each read in full "
                                                "with read_text first. Recorded as its sources, so outputs made from it stay linked to the original input.")


_EMBED = re.compile(r"\{\{artifact:([^}]+)\}\}")


async def write_text(ctx: RunContext, a: WriteTextIn) -> dict:
    if normalize_language(a.language) != "en":
        raise E.ToolError(E.PROVENANCE_VIOLATION, "write_text only saves English text written by the caller.",
                          hint="Write the text in English and call translate_text to produce Indian-language output.")
    if any(_EMBED.search(c) for c in re.findall(r"<!--.*?-->", a.content, re.S)):
        # embedded text must be visible to whoever reads the file, not tucked into a comment
        raise E.ToolError(E.INVALID_ARGUMENT, "{{artifact:...}} cannot be placed inside an HTML comment.",
                          hint="Put the embed where readers see it, or leave it out.")
    authored = _EMBED.sub("", a.content)
    if authored_indic(authored):  # also catches &#2351; style character references
        raise E.ToolError(E.PROVENANCE_VIOLATION,
                          "The content contains Indian-language text written by the caller. Indian-language text must come from a Bodhan model.",
                          hint="Write English and call translate_text, or embed an existing artifact with {{artifact:<path>}}.")

    embedded: list[str] = []

    def expand(m: re.Match) -> str:
        p = ctx.resolve_input(m.group(1).strip())
        embedded.append(str(p))
        return read_text_file(p).strip()

    final = _EMBED.sub(expand, a.content)
    sources = []
    for sp in a.source_paths:
        src = ctx.resolve_input(sp)  # must exist inside the sandbox, like any input
        if not ctx.fully_read(src, read_text_file(src)):
            # A source is recorded only for text the caller has actually seen, all of it: naming a file is not evidence.
            raise E.ToolError(E.INVALID_ARGUMENT, f"{src.name} has not been read in full with read_text, so it cannot be recorded as a source.",
                              hint="Page through it with read_text (follow next_offset to the end), then call write_text again.")
        sources.append(str(src))
    suffix = ".json" if a.name.lower().endswith(".json") else (".txt" if a.name.lower().endswith(".txt") else ".md")
    if suffix == ".json":
        if embedded:
            raise E.ToolError(E.INVALID_ARGUMENT, "Artifacts cannot be embedded into JSON; write Markdown (.md) instead.")
        try:
            decoded = json.dumps(json.loads(final), ensure_ascii=False)
        except json.JSONDecodeError as exc:
            raise E.ToolError(E.INVALID_ARGUMENT, f"content is not valid JSON: {exc}") from exc
        if authored_indic(decoded):  # unicode escapes decode to Indic text when the file is read back
            raise E.ToolError(E.PROVENANCE_VIOLATION, "The JSON contains Indian-language text (possibly as unicode escapes) written by the caller.",
                              hint="Write English and call translate_text; Indian-language text must come from a Bodhan model.")
    dest = ctx.artifact_path(a.name, suffix)
    dest.write_text(final, encoding="utf-8")
    ctx.register(Artifact(dest, "text", "write_text", "en", "planner (English)" + (" + embedded artifacts" if embedded else ""), None,
                          depends_on=sorted(set(embedded) | {s for s in sources if s in ctx.artifacts}),
                          origin=sorted({o for e in embedded + sources for o in ctx.origin_of(e)})))
    return {"path": str(dest), "chars": len(final), "embedded_artifacts": embedded, "source_paths": sources}


# ---------------------------------------------------------------------------
class ListVoicesIn(_Strict):
    language: str | None = Field(None, description="Optional language code to filter voices, e.g. 'bn'.")


async def list_voices(ctx: RunContext, a: ListVoicesIn) -> dict:
    if a.language:
        code = normalize_language(a.language)
        if code == "en":
            return {"language": "en", "voices": {"female": [ENGLISH_DEFAULT_VOICES["female"]], "male": [ENGLISH_DEFAULT_VOICES["male"]]},
                    "note": "No native English voice; any voice can read English. These defaults are used."}
        if code not in VOICES:
            raise E.ToolError(E.UNSUPPORTED_LANGUAGE, f"No Indic-Speak voices for {a.language!r}.",
                              hint=f"Speech languages: {', '.join(sorted(SPEAK_LANGUAGES))}.")
        return {"language": code, "language_name": LANGUAGE_NAMES[code], "voices": VOICES[code], "styles": sorted(SPEAK_STYLES)}
    return {"voices": VOICES, "styles": sorted(SPEAK_STYLES), "note": "Fixed voice library; no voice cloning. Names are case-sensitive."}
