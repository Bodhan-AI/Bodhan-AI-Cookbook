from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from bodhan_agent.core import guards
from bodhan_agent.schemas.ledger import Artifact, Ledger
from bodhan_agent.schemas.plan import Deliverable
from bodhan_agent.schemas.report import CheckStatus, VerificationCheck
from bodhan_agent.schemas.verify import StepOutcome, StepVerdict, Verdict
from bodhan_agent.tools.base import file_digest
from bodhan_mcp.core.languages import LANGUAGE_SCRIPTS
from bodhan_mcp.utils.script import script_counts
from bodhan_mcp.utils.text import table_shapes

WORD_RECALL_MIN = 0.8
DURATION_TOLERANCE = 0.2
SCRIPT_WARN_SHARE = 0.8
SCRIPT_FAIL_SHARE = 0.3
LENGTH_RATIO_RANGE = (0.3, 3.0)
SPEECH_COVERAGE_MIN = 0.8
MAX_TEXT_BYTES = 20 * 1024 * 1024

SCRIPT_SUFFIXES: dict[str, frozenset[str]] = {
    "deva": frozenset({"Devanagari"}),
    "beng": frozenset({"Bengali"}),
    "arab": frozenset({"Arabic"}),
    "mtei": frozenset({"Meetei Mayek"}),
    "latn": frozenset({"Latin"}),
}

_SRT_TIME = r"(\d{2}):(\d{2}):(\d{2}),(\d{3})"
_SRT_TIMING_RE = re.compile(rf"^{_SRT_TIME}\s*-->\s*{_SRT_TIME}$")
_PATH_RE = re.compile(r"(?<![\w/])(/[^\s`'\"<>()\[\]]+\.[A-Za-z0-9]{1,5})\b")


def _base(code: str) -> str:
    return code.split("-", 1)[0].lower()


def _check(name: str, status: CheckStatus, detail: str | None = None, **numbers: float | None) -> VerificationCheck:
    return VerificationCheck(name=name, status=status, detail=detail, **numbers)


def _status(warnings: list[str], failed: str | None = None) -> StepOutcome:
    if failed is not None:
        return "failed"
    return "warning" if warnings else "ok"


def step_verdict(tool: str, data: dict[str, Any]) -> StepVerdict:
    warnings = [str(item) for item in data.get("warnings") or []]
    facts: dict[str, Any] = {}
    failed: str | None = None
    if tool == "transcribe_audio":
        facts = {key: data.get(key) for key in ("verification_status", "chunks", "silent_chunks", "suspect_chunks")}
        spoken = int(data.get("chunks") or 0) - int(data.get("silent_chunks") or 0)
        if not str(data.get("transcript") or "").strip():
            failed = "The transcript is empty."
        elif spoken > 0 and int(data.get("suspect_chunks") or 0) >= spoken:
            warnings.append("Every transcribed window was flagged as suspect.")
    elif tool == "translate_text":
        facts = {
            key: data.get(key)
            for key in ("verification_status", "script", "source_chars", "translated_chars", "recovery_splits")
        }
        if not int(data.get("translated_chars") or 0):
            failed = "The translation is empty."
    elif tool == "ocr_document":
        facts = {"pages_read": len(data.get("pages_read") or [])}
        if not facts["pages_read"]:
            failed = "No page was read."
        elif not str(data.get("text") or "").strip():
            failed = "No text was found."
    elif tool == "synthesize_speech":
        verification = data.get("verification") or {}
        facts = {
            "speech_verification": verification.get("status"),
            "word_recall": verification.get("word_recall"),
            "label": data.get("label"),
            "voice_gender": data.get("voice_gender"),
            "format": data.get("format"),
        }
        if not float(data.get("duration_s") or 0) > 0:
            failed = "The audio has no duration."
        elif verification.get("status") != "passed":
            reason = verification.get("reason") or f"speech verification is '{verification.get('status')}'"
            warnings.append(str(reason))
    if data.get("verification_status") == "warning" and not warnings:
        warnings.append("The tool reported a verification warning.")
    if failed is not None:
        warnings.insert(0, failed)
    return StepVerdict(status=_status(warnings, failed), warnings=warnings, tool_checks=facts)


def apply_step(ledger: Ledger, artifact_ids: Iterable[str], verdict: StepVerdict) -> None:
    for artifact_id in artifact_ids:
        artifact = ledger.get(artifact_id)
        if artifact is None:
            continue
        artifact.tool_checks = {**artifact.tool_checks, **verdict.tool_checks}
        ledger.set_status(artifact_id, verdict.status, verdict.warnings)


def expected_scripts(language: str | None, artifact: Artifact | None = None) -> frozenset[str] | None:
    if not language:
        return None
    if artifact is not None and artifact.tool_checks.get("script") == "roman":
        return frozenset({"Latin"})
    parts = language.split("-")
    if len(parts) > 1 and parts[1].lower() in SCRIPT_SUFFIXES:
        return SCRIPT_SUFFIXES[parts[1].lower()]
    if _base(language) == "en":
        return frozenset({"Latin"})
    scripts = LANGUAGE_SCRIPTS.get(_base(language))
    return scripts or None


def script_share(text: str, scripts: frozenset[str]) -> float | None:
    counts = script_counts(text)
    total = sum(counts.values())
    if not total:
        return None
    return sum(count for script, count in counts.items() if script in scripts) / total


def _script_check(text: str, language: str | None, artifact: Artifact | None) -> VerificationCheck:
    scripts = expected_scripts(language, artifact)
    if scripts is None:
        return _check("target_script", "skipped", "No target language to check against.")
    share = script_share(text, scripts)
    expected = " or ".join(sorted(scripts))
    if share is None:
        return _check("target_script", "failed", "The text has no letters.")
    if share < SCRIPT_FAIL_SHARE:
        status: CheckStatus = "failed"
    elif share < SCRIPT_WARN_SHARE:
        status = "warning"
    else:
        status = "passed"
    return _check(
        "target_script", status, f"{share:.0%} of letters are in {expected} script.",
        value=round(share, 3), threshold=SCRIPT_WARN_SHARE,
    )  # fmt: skip


def _read_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_TEXT_BYTES:
            return None
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None


def parse_srt(text: str) -> list[tuple[float, float, str]] | None:
    cues: list[tuple[float, float, str]] = []
    blocks = [block for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()) if block.strip()]
    for number, block in enumerate(blocks, start=1):
        lines = block.strip().split("\n")
        if len(lines) < 3 or lines[0].strip() != str(number):
            return None
        match = _SRT_TIMING_RE.match(lines[1].strip())
        if match is None:
            return None
        values = [int(value) for value in match.groups()]
        start = values[0] * 3600 + values[1] * 60 + values[2] + values[3] / 1000
        end = values[4] * 3600 + values[5] * 60 + values[6] + values[7] / 1000
        cues.append((start, end, "\n".join(lines[2:]).strip()))
    return cues or None


def _common_checks(ledger: Ledger, deliverable: Deliverable, artifact: Artifact) -> list[VerificationCheck]:
    checks: list[VerificationCheck] = []
    path = Path(artifact.path)
    if not path.is_file():
        return [_check("file_intact", "failed", f"{artifact.id} no longer exists.")]
    if artifact.sha256 is not None and file_digest(path)[0] != artifact.sha256:
        return [_check("file_intact", "failed", f"{artifact.id} changed after it was produced.")]
    checks.append(_check("file_intact", "passed"))
    if artifact.kind != deliverable.kind:
        checks.append(_check("kind", "failed", f"Expected {deliverable.kind}, got {artifact.kind}."))
    if deliverable.format and path.suffix.lower().lstrip(".") != deliverable.format:
        checks.append(_check("format", "failed", f"Expected .{deliverable.format}, got {path.name}."))
    if deliverable.language and artifact.language and _base(deliverable.language) != _base(artifact.language):
        checks.append(
            _check("language", "failed", f"Expected '{deliverable.language}', recorded '{artifact.language}'.")
        )
    problem = guards.deliverable_source_problem(ledger, artifact, deliverable.language or artifact.language)
    checks.append(_check("provenance", "failed", problem) if problem else _check("provenance", "passed"))
    if artifact.step_status == "failed":
        checks.append(_check("step_status", "failed", "; ".join(artifact.warnings) or "The step failed."))
    elif artifact.step_status == "warning":
        checks.append(_check("step_status", "warning", "; ".join(artifact.warnings) or "The step had warnings."))
    elif artifact.step_status == "pending":
        checks.append(_check("step_status", "failed", "The step was never checked."))
    return checks


def _audio_checks(deliverable: Deliverable, artifact: Artifact) -> tuple[list[VerificationCheck], str | None]:
    facts = artifact.tool_checks
    checks: list[VerificationCheck] = []
    duration = artifact.duration_s or 0.0
    checks.append(
        _check("audio_duration", "passed", value=duration)
        if duration > 0
        else _check("audio_duration", "failed", "The audio has no duration.")
    )
    recall = facts.get("word_recall")
    verification = facts.get("speech_verification")
    if verification == "passed" and isinstance(recall, int | float) and recall >= WORD_RECALL_MIN:
        checks.append(_check("speech_word_recall", "passed", value=float(recall), threshold=WORD_RECALL_MIN))
    else:
        detail = f"Speech verification is '{verification}'." if verification else "Speech was not verified."
        value = float(recall) if isinstance(recall, int | float) else None
        checks.append(_check("speech_word_recall", "warning", detail, value=value, threshold=WORD_RECALL_MIN))
    target = deliverable.constraints.duration_s
    if target is not None and duration > 0:
        ratio = abs(duration - target) / target
        status: CheckStatus = "passed" if ratio <= DURATION_TOLERANCE else "warning"
        checks.append(
            _check("duration_target", status, f"{duration:.1f} s against a {target:.1f} s target.",
                   value=round(duration, 3), threshold=target)
        )  # fmt: skip
    wanted = deliverable.constraints.voice_gender
    if wanted is not None:
        actual = facts.get("voice_gender")
        status = "passed" if actual == wanted else "warning"
        checks.append(_check("voice_gender", status, f"Requested {wanted}, used {actual or 'unknown'}."))
    label = facts.get("label") if isinstance(facts.get("label"), str) and facts.get("label") else None
    checks.append(
        _check("ai_label", "passed", label) if label else _check("ai_label", "failed", "No AI-generated label.")
    )
    return checks, label


def _source_text(ledger: Ledger, artifact: Artifact) -> str | None:
    for source_id in artifact.sources:
        source = ledger.get(source_id)
        if source is not None and source.kind in ("text", "markdown"):
            return _read_text(Path(source.path))
    return None


def _text_checks(ledger: Ledger, deliverable: Deliverable, artifact: Artifact) -> list[VerificationCheck]:
    text = _read_text(Path(artifact.path))
    if text is None:
        return [_check("text_readable", "failed", "The file is not readable UTF-8 text.")]
    if not text.strip():
        return [_check("text_readable", "failed", "The file is empty.")]
    checks = [_check("text_readable", "passed"), _script_check(text, deliverable.language, artifact)]
    verification = artifact.tool_checks.get("verification_status")
    if verification == "passed":
        checks.append(_check("translation_structure", "passed"))
    elif verification == "warning":
        checks.append(_check("translation_structure", "warning", "The translation reported structural warnings."))
    source = _source_text(ledger, artifact)
    if source is not None and source.strip():
        ratio = len(text) / len(source)
        low, high = LENGTH_RATIO_RANGE
        status: CheckStatus = "passed" if low <= ratio <= high else "warning"
        checks.append(
            _check("length_coverage", status, f"Output is {ratio:.2f}x the source length.", value=round(ratio, 3))
        )
    if deliverable.constraints.keep_tables:
        checks.append(_tables_check(source, text))
    return checks


def _tables_check(source: str | None, text: str) -> VerificationCheck:
    if source is None:
        return _check("tables_kept", "skipped", "No source text to compare tables with.")
    before = [(shape.rows, shape.columns) for shape in table_shapes(source)]
    after = [(shape.rows, shape.columns) for shape in table_shapes(text)]
    if before == after:
        return _check("tables_kept", "passed", f"{len(before)} table(s) kept.")
    if before and not after:
        return _check("tables_kept", "failed", f"The source had {len(before)} table(s); the output has none.")
    return _check("tables_kept", "warning", f"Table shapes changed from {before} to {after}.")


def _speech_duration(ledger: Ledger, artifact: Artifact) -> float | None:
    for source_id in artifact.sources:
        source = ledger.get(source_id)
        if source is None:
            continue
        if source.duration_s:
            return source.duration_s
        for parent_id in source.sources:
            parent = ledger.get(parent_id)
            if parent is not None and parent.duration_s:
                return parent.duration_s
    return None


def _subtitle_checks(ledger: Ledger, deliverable: Deliverable, artifact: Artifact) -> list[VerificationCheck]:
    text = _read_text(Path(artifact.path))
    cues = parse_srt(text) if text is not None else None
    if cues is None:
        return [_check("srt_parses", "failed", "The file is not a valid SRT.")]
    checks = [_check("srt_parses", "passed", f"{len(cues)} cue(s).", value=len(cues))]
    ordered = all(end > start for start, end, _ in cues) and all(
        cues[index + 1][0] >= cues[index][1] for index in range(len(cues) - 1)
    )
    checks.append(
        _check("cues_ordered", "passed")
        if ordered
        else _check("cues_ordered", "failed", "Cues overlap, run backwards or have no length.")
    )
    duration = _speech_duration(ledger, artifact)
    if duration:
        coverage = min(1.0, cues[-1][1] / duration)
        status: CheckStatus = "passed" if coverage >= SPEECH_COVERAGE_MIN else "warning"
        checks.append(
            _check("speech_coverage", status, f"Cues reach {coverage:.0%} of the audio.",
                   value=round(coverage, 3), threshold=SPEECH_COVERAGE_MIN)
        )  # fmt: skip
    checks.append(_script_check("\n".join(body for _, _, body in cues), deliverable.language, artifact))
    return checks


def _answer_checks(ledger: Ledger, answer_en: str | None) -> list[VerificationCheck]:
    if answer_en is None or not answer_en.strip():
        return [_check("answer_english", "failed", "No answer was given.")]
    problem = guards.llm_text_problem(answer_en)
    checks = [
        _check("answer_english", "failed", problem[1]) if problem else _check("answer_english", "passed"),
    ]
    unknown = [path for path in _PATH_RE.findall(answer_en) if not _known(ledger, path)]
    if unknown:
        checks.append(_check("answer_files", "warning", f"Mentions files not produced in this run: {unknown[:5]}"))
    elif _PATH_RE.search(answer_en):
        checks.append(_check("answer_files", "passed"))
    return checks


def _known(ledger: Ledger, raw: str) -> bool:
    artifact = ledger.by_path(raw)
    return artifact is not None and Path(artifact.path).is_file()


def _verdict(checks: list[VerificationCheck], label: str | None = None) -> Verdict:
    statuses = {check.status for check in checks}
    if "failed" in statuses:
        return Verdict(status="failed", checks=checks, label=label)
    return Verdict(status="warning" if "warning" in statuses else "verified", checks=checks, label=label)


def verify_deliverable(
    ledger: Ledger, deliverable: Deliverable, artifact: Artifact | None, answer_en: str | None = None
) -> Verdict:
    if deliverable.kind == "answer":
        return _verdict(_answer_checks(ledger, answer_en))
    if artifact is None:
        return Verdict(status="missing")
    checks = _common_checks(ledger, deliverable, artifact)
    if any(check.name == "file_intact" and check.status == "failed" for check in checks):
        return _verdict(checks)
    label = None
    if deliverable.kind == "audio":
        extra, label = _audio_checks(deliverable, artifact)
    elif deliverable.kind == "subtitles":
        extra = _subtitle_checks(ledger, deliverable, artifact)
    else:
        extra = _text_checks(ledger, deliverable, artifact)
    return _verdict([*checks, *extra], label)
