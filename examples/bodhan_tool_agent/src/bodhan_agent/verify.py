"""Verify every deliverable before the run ends (code only, using what the tools already measured)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import srt

from bodhan_core import langs

from .planner import Plan

AUDIO = {".mp3", ".wav"}
TEXT = {".md", ".txt"}
DURATION_TOLERANCE = 0.25


@dataclass
class Verification:
    checks: list[dict] = field(default_factory=list)
    missed_targets: list[dict] = field(default_factory=list)

    @property
    def failures(self) -> list[dict]:
        return [c for c in self.checks if not c["passed"]]

    def add(self, check: str, passed: bool, detail: str, artifact: str | None = None) -> None:
        self.checks.append({"check": check, "artifact": artifact, "passed": passed, "detail": detail})


def _lang_matches(text: str, code: str) -> bool:
    s = langs.detect_script(text)
    if s["script"] is None:
        return True
    if code == "en":
        return s["script"] == "Latin"
    return code in s["candidates"] or code.split("-")[0] in s["candidates"]


def verify(plan: Plan, answer: str, artifacts: list[str], results: dict[str, dict], work_dir: Path) -> Verification:
    v = Verification()
    resolved: list[Path] = []
    for a in artifacts:
        p = Path(a).expanduser().resolve()
        if not p.is_file():
            v.add("exists", False, f"{a} does not exist", a)
            continue
        if str(p) not in results and work_dir.resolve() not in p.parents:
            v.add("produced", False, f"{p.name} was not produced in this run (inputs are not deliverables)", a)
            continue
        resolved.append(p)
        produced = results.get(str(p), {})
        tool = produced.get("tool")
        if p.suffix in AUDIO:
            ver = produced.get("verification") or {}
            ok = bool(ver.get("checked") and ver.get("passed"))
            v.add("speech_back_transcription", ok,
                  "every piece matched its text when transcribed back" if ok else
                  f"verification failed for pieces {ver.get('failed_pieces')} (or was not run)", p.name)
        elif p.suffix == ".srt":
            try:
                cues = list(srt.parse(p.read_text(encoding="utf-8")))
                ok = bool(cues) and all(c.content.strip() for c in cues)
                v.add("subtitles_valid", ok, f"{len(cues)} cues", p.name)
            except Exception as e:  # noqa: BLE001
                v.add("subtitles_valid", False, f"unparseable SRT: {e}", p.name)
        elif p.suffix in TEXT and tool in {"translate_text"}:
            cov = produced.get("coverage") or {}
            ok = bool(cov.get("complete")) and cov.get("tables_preserved", True)
            v.add("translation_coverage", ok,
                  f"{cov.get('units')} units, {cov.get('empty')} empty, tables preserved={cov.get('tables_preserved')}, "
                  f"{produced.get('issue_count', 0)} warnings", p.name)

    # Every planned deliverable must be present, in the right language.
    for d in plan.deliverables:
        if d.kind == "answer":
            v.add("answer_present", bool(answer.strip()), "final answer written" if answer.strip() else "empty answer")
            continue
        if d.kind == "audio":
            matches = [p for p in resolved if p.suffix in AUDIO and _audio_lang(results.get(str(p), {}), d.language)]
        elif d.kind == "subtitles":
            matches = [p for p in resolved if p.suffix == ".srt" and _lang_matches(p.read_text(encoding="utf-8"), d.language)]
        else:
            matches = [p for p in resolved if p.suffix in TEXT and _lang_matches(p.read_text(encoding="utf-8"), d.language)]
        v.add("deliverable_present", bool(matches),
              f"{d.kind} in {d.language}: {', '.join(m.name for m in matches) or 'missing'}")
        if d.kind == "audio" and d.target_duration_s and matches:
            dur = float(results.get(str(matches[0]), {}).get("duration_s") or 0)
            ratio = dur / d.target_duration_s
            if not 1 - DURATION_TOLERANCE <= ratio <= 1 + DURATION_TOLERANCE:
                v.missed_targets.append({"target": f"audio duration ≈ {d.target_duration_s:.0f}s",
                                         "actual": f"{dur:.0f}s", "why": "summary length vs speaking rate"})
    return v


def _audio_lang(result: dict, code: str) -> bool:
    return not result or result.get("language") in (None, code)
