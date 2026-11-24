"""Final, deterministic verification of a run's outputs. Plain code, no LLM.

Quality target: "every generated audio file and translation is checked before the task ends".
So we check *every* audio file and translation the tools produced in the run (not only the
deliverables), and on top of that check each deliverable for existence, emptiness and format.
The LLM's own claim of success never overrides these results.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from bodhan_mcp import audio, languages, text

_WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
             "ten": 10, "half": 0.5, "a": 1, "an": 1}
_DURATION_RE = re.compile(r"\b(\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|half|an?)[\s-]*"
                          r"(minute|min|second|sec)s?\b", re.IGNORECASE)


def requested_duration_s(task: str) -> float | None:
    """A spoken-length target stated in the task ("two-minute", "30 seconds"), if any."""
    m = _DURATION_RE.search(task)
    if not m:
        return None
    raw = m.group(1).lower()
    n = _WORD_NUM.get(raw)
    value = float(n if n is not None else raw)
    return value * (60 if m.group(2).lower().startswith("min") else 1)


def _check(name: str, passed: bool, detail: str = "", severity: str = "error", file: str | None = None) -> dict[str, Any]:
    c = {"name": name, "passed": bool(passed), "severity": severity, "detail": detail}
    if file:
        c["file"] = file
    return c


def _srt_check(p: Path) -> dict[str, Any]:
    body = p.read_text(encoding="utf-8", errors="replace")
    stamps = re.findall(r"(\d\d):(\d\d):(\d\d)[,.](\d{3}) --> (\d\d):(\d\d):(\d\d)[,.](\d{3})", body)
    if not stamps:
        return _check("subtitles_parse", False, "no cues found", file=p.name)
    to_s = lambda h, m, s, ms: int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000  # noqa: E731
    prev_end, ok = -1.0, True
    for st in stamps:
        a, b = to_s(*st[:4]), to_s(*st[4:])
        if b <= a or a < prev_end - 0.01:
            ok = False
            break
        prev_end = b
    return _check("subtitles_parse", ok, f"{len(stamps)} cues, timestamps {'monotonic' if ok else 'out of order'}",
                  file=p.name)


def verify_run(task: str, produced: dict[str, dict[str, Any]], deliverables: list[Path],
               authored: set[str]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    # 1. Every audio file and translation produced by a tool in this run.
    for path, info in produced.items():
        data, tool, key = info.get("data") or {}, info.get("tool"), info.get("key")
        name = Path(path).name
        if tool == "synthesize_speech" and key == "audio_file":
            v = data.get("verification")
            if v is None:
                checks.append(_check("speech_back_transcription", False, "synthesised without verification", file=name))
            else:
                bad = [p for p in v.get("pieces", []) if p.get("status") != "ok"]
                checks.append(_check("speech_back_transcription", v.get("passed", False),
                                     f"mean CER {v.get('mean_cer')}, max CER {v.get('max_cer')}, "
                                     f"{len(bad)} piece(s) flagged {sorted({p.get('status') for p in bad})}" if bad
                                     else f"mean CER {v.get('mean_cer')}, all pieces ok", file=name))
        elif tool == "translate_text" and key == "output_file":
            v = data.get("verification") or {}
            failed = [c for c in v.get("checks", []) if not c.get("passed")]
            checks.append(_check("translation_checks", v.get("passed", False),
                                 "; ".join(f"{c['name']}: {c.get('detail', '')}" for c in failed)[:400] or
                                 "length, script, numbers and structure checks passed", file=name))
    # 2. Every deliverable.
    target_s = requested_duration_s(task)
    for p in deliverables:
        name = p.name
        if not p.is_file():
            checks.append(_check("artifact_exists", False, "file missing", file=name))
            continue
        size = p.stat().st_size
        checks.append(_check("artifact_non_empty", size > 0, f"{size} bytes", file=name))
        suffix = p.suffix.lower()
        if suffix in {".srt", ".vtt"}:
            checks.append(_srt_check(p))
        elif suffix in {".mp3", ".wav", ".m4a", ".ogg", ".flac"}:
            try:
                dur = audio.probe(p).get("duration_s") or 0.0
            except Exception as exc:  # report, never crash verification
                checks.append(_check("audio_decodes", False, str(exc)[:200], file=name))
                continue
            checks.append(_check("audio_decodes", dur > 0.2, f"duration {dur:.1f}s", file=name))
            if target_s:
                ratio = dur / target_s
                checks.append(_check("requested_duration", 0.6 <= ratio <= 1.4,
                                     f"{dur:.0f}s vs requested ~{target_s:.0f}s", severity="warning", file=name))
        elif suffix in {".md", ".txt", ".markdown"}:
            body = p.read_text(encoding="utf-8", errors="replace")
            checks.append(_check("text_non_blank", bool(body.strip()), f"{len(body)} chars", file=name))
            if str(p.resolve()) in authored:
                checks.append(_check("llm_text_is_english", languages.indic_fraction(body) == 0.0,
                                     "LLM-authored file contains no Indian-script text", file=name))
            if text.table_shapes(body):
                checks.append(_check("tables_present", True, f"{len(text.table_shapes(body))} table(s)",
                                     severity="warning", file=name))
    errors = [c for c in checks if not c["passed"] and c["severity"] == "error"]
    return {"passed": not errors, "checks": checks, "failed": len(errors),
            "warnings": sum(1 for c in checks if not c["passed"] and c["severity"] == "warning")}
