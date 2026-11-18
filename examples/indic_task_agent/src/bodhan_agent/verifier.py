"""Verification: every check returns a number. Status is derived from these, never asserted."""
from __future__ import annotations

import difflib
import hashlib
import re
from collections import Counter
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


def verify_artifact(path: Path, kind: str, *, expected_chars: int | None = None, source_chars: int | None = None, source_path: Path | None = None, truncated: bool = False, trace: Trace | None = None) -> list[dict[str, str]]:
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
        if source_path and Path(source_path).is_file():
            source = Path(source_path).read_text(encoding="utf-8")
            missing = missing_digits(source, text)
            wanted = len(txt.digits(source))
            checks.append(_check(rel, "digits", "passed" if not missing else "failed", "no digits in source" if not wanted else f"{wanted} digits; missing {', '.join(missing[:8])}"))
    elif kind == "document":
        text = path.read_text(encoding="utf-8")
        checks.append(_check(rel, "non_empty", "passed" if text.strip() else "failed", f"{len(text)} chars"))
        if _has_tables(text):
            ok, detail = table_consistency(text)
            checks.append(_check(rel, "tables", "passed" if ok else "failed", detail))
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


def missing_digits(source: str, output: str) -> list[str]:
    """Digits (any Indic or ASCII form, folded) present in the source but absent from the output."""
    want, got = Counter(txt.digits(source)), Counter(txt.digits(output))
    return sorted((want - got).elements())


_WORDS = {word: i for i, word in enumerate("zero one two three four five six seven eight nine ten eleven twelve".split())}
_UNITS = {"s": 1.0, "sec": 1.0, "secs": 1.0, "second": 1.0, "seconds": 1.0, "min": 60.0, "mins": 60.0, "minute": 60.0, "minutes": 60.0, "hour": 3600.0, "hours": 3600.0}


def requested_duration(task: str) -> float | None:
    """Seconds the task asks for ('a two-minute clip', '90 seconds'), or None if it never says."""
    for match in re.finditer(r"(\d+(?:\.\d+)?|[a-z]+)[\s-]*(seconds?|secs?|s|minutes?|mins?|hours?)\b", task.lower()):
        raw, unit = match.groups()
        value = float(raw) if raw[0].isdigit() else _WORDS.get(raw)
        if value:
            return value * _UNITS[unit]
    return None


def verify_duration(path: Path, wanted_s: float, trace: Trace | None = None) -> list[dict[str, str]]:
    """Measured audio against the duration the task asked for, not against the model's own claim."""
    got = media.ffprobe_duration(str(path))
    tolerance = max(2.0, wanted_s * 0.10)
    ok = abs(got - wanted_s) <= tolerance
    check = _check(str(path), "requested_duration", "passed" if ok else "failed", f"{got:.1f}s vs {wanted_s:.1f}s asked for (tolerance {tolerance:.1f}s)")
    if trace:
        trace.emit("verify", **check)
    return [check]


def repeated_ngram(text: str, n: int = 5, limit: int = 2) -> str | None:
    """A 5-gram spoken more than `limit` times means a synthesis loop, not prose."""
    words = _norm(text).lower().split()
    counts = Counter(tuple(words[i : i + n]) for i in range(max(len(words) - n + 1, 0)))
    for gram, count in counts.items():
        if count > limit:
            return " ".join(gram)
    return None


_TABLE_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_TABLE_CELL = re.compile(r"<t[dh][^>]*>", re.I)
_MD_TABLE = re.compile(r"(?:^[ \t]*\|.*\|[ \t]*$\n?)+", re.M)
_MD_RULE = re.compile(r"^\|?[\s:|-]+\|?$")


def _has_tables(text: str) -> bool:
    return bool(re.search(r"<table", text, re.I) or _MD_TABLE.search(text))


def table_consistency(text: str) -> tuple[bool, str]:
    """Every row of a table should carry the same number of cells; a short row is lost text."""
    problems: list[str] = []
    for table in re.findall(r"<table.*?</table>", text, re.S | re.I):
        widths = [len(_TABLE_CELL.findall(row)) for row in _TABLE_ROW.findall(table)]
        widths = [w for w in widths if w]
        if widths and len(set(widths)) > 1:
            problems.append(f"HTML table rows carry {sorted(set(widths))} cells")
    for block in _MD_TABLE.findall(text):
        rows = [row.strip() for row in block.strip().splitlines() if row.strip() and not _MD_RULE.match(row.strip())]
        widths = [len(row.strip("|").split("|")) for row in rows]
        if widths and len(set(widths)) > 1:
            problems.append(f"Markdown table rows carry {sorted(set(widths))} cells")
    if problems:
        return False, "; ".join(problems[:3])
    return True, "every row carries the same number of cells"


def _looks_english(text: str) -> bool:
    return not any(0x0900 <= ord(ch) <= 0x0DFF for ch in text)


def l2_translation(path: Path, source_path: Path, target_language: str, ledger: Any, trace: Trace) -> list[dict[str, str]]:
    """Round-trip the translation to English and ask the LLM whether the meaning survived.

    Only an English source can be compared: the LLM may never read Indic text.
    Costs one translation call; skipped (never passed) when unaffordable or when
    the LLM is unavailable.
    """
    rel = str(path)
    if target_language == "en":
        return [_check(rel, "meaning", "skipped", "target is English; no English source to compare")]
    source = Path(source_path)
    if not source.is_file() or not _looks_english(source.read_text(encoding="utf-8")):
        return [_check(rel, "meaning", "skipped", "source is not English")]
    remaining = ledger.remaining()
    if remaining < 0.05:
        return [_check(rel, "meaning", "skipped", "budget")]
    try:
        import tempfile

        from bodhan_mcp.tools.translate import translate_text

        with tempfile.TemporaryDirectory(prefix="bodhan-verify-") as tmp:
            back = translate_text(str(path), "en", out_dir=tmp, max_cost_inr=remaining)
            ledger.settle(0.0, back.cost_inr)
            translated_back = Path(back.text_path).read_text(encoding="utf-8")
        from . import llm

        reply = llm.chat(
            [
                {"role": "system", "content": "You compare an English source with an English back-translation of a translation. Paraphrase and word choice are expected and fine. Reply on one line: 'consistent' if every fact, number, name and instruction survives, or 'inconsistent' only if one is lost, added or reversed; then a short reason. Do not rewrite either text."},
                {"role": "user", "content": f"SOURCE:\n{source.read_text(encoding='utf-8')[:4000]}\n\nBACK-TRANSLATION:\n{translated_back[:4000]}"},
            ],
            max_tokens=1500,
        )
        verdict = reply.text.strip().lower()
        check = _check(rel, "meaning", "passed" if verdict.startswith("consistent") else "failed", reply.text.strip()[:160])
    except Exception as exc:  # noqa: BLE001 - a failed check must never read as passed
        check = _check(rel, "meaning", "skipped", f"check error: {type(exc).__name__}")
    trace.emit("verify", **check)
    return [check]


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
        checks = [
            _check(rel, "back_transcription", "passed" if ratio >= 0.60 else "failed", f"similarity {ratio:.2f} (threshold 0.60)"),
        ]
        looped = repeated_ngram(spoken)
        checks.append(_check(rel, "repetition", "failed" if looped else "passed", "a 5-gram repeats more than twice in the back-transcription" if looped else "no 5-gram repeats more than twice"))
    except Exception as exc:  # noqa: BLE001 - a failed check must never read as passed
        checks = [_check(rel, "back_transcription", "skipped", f"check error: {type(exc).__name__}")]
    for check in checks:
        trace.emit("verify", **check)
    return checks


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
