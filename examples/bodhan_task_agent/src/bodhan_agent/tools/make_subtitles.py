from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from bodhan_agent.core import guards
from bodhan_agent.schemas.ledger import Artifact
from bodhan_agent.schemas.mcp import McpResult
from bodhan_agent.schemas.tools import LocalResult, MakeSubtitlesInput
from bodhan_agent.tools.base import LocalTool, LocalToolError, ToolContext, known_file, output_file, register, write_new
from bodhan_agent.tools.read import _load
from bodhan_mcp.config import Settings
from bodhan_mcp.schemas.transcribe import TranscriptSegment
from bodhan_mcp.utils.script import dominant_script

MAX_CUE_S = 7.0
LINE_CHARS = 42
MAX_LINES = 2
CUE_CHARS = LINE_CHARS * MAX_LINES
SENTENCE_ENDS = (".", "?", "!", "।", "॥", "۔")
TIMING_NOTE = "Cue times are shared out within each transcript window by text length; they are approximate."

_SEGMENTS = TypeAdapter(list[TranscriptSegment])
_MARKER_RE = re.compile(r"\[\s*(\d+)\s*\]")
_SUM_FIELDS = ("model_calls", "http_attempts", "estimated_cost_inr", "possibly_billed_failed_attempts", "cache_hits")


@dataclass(frozen=True)
class Cue:
    start_s: float
    end_s: float
    text: str


class Usage:
    def __init__(self) -> None:
        self.totals: dict[str, Any] = {field: 0 for field in _SUM_FIELDS}
        self.totals["billing_uncertain"] = False
        self.calls = 0

    def add(self, result: McpResult) -> None:
        self.calls += 1
        usage = result.usage
        uncertain = bool(result.error and result.error.uncertain)
        if usage is None:
            self.totals["billing_uncertain"] = True
            return
        for field in _SUM_FIELDS:
            value = usage.get(field)
            if isinstance(value, int | float) and not isinstance(value, bool):
                self.totals[field] += value
        self.totals["billing_uncertain"] = (
            self.totals["billing_uncertain"] or uncertain or bool(usage.get("billing_uncertain"))
        )

    def summary(self) -> dict[str, Any] | None:
        if self.calls == 0:
            return None
        totals = dict(self.totals)
        totals["estimated_cost_inr"] = round(totals["estimated_cost_inr"], 6)
        return totals


def _segments(artifact: Artifact) -> tuple[dict[str, Any], list[TranscriptSegment]]:
    try:
        document = json.loads(_load(artifact))
    except ValueError as exc:
        raise LocalToolError("invalid_input", f"{artifact.id} is not valid JSON.") from exc
    raw = document.get("segments") if isinstance(document, dict) else None
    if not isinstance(raw, list):
        raise LocalToolError(
            "invalid_input",
            f"{artifact.id} has no segment list.",
            hint="Pass the segments_path returned by transcribe_audio.",
        )
    try:
        segments = _SEGMENTS.validate_python(raw)
    except ValidationError as exc:
        raise LocalToolError("invalid_input", f"{artifact.id} has malformed segments.") from exc
    return document, sorted(segments, key=lambda item: item.start_s)


def _groups(items: list[tuple[int, str]], limit: int) -> list[list[tuple[int, str]]]:
    groups: list[list[tuple[int, str]]] = []
    size = 0
    for number, text in items:
        cost = len(_numbered([(number, text)])) + 2
        if groups and size + cost <= limit:
            groups[-1].append((number, text))
            size += cost
        else:
            groups.append([(number, text)])
            size = cost
    return groups


def _numbered(group: list[tuple[int, str]]) -> str:
    return "\n\n".join(f"[{number}] {text.strip()}" for number, text in group)


def _parse_numbered(text: str, expected: list[int]) -> dict[int, str] | None:
    parts = _MARKER_RE.split(text)
    if parts[0].strip():
        return None
    numbers = [int(value) for value in parts[1::2]]
    if numbers != expected:
        return None
    pieces = [piece.strip() for piece in parts[2::2]]
    return dict(zip(numbers, pieces, strict=True))


def _longest_s(pieces: list[str], duration_s: float) -> float:
    total = sum(len(piece) for piece in pieces)
    return max(duration_s * len(piece) / total for piece in pieces) if total else 0.0


def _cue_texts(text: str, duration_s: float) -> list[str]:
    words = text.split()
    if not words:
        return []
    count = max(1, math.ceil(duration_s / MAX_CUE_S), math.ceil(len(" ".join(words)) / CUE_CHARS))
    pieces = _pack(words, count)
    while _longest_s(pieces, duration_s) > MAX_CUE_S + 1e-6 and count < len(words):
        count += 1
        pieces = _pack(words, count)
    return pieces


def _pack(words: list[str], count: int) -> list[str]:
    target = len(" ".join(words)) / count
    cues: list[str] = []
    current: list[str] = []
    for word in words:
        while len(word) > CUE_CHARS:
            if current:
                cues.append(" ".join(current))
                current = []
            cues.append(word[:CUE_CHARS])
            word = word[CUE_CHARS:]
        candidate = len(" ".join([*current, word]))
        if current and (candidate > CUE_CHARS or candidate > target * 1.15):
            cues.append(" ".join(current))
            current = []
        current.append(word)
        length = len(" ".join(current))
        if word.endswith(SENTENCE_ENDS) and length >= target * 0.6:
            cues.append(" ".join(current))
            current = []
    if current:
        cues.append(" ".join(current))
    return [cue for cue in cues if cue]


def _wrap(text: str) -> str:
    if len(text) <= LINE_CHARS:
        return text
    middle = len(text) // 2
    spaces = [index for index, char in enumerate(text) if char == " "]
    if not spaces:
        return text
    split = min(spaces, key=lambda index: abs(index - middle))
    return f"{text[:split]}\n{text[split + 1 :]}"


def _cues(windows: list[tuple[TranscriptSegment, str]]) -> list[Cue]:
    cues: list[Cue] = []
    for segment, text in windows:
        pieces = _cue_texts(text, segment.end_s - segment.start_s)
        total = sum(len(piece) for piece in pieces)
        start = max(segment.start_s, cues[-1].end_s if cues else 0.0)
        span = segment.end_s - start
        if span <= 0:
            continue
        for piece in pieces:
            end = start + span * len(piece) / total
            cues.append(Cue(round(start, 3), round(min(end, segment.end_s), 3), piece))
            start = end
    return [cue for cue in cues if cue.end_s > cue.start_s]


def _timestamp(seconds: float) -> str:
    millis = round(seconds * 1000)
    hours, rest = divmod(millis, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _srt(cues: list[Cue]) -> str:
    blocks = [
        f"{index}\n{_timestamp(cue.start_s)} --> {_timestamp(cue.end_s)}\n{_wrap(cue.text)}"
        for index, cue in enumerate(cues, start=1)
    ]
    return "\n\n".join(blocks) + "\n"


def _default_name(source: Artifact, language: str) -> str:
    name = Path(source.path).name
    stem = name.removesuffix(".segments.json") if name.endswith(".segments.json") else Path(name).stem
    return f"{stem}.{language}.srt"


class MakeSubtitlesTool(LocalTool):
    name = "make_subtitles"
    description = (
        "Build an .srt subtitle file from the segments_path returned by transcribe_audio. Bodhan's translate_text "
        "translates the transcript into target_language (skipped when it is already in that language) and code "
        "splits it into timed cues of at most 7 s and two lines. Cue times are approximate. It costs the same as "
        "translating the transcript. The subtitles are written to output_path in the work or artifacts folder."
    )
    Input = MakeSubtitlesInput

    async def run(self, params: MakeSubtitlesInput, ctx: ToolContext) -> LocalResult:
        source = known_file(ctx, params.segments_path)
        if source.kind != "segments":
            raise LocalToolError(
                "unsupported_input",
                f"{source.id} is a {source.kind} file, not transcript segments.",
                hint="Call transcribe_audio first and pass its segments_path.",
            )
        document, segments = _segments(source)
        path = output_file(ctx, params.output_path, _default_name(source, params.target_language), (".srt",))
        spoken = [segment for segment in segments if segment.status != "silent" and segment.text.strip()]
        if not spoken:
            raise LocalToolError("empty_input", f"{source.id} has no transcribed speech to subtitle.")
        warnings: list[str] = []
        suspect = sum(1 for segment in spoken if segment.status == "suspect")
        if suspect:
            warnings.append(f"{suspect} transcript segment(s) were flagged as suspect and may be wrong.")
        usage = Usage()
        same = document.get("language") == params.target_language
        fallback = 0
        if same:
            texts = {number: segment.text.strip() for number, segment in enumerate(spoken, start=1)}
        else:
            texts, fallback = await self._translate(ctx, spoken, params.target_language, usage)
            if fallback:
                warnings.append(f"{fallback} segment(s) were translated one by one after a batch lost its markers.")
        empty = [number for number, text in texts.items() if not text]
        if empty:
            warnings.append(f"{len(empty)} segment(s) came back empty and have no subtitles.")
        cues = _cues([(segment, texts[number]) for number, segment in enumerate(spoken, start=1) if texts[number]])
        if not cues:
            raise LocalToolError(
                "empty_output",
                "No subtitle cues could be built.",
                details={"usage": usage.summary()} if usage.calls else None,
            )
        content = _srt(cues)
        write_new(path, content.encode("utf-8"))
        body = "\n".join(cue.text for cue in cues)
        artifact = register(
            ctx,
            path,
            "subtitles",
            "code_composed",
            sources=[source.id],
            tool=self.name,
            language=params.target_language,
            language_source="requested",
            script=dominant_script(body),
            indic_text=guards.contains_indic(body),
            chars=len(content),
            duration_s=round(cues[-1].end_s, 3),
        )
        ctx.ledger.set_status(artifact.id, "warning" if warnings else "ok", warnings)
        data: dict[str, Any] = {
            "artifact": artifact.id,
            "path": artifact.path,
            "language": params.target_language,
            "cues": len(cues),
            "segments": len(spoken),
            "translated": not same,
            "duration_s": artifact.duration_s,
            "timing_note": TIMING_NOTE,
        }
        if same:
            data["note"] = "The transcript is already in the target language; it was not translated."
        if warnings:
            data["warnings"] = warnings
        return LocalResult(data=data, artifact_ids=[artifact.id], usage=usage.summary())

    async def _translate(
        self, ctx: ToolContext, spoken: list[TranscriptSegment], language: str, usage: Usage
    ) -> tuple[dict[int, str], int]:
        items = [(number, segment.text) for number, segment in enumerate(spoken, start=1)]
        texts: dict[int, str] = {}
        fallback = 0
        for group in _groups(items, Settings.from_env().translate_chunk_chars):
            if len(group) == 1:
                number, text = group[0]
                texts[number] = (await self._call(ctx, text, language, usage)).strip()
                continue
            translated = await self._call(ctx, _numbered(group), language, usage)
            parsed = _parse_numbered(translated, [number for number, _ in group])
            if parsed is None:
                fallback += len(group)
                parsed = {number: (await self._call(ctx, text, language, usage)).strip() for number, text in group}
            texts.update(parsed)
        return texts, fallback

    async def _call(self, ctx: ToolContext, text: str, language: str, usage: Usage) -> str:
        if ctx.mcp is None:
            raise LocalToolError(
                "server_unavailable", "The Bodhan MCP server is not running.", details=self._details(usage)
            )
        result = await ctx.mcp.call(
            "translate_text", {"text": text, "target_language": language}, deadline=ctx.deadline
        )
        usage.add(result)
        if not result.ok or result.data is None:
            error = result.error
            details = self._details(usage)
            details["uncertain"] = bool(error and error.uncertain)
            raise LocalToolError(
                error.code if error else "protocol_error",
                f"translate_text failed while making subtitles: {error.message if error else 'no result'}",
                hint=error.hint if error else None,
                retryable=bool(error and error.retryable),
                details=details,
            )
        if result.data.get("translation_is_preview"):
            try:
                return Path(str(result.data["output_path"])).read_text(encoding="utf-8")
            except (KeyError, OSError, UnicodeDecodeError) as exc:
                raise LocalToolError(
                    "protocol_error", "The full translation file could not be read.", details=self._details(usage)
                ) from exc
        return str(result.data.get("translation", ""))

    @staticmethod
    def _details(usage: Usage) -> dict[str, Any]:
        summary = usage.summary()
        return {"usage": summary} if summary is not None else {}
