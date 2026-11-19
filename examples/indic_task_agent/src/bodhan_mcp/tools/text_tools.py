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
    LANGUAGE_NAMES,
    SPEAK_LANGUAGES,
    SPEAK_STYLES,
    TRANSCRIBE_LANGUAGES,
    TRANSLATE_LANGUAGES,
    VOICES,
    detect_language,
    indic_fraction,
    normalize_language,
)
from ..runtime import Artifact, RunContext
from ..textutil import preview
from .common import frame_untrusted, output_name, resolve_text
from .subtitles import parse_srt, render_srt
from .translation import check_target, detect_source, translate_markdown, verify_units


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
    source, src_label = resolve_text(ctx, a.text, a.path)
    src_lang, detection = detect_source(source, a.source_language)
    is_srt = src_label.lower().endswith(".srt")

    if src_lang == target and a.script == "native" and (detection.get("confidence", 1) or 0) >= 0.6 and not is_srt:
        dest = ctx.artifact_path(output_name(a.output_name, src_label, f"_{target}"), ".md")
        dest.write_text(source, encoding="utf-8")
        ctx.register(Artifact(dest, "translation", "translate_text", target, "copied (source already in target language)",
                              {"status": "pass", "method": "no translation needed", "problems": [], "warnings": []}))
        return {"path": str(dest), "target_language": target, "skipped": True,
                "note": f"Source is already {LANGUAGE_NAMES.get(target, target)}; copied without calling the model.",
                "source_detection": detection}

    if is_srt:
        cues = parse_srt(source)
        if not cues:
            raise E.ToolError(E.INVALID_ARGUMENT, f"{src_label} has no subtitle cues.")
        import asyncio

        from .translation import _translate_piece  # cue-by-cue keeps timing aligned

        outs = await asyncio.gather(*[_translate_piece(ctx, c["text"], target, a.script) for c in cues])
        new_cues = [{**c, "text": o} for c, (o, _) in zip(cues, outs)]
        output = render_srt(new_cues)
        from .translation import Unit

        units = [Unit("text", c["text"], o) for c, (o, _) in zip(cues, outs)]
        suffix = ".srt"
    else:
        output, units = await translate_markdown(ctx, source, target, a.script)
        suffix = ".md"

    verification = None
    if a.verify:
        verification = await verify_units(ctx, units, source, output, target, a.script, src_lang)
        ctx.emit({"type": "verification", "tool": "translate_text", "status": verification["status"],
                  "problems": verification["problems"], "warnings": verification["warnings"]})

    dest = ctx.artifact_path(output_name(a.output_name, src_label, f"_{target}"), suffix)
    dest.write_text(output, encoding="utf-8")
    notes = [n for u in units for n in u.notes]
    art = ctx.register(Artifact(dest, "subtitles" if is_srt else "translation", "translate_text", target, "indic-translate", verification, notes))
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
    source, _ = resolve_text(ctx, a.source_text, a.source_path, "source_text")
    trans_path = ctx.resolve_input(a.translation_path)
    output = read_text_file(trans_path)
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
    if key in ctx.artifacts:
        ctx.artifacts[key].verification = verification
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
    chunk = content[a.offset : a.offset + a.max_chars]
    framed = frame_untrusted(chunk, str(p), a.max_chars + 50)
    framed["text"] = framed.pop("text_preview")
    return {"path": str(p), "total_chars": len(content), "offset": a.offset, "returned_chars": len(chunk),
            "next_offset": a.offset + len(chunk) if a.offset + len(chunk) < len(content) else None,
            "language": detect_language(chunk).as_dict(), **framed}


# ---------------------------------------------------------------------------
class WriteTextIn(_Strict):
    name: str = Field(description="File name for the artifact, e.g. 'summary_en.md' or 'telugu_clips.md'.")
    content: str = Field(description="English text or Markdown to save. May embed earlier artifacts with {{artifact:<path>}}, which is replaced by that file's content.")
    language: str = Field("en", description="Language of `content` you wrote. Must be 'en': write English and use translate_text for Indian languages.")


_EMBED = re.compile(r"\{\{artifact:([^}]+)\}\}")


async def write_text(ctx: RunContext, a: WriteTextIn) -> dict:
    if normalize_language(a.language) != "en":
        raise E.ToolError(E.PROVENANCE_VIOLATION, "write_text only saves English text written by the caller.",
                          hint="Write the text in English and call translate_text to produce Indian-language output.")
    authored = _EMBED.sub("", a.content)
    if indic_fraction(authored) > 0.02:
        raise E.ToolError(E.PROVENANCE_VIOLATION,
                          "The content contains Indian-language text written by the caller. Indian-language text must come from a Bodhan model.",
                          hint="Write English and call translate_text, or embed an existing artifact with {{artifact:<path>}}.")

    embedded: list[str] = []

    def expand(m: re.Match) -> str:
        p = ctx.resolve_input(m.group(1).strip())
        embedded.append(str(p))
        return read_text_file(p).strip()

    final = _EMBED.sub(expand, a.content)
    suffix = ".json" if a.name.lower().endswith(".json") else (".txt" if a.name.lower().endswith(".txt") else ".md")
    if suffix == ".json":
        try:
            json.loads(final)
        except json.JSONDecodeError as exc:
            raise E.ToolError(E.INVALID_ARGUMENT, f"content is not valid JSON: {exc}") from exc
    dest = ctx.artifact_path(a.name, suffix)
    dest.write_text(final, encoding="utf-8")
    ctx.register(Artifact(dest, "text", "write_text", "en", "planner (English)" + (" + embedded artifacts" if embedded else ""), None))
    return {"path": str(dest), "chars": len(final), "embedded_artifacts": embedded}


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
