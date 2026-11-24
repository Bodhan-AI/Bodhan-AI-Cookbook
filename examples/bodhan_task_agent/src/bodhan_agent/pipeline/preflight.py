from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
from pathlib import Path
from time import monotonic
from typing import Any

from bodhan_agent.core import guards
from bodhan_agent.core.config import RunSettings
from bodhan_agent.schemas.ledger import Artifact, Ledger
from bodhan_agent.schemas.preflight import InputIssue, PreflightResult
from bodhan_mcp.exceptions import BodhanToolError
from bodhan_mcp.utils.audio import sniff_audio_format
from bodhan_mcp.utils.documents import probe_pdf_pages, sniff_document
from bodhan_mcp.utils.process import run_process
from bodhan_mcp.utils.script import dominant_script, language_from_script

MAX_INPUT_FILES = 50
PREFLIGHT_DEADLINE_S = 30.0
TEXT_SAMPLE_BYTES = 2_000_000
HEAD_BYTES = 64
TASK_TRANSLATION_SHARE = 0.5
SYSTEM_TOOLS = ("ffmpeg", "ffprobe", "pdfinfo", "pdftoppm")
MARKDOWN_SUFFIXES = frozenset({".md", ".markdown"})
AUDIO_BRANDS = frozenset({b"M4A ", b"M4B ", b"M4P "})
L0_NOTE = "The full original task. It may not be the content the user wants processed."

_QUOTE_RE = re.compile(r'"([^"\n]+)"|“([^”\n]+)”|‘([^’\n]+)’|«([^»\n]+)»')
_EBML = b"\x1a\x45\xdf\xa3"
_UTF8_MAX_TAIL = 3


def _issue(path: Path | str, code: str, message: str) -> InputIssue:
    return InputIssue(path=str(path), code=code, message=message)


def _inside(path: Path, folder: Path) -> bool:
    return path.resolve().is_relative_to(folder)


def _sha256(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def _head(path: Path) -> bytes:
    with path.open("rb") as handle:
        return handle.read(HEAD_BYTES)


def _container(head: bytes) -> str | None:
    if head[4:8] == b"ftyp":
        return "audio" if head[8:12] in AUDIO_BRANDS else "video"
    if head[:4] == _EBML or (head[:4] == b"RIFF" and head[8:12] == b"AVI "):
        return "video"
    return None


def _read_text(path: Path, size: int) -> tuple[str, bool] | None:
    with path.open("rb") as handle:
        data = handle.read(TEXT_SAMPLE_BYTES)
    if b"\x00" in data:
        return None
    complete = size <= TEXT_SAMPLE_BYTES
    try:
        return data.decode("utf-8-sig"), complete
    except UnicodeDecodeError as exc:
        if complete or exc.start < len(data) - _UTF8_MAX_TAIL:
            return None
        return data[: exc.start].decode("utf-8-sig"), False


def _script_facts(text: str) -> dict[str, Any]:
    script = dominant_script(text)
    language, _ = language_from_script(script)
    return {
        "script": script,
        "indic_text": guards.contains_indic(text),
        "language": language,
        "language_source": "detected" if language else None,
    }


def collect_inputs(settings: RunSettings) -> tuple[list[Path], list[InputIssue]]:
    found: list[Path] = []
    seen: set[Path] = set()
    issues: list[InputIssue] = []

    def add(path: Path) -> None:
        if path not in seen:
            seen.add(path)
            found.append(path)

    def usable(child: Path, name: str) -> bool:
        if name.startswith("."):
            issues.append(_issue(child, "skipped_hidden", f"{name} is hidden and was skipped."))
            return False
        if child.is_symlink():
            issues.append(_issue(child, "skipped_link", f"{name} is a symbolic link and was skipped."))
            return False
        return not _inside(child, settings.out_dir)

    for root in settings.files:
        if not root.exists():
            issues.append(_issue(root, "missing_input", f"{root} does not exist."))
        elif root.is_file():
            add(root)
        elif not root.is_dir():
            issues.append(_issue(root, "unsupported_input", f"{root} is not a regular file or folder."))
        else:
            before = len(found)
            for current, dirs, files in os.walk(root):
                here = Path(current)
                dirs[:] = [name for name in sorted(dirs) if usable(here / name, name)]
                for name in sorted(files):
                    if usable(here / name, name):
                        add(here / name)
            if len(found) == before:
                issues.append(_issue(root, "empty_input", f"{root} contains no usable files."))
    if len(found) > MAX_INPUT_FILES:
        issues.append(
            _issue(
                settings.files[0] if settings.files else "",
                "too_many_files",
                f"{len(found)} files were found; only the first {MAX_INPUT_FILES} are used.",
            )
        )
        found = found[:MAX_INPUT_FILES]
    return found, issues


async def _probe_media(path: Path, deadline: float) -> dict[str, Any] | None:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return None
    try:
        code, stdout, _ = await run_process(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type:stream_disposition=attached_pic",
                "-of",
                "json",
                str(path),
            ],  # fmt: skip
            deadline,
            "Inspecting media",
        )
        if code != 0:
            return {}
        data = json.loads(stdout)
    except (BodhanToolError, ValueError):
        return {}
    streams = data.get("streams") or []
    try:
        duration = float((data.get("format") or {}).get("duration"))
    except (TypeError, ValueError):
        duration = math.nan
    return {
        "has_audio": any(stream.get("codec_type") == "audio" for stream in streams),
        "has_video": any(
            stream.get("codec_type") == "video" and not (stream.get("disposition") or {}).get("attached_pic")
            for stream in streams
        ),
        "duration_s": duration if math.isfinite(duration) and duration > 0 else None,
    }


def _media_fields(probe: dict[str, Any] | None, guess: str | None, path: Path) -> tuple[dict[str, Any], list[str]]:
    if probe is None:
        kind = guess or "unknown"
        status = "warning" if guess else "failed"
        return {"kind": kind, "step_status": status, "warnings": ["ffprobe is not installed; length unknown"]}, []
    if not probe:
        return {"kind": "unknown", "step_status": "failed", "note": "could not be read as audio or video"}, [
            f"{path.name} could not be read as audio or video."
        ]
    kind = "video" if probe["has_video"] else "audio" if probe["has_audio"] else "unknown"
    fields: dict[str, Any] = {"kind": kind, "duration_s": probe["duration_s"], "step_status": "ok"}
    if not probe["has_audio"]:
        fields.update(step_status="failed", warnings=["no audio track"], note="has no audio track")
        return fields, [f"{path.name} has no audio track."]
    if probe["duration_s"] is None:
        fields.update(step_status="warning", warnings=["duration unknown"])
    return fields, []


async def inspect_input(path: Path, artifact_id: str, deadline: float) -> tuple[Artifact, list[InputIssue]]:
    base: dict[str, Any] = {"id": artifact_id, "path": str(path), "producer": "user_input"}
    try:
        digest, size = _sha256(path)
        head = _head(path)
    except OSError as exc:
        artifact = Artifact(**base, kind="unknown", step_status="failed", note="could not be read")
        return artifact, [_issue(path, "unreadable_input", f"{path.name} could not be read: {exc.strerror}.")]
    base.update(sha256=digest, size_bytes=size)
    if size == 0:
        artifact = Artifact(**base, kind="unknown", step_status="failed", note="empty file")
        return artifact, [_issue(path, "empty_input", f"{path.name} is empty.")]

    document = sniff_document(head)
    if document == "bmp" and _read_text(path, size) is not None:
        document = None
    if document == "pdf":
        if shutil.which("pdfinfo") is None:
            return Artifact(**base, kind="pdf", step_status="warning", warnings=["pdfinfo is not installed"]), []
        try:
            pages = await probe_pdf_pages(path, deadline=deadline)
        except BodhanToolError as error:
            artifact = Artifact(**base, kind="pdf", step_status="failed", note=error.message)
            return artifact, [_issue(path, "unreadable_input", error.message)]
        return Artifact(**base, kind="pdf", pages=pages, step_status="ok"), []
    if document is not None:
        return Artifact(**base, kind="image", pages=1, step_status="ok"), []

    guess = "audio" if sniff_audio_format(head) else _container(head)
    text = None if guess else _read_text(path, size)
    if text is not None:
        content, complete = text
        kind = "markdown" if path.suffix.lower() in MARKDOWN_SUFFIXES else "text"
        fields = {**base, "kind": kind, **_script_facts(content), "step_status": "ok"}
        if complete:
            fields["chars"] = len(content)
        else:
            fields["note"] = f"larger than {TEXT_SAMPLE_BYTES:,} bytes; facts come from the start of the file"
        if not content.strip():
            fields.update(step_status="failed", note="contains only whitespace")
            return Artifact(**fields), [_issue(path, "empty_input", f"{path.name} contains only whitespace.")]
        return Artifact(**fields), []

    probe = await _probe_media(path, deadline)
    if guess is None and not (probe and (probe["has_audio"] or probe["has_video"])):
        artifact = Artifact(**base, kind="unknown", step_status="failed", note="unsupported file type")
        return artifact, [
            _issue(path, "unsupported_input", f"{path.name} is not a document, image, audio, video or text file.")
        ]
    fields, problems = _media_fields(probe, guess, path)
    issues = [_issue(path, "unsupported_input", problem) for problem in problems]
    return Artifact(**base, **fields), issues


def _save_literal(settings: RunSettings, ledger: Ledger, literal_id: str, text: str, note: str) -> Artifact:
    path = settings.literals_dir / f"{literal_id}.txt"
    data = text.encode("utf-8")
    with path.open("xb") as handle:
        handle.write(data)
    return ledger.add(
        Artifact(
            id=literal_id,
            path=str(path),
            kind="text",
            producer="user_input",
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
            chars=len(text),
            step_status="ok",
            note=note,
            **_script_facts(text),
        )
    )


def _marker(artifact: Artifact, original: str, quoted: bool) -> str:
    if not artifact.indic_text:
        return f"{original} ({artifact.id})"
    script = artifact.script if artifact.script in guards.INDIC_SCRIPTS else "Indic"
    label = f"[{artifact.id}: {artifact.chars:,} chars of {script} text]"
    return f'"{label}"' if quoted else label


def extract_literals(task: str, settings: RunSettings, ledger: Ledger) -> tuple[str, list[str], bool]:
    quoted = [match for match in _QUOTE_RE.finditer(task) if match.group(match.lastindex or 0).strip()]
    gaps: list[tuple[int, int]] = []
    cursor = 0
    for match in quoted:
        gaps.append((cursor, match.start()))
        cursor = match.end()
    gaps.append((cursor, len(task)))
    outside = "".join(task[start:end] for start, end in gaps)
    needs_translation = guards.indic_share(outside) >= TASK_TRANSLATION_SHARE

    regions = [(match.start(), match.end(), match.start(match.lastindex), match.end(match.lastindex), True)
               for match in quoted]  # fmt: skip
    if not needs_translation:
        for gap_start, gap_end in gaps:
            for start, end in guards.indic_spans(task[gap_start:gap_end]):
                regions.append((gap_start + start, gap_start + end, gap_start + start, gap_start + end, False))
    regions.sort()

    parts: list[str] = []
    literal_ids: list[str] = []
    cursor = 0
    for start, end, inner_start, inner_end, is_quoted in regions:
        literal_id = ledger.next_id("literal")
        note = "Text quoted in the task." if is_quoted else "Indian-language text from the task."
        artifact = _save_literal(settings, ledger, literal_id, task[inner_start:inner_end], note)
        parts.append(task[cursor:start])
        parts.append(_marker(artifact, task[start:end], is_quoted))
        literal_ids.append(literal_id)
        cursor = end
    parts.append(task[cursor:])
    return "".join(parts), literal_ids, needs_translation


async def run_preflight(settings: RunSettings, ledger: Ledger) -> PreflightResult:
    for folder in (settings.artifacts_dir, settings.literals_dir):
        folder.mkdir(parents=True, exist_ok=True)
    deadline = monotonic() + PREFLIGHT_DEADLINE_S
    paths, issues = collect_inputs(settings)
    input_ids: list[str] = []
    for path in paths:
        artifact, problems = await inspect_input(path, ledger.next_id("input"), deadline)
        ledger.add(artifact)
        input_ids.append(artifact.id)
        issues.extend(problems)

    task_for_llm, literal_ids, needs_translation = extract_literals(settings.task, settings, ledger)
    instruction_id = None
    if needs_translation:
        original = _save_literal(settings, ledger, "L0", settings.task, L0_NOTE)
        instruction_id = original.id
        task_for_llm = (
            f"[L0: the task is written in {original.script or 'an Indian-language'} script "
            f"({original.chars:,} chars); Bodhan translates it to English before planning.]"
        )
        if literal_ids:
            quoted = ", ".join(_marker(ledger.artifacts[item], "", False) for item in literal_ids)
            task_for_llm += f" Quoted text from the task: {quoted}."
    task_for_llm, _ = guards.redact_text(task_for_llm)
    return PreflightResult(
        task=settings.task,
        task_for_llm=task_for_llm,
        needs_translation=needs_translation,
        instruction_id=instruction_id,
        literal_ids=literal_ids,
        input_ids=input_ids,
        issues=issues,
        missing_tools=[name for name in SYSTEM_TOOLS if shutil.which(name) is None],
    )
