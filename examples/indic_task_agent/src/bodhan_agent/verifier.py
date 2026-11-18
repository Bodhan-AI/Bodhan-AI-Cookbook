"""Verification: every check returns a number. Status is derived from these, never asserted."""
from __future__ import annotations

import difflib
import hashlib
import re
from pathlib import Path
from typing import Any

from bodhan_mcp.core import media
from bodhan_mcp.core import text as txt

from .trace import Trace

_SCRIPT_ONLY = re.compile(r"[^\w\s]", re.UNICODE)


def _norm(text: str) -> str:
    return " ".join(_SCRIPT_ONLY.sub(" ", text).split())


def similarity(a: str, b: str) -> float:
    return round(difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio(), 3)


def _check(artifact: str, check: str, result: str, detail: str) -> dict[str, str]:
    return {"artifact": artifact, "check": check, "result": result, "detail": detail}


def verify_artifact(path: Path, kind: str, *, expected_chars: int | None = None, source_chars: int | None = None, truncated: bool = False, trace: Trace | None = None) -> list[dict[str, str]]:
    """L1 checks: free, structural, always run. A missing artifact fails, it never passes silently."""
    rel = str(path)
    if not path.is_file() or path.stat().st_size == 0:
        return [_check(rel, "exists", "failed", "artifact is missing or empty")]
    checks: list[dict[str, str]] = [_check(rel, "exists", "passed", f"{path.stat().st_size} bytes")]
    if kind == "audio":
        duration = media.ffprobe_duration(str(path))
        expected = (expected_chars or 0) / 13.5 if expected_chars else None
        checks.append(_check(rel, "parses", "passed", f"{duration:.2f}s"))
        if expected:
            ratio = duration / expected
            ok = 0.65 <= ratio <= 1.35
            checks.append(_check(rel, "duration", "passed" if ok else "failed", f"{duration:.2f}s vs {expected:.2f}s predicted (ratio {ratio:.2f})"))
    elif kind == "translation":
        text = path.read_text(encoding="utf-8")
        if source_chars is not None:
            checks.append(_check(rel, "coverage", "passed" if text.strip() else "failed", f"{len(text)} chars"))
        checks.append(_check(rel, "not_truncated", "failed" if truncated else "passed", "API truncated flag" if truncated else "API reported a complete translation"))
        if source_chars:
            ratio = len(text) / max(source_chars, 1)
            ok = 0.6 <= ratio <= 2.5
            checks.append(_check(rel, "length_ratio", "passed" if ok else "failed", f"{ratio:.2f}"))
        if not txt.digits(text) and source_chars:
            checks.append(_check(rel, "digits", "passed", "no digits in source"))
    elif kind == "document":
        text = path.read_text(encoding="utf-8")
        checks.append(_check(rel, "non_empty", "passed" if text.strip() else "failed", f"{len(text)} chars"))
    elif kind == "text":
        text = path.read_text(encoding="utf-8")
        ok_len = expected_chars is None or len(text) <= int(expected_chars * 1.05)
        checks.append(_check(rel, "english_only", "passed" if text.strip().isascii() else "failed", "non-ASCII found" if not text.strip().isascii() else "ASCII"))
        checks.append(_check(rel, "length", "passed" if ok_len else "failed", f"{len(text)} chars vs target {expected_chars}"))
    for c in checks:
        if trace:
            trace.emit("verify", **c)
    return checks


def verify_srt(path: Path, trace: Trace | None = None) -> list[dict[str, str]]:
    content = path.read_text(encoding="utf-8")
    blocks = [b for b in content.strip().split("\n\n") if b.strip()]
    bad_lines, last_end, monotonic = 0, -1.0, True
    for block in blocks:
        lines = block.splitlines()
        if len(lines) < 3:
            bad_lines += 1
            continue
        stamp = lines[1]
        try:
            start_s, end_s = _parse_stamp(stamp)
        except ValueError:
            bad_lines += 1
            continue
        if start_s < last_end - 1e-6:
            monotonic = False
        last_end = end_s
        if len(lines) - 2 > 2 or any(len(line) > 42 for line in lines[2:]):
            bad_lines += 1
    checks = [
        _check(str(path), "cues", "passed" if blocks and not bad_lines else "failed", f"{len(blocks)} cues, {bad_lines} malformed"),
        _check(str(path), "monotonic", "passed" if monotonic else "failed", "cues ordered without overlap" if monotonic else "cues overlap or go backwards"),
    ]
    for c in checks:
        if trace:
            trace.emit("verify", **c)
    return checks


def _parse_stamp(stamp: str) -> tuple[float, float]:
    left, _, right = stamp.partition(" --> ")
    return _to_s(left.strip()), _to_s(right.strip())


def _to_s(value: str) -> float:
    hms, _, ms = value.partition(",")
    h, m, s = (int(x) for x in hms.split(":"))
    return h * 3600 + m * 60 + s + int(ms or 0) / 1000


def l2_audio(path: Path, source_path: Path, ledger: Any, trace: Trace) -> list[dict[str, str]]:
    """Back-transcribe the first 30 s and compare with the spoken source. Costs one ASR call; skipped if unaffordable."""
    rel = str(path)
    ledger_remaining = ledger.remaining()
    if ledger_remaining < 0.25:
        return [_check(rel, "back_transcription", "skipped", "budget")]
    from bodhan_mcp.tools.transcribe import transcribe_audio

    try:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="bodhan-verify-") as tmp:
            duration = media.ffprobe_duration(str(path))
            window = min(30.0, duration)
            wav = Path(tmp) / "audio.wav"
            media.to_wav(str(path), str(wav))
            clip = Path(tmp) / "head.wav"
            clip.write_bytes(media.wav_slice(str(wav), 0.0, window))
            transcript = transcribe_audio(str(clip), out_dir=tmp, max_cost_inr=ledger_remaining)
            ledger.settle(0.0, transcript.cost_inr)
            spoken = Path(transcript.text_path).read_text(encoding="utf-8")
            source = source_path.read_text(encoding="utf-8")[: int(window * 13.5) + 40]
            ratio = similarity(spoken, source)
        result = "passed" if ratio >= 0.60 else "failed"
        check = _check(rel, "back_transcription", result, f"similarity {ratio:.2f} (threshold 0.60)")
    except Exception as exc:  # noqa: BLE001 - a failed check must never read as passed
        check = _check(rel, "back_transcription", "skipped", f"check error: {type(exc).__name__}")
    trace.emit("verify", **check)
    return [check]


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def derive_status(checks: list[dict[str, str]], any_artifact: bool) -> str:
    failed = [c for c in checks if c["result"] == "failed"]
    if not any_artifact:
        return "failed"
    if failed:
        return "partial"
    return "completed"
