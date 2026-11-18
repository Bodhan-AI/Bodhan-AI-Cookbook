"""translate_text / translate_segments via Indic-Translate, with structure kept and coverage checked.

Markdown is split into blocks. Headings, list markers and quotes keep their prefix outside the
translated text, code is left alone, and **tables are translated cell by cell and rebuilt in code**
(sent whole, the model drops the |---| row). Long paragraphs are split at sentence boundaries under
MAX_CHARS. Every unit is then checked: non-empty, sane length ratio, numbers kept, target script.
"""

from __future__ import annotations

import asyncio
import hashlib
import json

from bodhan_core import chunking, langs, workspace
from bodhan_core.errors import ErrorCode, ToolError
from bodhan_mcp.runtime import get_client

MAX_CHARS = 1500  # per request; well inside the context window and keeps truncation unlikely
INLINE_LIMIT = 2000  # return the translation inline when it is at most this long
MAX_INPUT_CHARS = 200_000


def _load_text(text: str | None, path: str | None) -> tuple[str, str]:
    if (text is None) == (path is None):
        raise ToolError(ErrorCode.INVALID_ARGUMENT, "Pass exactly one of `text` or `path`.")
    if path is not None:
        p = workspace.resolve_input(path)
        kind = workspace.media_kind(p)
        if kind != "text":
            raise ToolError(ErrorCode.UNSUPPORTED_MEDIA, f"translate_text reads text files, got {kind}.",
                            hint="Run ocr_document (images/PDFs) or transcribe_audio (audio) first, then pass the "
                                 "text_path/transcript_path it returns.")
        return workspace.read_text_file(p), str(p)
    if not text.strip():
        raise ToolError(ErrorCode.INVALID_ARGUMENT, "`text` is empty.")
    return text, "text-" + hashlib.sha256(text.encode()).hexdigest()[:10]


def _script_matches(text: str, target: str) -> bool:
    s = langs.detect_script(text)
    return s["script"] is None or target in s["candidates"] or (target in {"ks-Deva", "sd"} and s["script"] == "Devanagari")


class _Translator:
    def __init__(self, target: str, script: str):
        self.target, self.script = target, script
        self.client = get_client()
        self.cost = 0.0
        self.calls = 0
        self.cached = 0

    async def one(self, text: str) -> str:
        script = None if self.script == "native" else self.script
        r = await self.client.translate(text, target_language=self.target, script=script)
        self.cost += r.cost_inr
        self.calls += 1
        self.cached += r.cached
        if r.data.get("truncated") and len(text) > 80:
            # Output stopped early: translate the sentences separately instead.
            parts = chunking.pack_sentences(text, max(len(text) // 3, 80))
            return " ".join([await self.one(part) for part in parts])
        return (r.data.get("translation") or "").strip()

    async def unit(self, text: str) -> str:
        if len(text) <= MAX_CHARS:
            return await self.one(text)
        pieces = chunking.pack_sentences(text, MAX_CHARS)
        return " ".join(await asyncio.gather(*(self.one(x) for x in pieces)))


def _check(source: str, translation: str, target: str) -> list[str]:
    issues = []
    if not translation.strip():
        return ["empty translation"]
    ratio = len(translation) / max(len(source), 1)
    if len(source) > 40 and not 0.25 <= ratio <= 4.0:
        issues.append(f"length ratio {ratio:.2f} (possible omission or repetition)")
    missing = chunking.missing_numbers(source, translation)
    if missing:
        issues.append(f"numbers missing: {', '.join(missing[:5])}")
    if len(translation) > 20 and not _script_matches(translation, target):
        issues.append(f"output is not in the {target} script")
    return issues


async def translate_text(
    target_language: str,
    text: str | None = None,
    path: str | None = None,
    preserve_format: bool = True,
    script: str = "native",
    out_dir: str | None = None,
) -> dict:
    target = langs.require_supported("indic-translate", target_language)
    if script not in {"native", "roman", "codemix"}:
        raise ToolError(ErrorCode.INVALID_ARGUMENT, "script must be native, roman or codemix.")
    source, source_id = _load_text(text, path)
    if len(source) > MAX_INPUT_CHARS:
        raise ToolError(ErrorCode.INVALID_ARGUMENT, f"Input is {len(source)} chars, over {MAX_INPUT_CHARS}.",
                        hint="Split the document and translate it in parts.")

    src_script = langs.detect_script(source)
    if script == "native" and src_script["candidates"] == [target] and src_script["share"] > 0.9:
        translation, units, issues, tr = source, 0, [], None  # already in the target language: no call
        note = f"Source is already {langs.language_names()[target]}; returned unchanged."
    else:
        note = None
        tr = _Translator(target, script)
        if preserve_format:
            blocks = chunking.parse_markdown(source)
            jobs: list[tuple[object, str]] = []  # (setter target, source text)
            for b in blocks:
                if b.translatable:
                    jobs.append((b, b.text))
                elif b.kind == "table":
                    for r_i, row in enumerate(b.rows):
                        for c_i, cell in enumerate(row):
                            if chunking.is_translatable_cell(cell):
                                jobs.append(((b, r_i, c_i), cell))
            outputs = await asyncio.gather(*(tr.unit(src) for _, src in jobs))
            issues = []
            for (target_ref, src), dst in zip(jobs, outputs, strict=True):
                if isinstance(target_ref, tuple):
                    b, r_i, c_i = target_ref
                    b.rows[r_i][c_i] = dst.replace("|", "\\|").replace("\n", " ")
                else:
                    target_ref.text = dst.replace("\n", " ")
                problems = _check(src, dst, target) if len(src) > 3 else []
                if problems:
                    issues.append({"source": workspace.preview(src, 80), "issues": problems})
            translation = chunking.render_markdown(blocks)
            units = len(jobs)
        else:
            paragraphs = [x for x in source.split("\n\n") if x.strip()]
            outputs = await asyncio.gather(*(tr.unit(x) for x in paragraphs))
            issues = [{"source": workspace.preview(s, 80), "issues": c} for s, d in zip(paragraphs, outputs, strict=True)
                      if (c := _check(s, d, target))]
            translation = "\n\n".join(outputs)
            units = len(paragraphs)

    if preserve_format and chunking.table_shape(translation) != chunking.table_shape(source):
        issues.append({"source": "(tables)", "issues": ["table shape changed"]})

    out = workspace.output_dir(out_dir)
    out_path = workspace.artifact_path(out, source_id, f".{target}.md", key=f"{script}|{preserve_format}")
    out_path.write_text(translation + ("\n" if not translation.endswith("\n") else ""), encoding="utf-8")
    empty = sum(1 for i in issues if "empty translation" in i["issues"])
    result = {
        "ok": True,
        "output_path": str(out_path),
        "target_language": target,
        "source_script": src_script,
        "units": units,
        "coverage": {"units": units, "empty": empty, "complete": empty == 0,
                     "tables_preserved": chunking.table_shape(translation) == chunking.table_shape(source)},
        "issues": issues[:20],
        "issue_count": len(issues),
        "chars": len(translation),
        "cost_inr": round(tr.cost, 4) if tr else 0.0,
        "calls": tr.calls if tr else 0,
        "cached_calls": tr.cached if tr else 0,
    }
    if note:
        result["note"] = note
    if len(translation) <= INLINE_LIMIT:
        result["translation"] = translation
    else:
        result["preview"] = workspace.preview(translation)
    return result


async def translate_segments(segments_path: str, target_language: str, out_dir: str | None = None) -> dict:
    target = langs.require_supported("indic-translate", target_language)
    p = workspace.resolve_input(segments_path)
    try:
        segments = json.loads(workspace.read_text_file(p))
        assert isinstance(segments, list) and all({"start", "end", "text"} <= set(s) for s in segments)
    except (json.JSONDecodeError, AssertionError, TypeError):
        raise ToolError(ErrorCode.INVALID_ARGUMENT, f"{p.name} is not a segments file.",
                        hint="Pass the segments_path returned by transcribe_audio.") from None

    tr = _Translator(target, "native")
    outputs = await asyncio.gather(*(tr.unit(s["text"]) for s in segments))
    translated, issues = [], []
    for s, dst in zip(segments, outputs, strict=True):
        translated.append({**s, "text": dst, "source_text": s["text"]})
        if problems := _check(s["text"], dst, target):
            issues.append({"id": s.get("id"), "issues": problems})

    out = workspace.output_dir(out_dir)
    out_path = workspace.artifact_path(out, p, f".{target}.segments.json")
    out_path.write_text(json.dumps(translated, ensure_ascii=False, indent=1), encoding="utf-8")
    return {
        "ok": True,
        "segments_path": str(out_path),
        "target_language": target,
        "segments": len(translated),
        "coverage": {"segments": len(translated), "empty": sum(1 for t in translated if not t["text"]),
                     "complete": all(t["text"] for t in translated)},
        "issues": issues[:20],
        "preview": workspace.preview(" ".join(t["text"] for t in translated)),
        "cost_inr": round(tr.cost, 4),
        "calls": tr.calls,
        "cached_calls": tr.cached,
    }
