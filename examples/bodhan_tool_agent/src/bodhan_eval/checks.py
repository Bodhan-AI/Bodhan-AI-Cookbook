"""Deterministic graders for one agent run. Each check reads only the run's out/ folder (report.json,
trace.jsonl, artifacts/), so a finished run can be re-graded later at no cost.

A check is {"type": "<name>", ...params}. Each returns (passed, detail). No LLM is used for grading."""

from __future__ import annotations

import json
import re
from pathlib import Path

import srt

from bodhan_agent.guards import INDIC_RE
from bodhan_core import langs

CheckResult = tuple[bool, str]


class Run:
    """A finished agent run, loaded from its out/ folder."""

    def __init__(self, out: Path):
        self.out = out
        self.report = json.loads((out / "report.json").read_text(encoding="utf-8"))
        trace = out / "trace.jsonl"
        self.trace = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()] if trace.exists() else []

    @property
    def artifacts(self) -> list[Path]:
        folder = self.out / "artifacts"
        return sorted(p for p in folder.iterdir() if p.is_file()) if folder.is_dir() else []

    def matching(self, pattern: str | None) -> list[Path]:
        rx = re.compile(pattern or ".")
        return [p for p in self.artifacts if rx.search(p.name)]

    @property
    def answer_text(self) -> str:
        answer = self.report.get("answer")
        if answer is None and (self.out / "result.md").exists():  # reports from before "answer" was added
            m = re.search(r"^## Answer\n(.*?)(?=^## |\Z)", (self.out / "result.md").read_text(encoding="utf-8"), re.M | re.S)
            answer = m.group(1).strip() if m else None
        return " ".join(filter(None, [answer, self.report.get("reason")]))

    def acts(self, tool: str | None = None, ok: bool | None = None) -> list[dict]:
        return [e for e in self.trace if e.get("phase") == "act" and (tool is None or e.get("tool") == tool)
                and (ok is None or bool(e.get("ok")) == ok)]


def _text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def _need(run: Run, params: dict) -> tuple[list[Path], CheckResult | None]:
    files = run.matching(params.get("match"))
    if not files:
        return files, (False, f"no artifact matches {params.get('match')!r} (have: {[p.name for p in run.artifacts]})")
    return files, None


# --------------------------------------------------------------------------- run-level checks


def status(run: Run, p: dict) -> CheckResult:
    allowed = p["value"] if isinstance(p["value"], list) else [p["value"]]
    got = run.report.get("status")
    return got in allowed, f"status={got} (expected {' or '.join(allowed)})"


def max_cost_inr(run: Run, p: dict) -> CheckResult:
    got = float(run.report.get("total_cost_inr") or 0)
    return got <= p["value"] + 1e-9, f"cost ₹{got:.4f} (limit ₹{p['value']})"


def max_steps(run: Run, p: dict) -> CheckResult:
    got = int(run.report.get("steps") or 0)
    return got <= p["value"], f"{got} steps (limit {p['value']})"


def verification_passed(run: Run, p: dict) -> CheckResult:
    failed = [c["check"] for c in run.report.get("verification") or [] if not c.get("passed")]
    ok = bool(run.report.get("verification_passed"))
    return ok, "all output checks passed" if ok else f"failed or missing: {failed or 'no checks ran'}"


def tool_called(run: Run, p: dict) -> CheckResult:
    used = {e["tool"] for e in run.acts(ok=True)}
    missing = [t for t in p["tools"] if t not in used]
    return not missing, f"missing successful call(s): {missing}" if missing else f"called {p['tools']}"


def tool_not_called(run: Run, p: dict) -> CheckResult:
    used = sorted({e["tool"] for e in run.acts(ok=True)} & set(p["tools"]))  # blocked attempts do not count
    return not used, f"called {used}" if used else f"no successful call to {p['tools']}"


def speech_voice_gender(run: Run, p: dict) -> CheckResult:
    calls = run.acts("synthesize_speech", ok=True)
    if not calls:
        return False, "no successful synthesize_speech call"
    for c in calls:
        args = c.get("arguments") or {}
        gender = args.get("gender")
        if args.get("voice"):
            match = [v["gender"] for v in langs.voices() if v["voice"].lower() == str(args["voice"]).lower()]
            gender = match[0] if match else gender
        if gender != p["gender"]:
            return False, f"speech used gender={gender!r} voice={args.get('voice')!r}"
    return True, f"{len(calls)} speech call(s), all {p['gender']}"


# --------------------------------------------------------------------------- answer checks


def answer_contains(run: Run, p: dict) -> CheckResult:
    text = run.answer_text.lower()
    missing = [w for w in p["all"] if w.lower() not in text]
    return not missing, f"answer lacks {missing}" if missing else f"answer mentions {p['all']}"


def answer_not_contains(run: Run, p: dict) -> CheckResult:
    text = run.answer_text.lower()
    found = [w for w in p["any"] if w.lower() in text]
    return not found, f"answer contains {found}" if found else "none of the forbidden strings appear"


def answer_has_no_indic_text(run: Run, p: dict) -> CheckResult:
    hit = INDIC_RE.search(run.answer_text)
    return hit is None, "answer is English only" if hit is None else "answer contains Indian-language characters"


# --------------------------------------------------------------------------- artifact checks


def artifacts_count(run: Run, p: dict) -> CheckResult:
    n = len(run.matching(p.get("match")))
    lo, hi = p.get("min", 0), p.get("max", 10**6)
    return lo <= n <= hi, f"{n} artifact(s) match {p.get('match')!r} (expected {lo}..{hi if hi < 10**6 else '∞'})"


def artifact_script(run: Run, p: dict) -> CheckResult:
    files, fail = _need(run, p)
    if fail:
        return fail
    for f in files:
        d = langs.detect_script(_text(f))
        if d["script"] != p["script"] or d["share"] < p.get("min_share", 0.6):
            return False, f"{f.name}: script={d['script']} share={d['share']} (expected {p['script']})"
    return True, f"{len(files)} file(s) in {p['script']} script"


def artifact_contains(run: Run, p: dict) -> CheckResult:
    files, fail = _need(run, p)
    if fail:
        return fail
    text = " ".join(_text(f) for f in files).lower()
    missing = [w for w in p["all"] if w.lower() not in text]
    return not missing, f"artifacts lack {missing}" if missing else f"artifacts mention {p['all']}"


def artifact_not_contains(run: Run, p: dict) -> CheckResult:
    files, fail = _need(run, p)
    if fail:
        return fail
    text = " ".join(_text(f) for f in files).lower()
    found = [w for w in p["any"] if w.lower() in text]
    return not found, f"artifacts contain {found}" if found else f"artifacts avoid {p['any']}"


def table_rows(run: Run, p: dict) -> CheckResult:
    files, fail = _need(run, p)
    if fail:
        return fail
    rows = [line for f in files for line in _text(f).splitlines() if line.strip().startswith("|")]
    return len(rows) >= p["min"], f"{len(rows)} table row(s) (min {p['min']})"


def audio_duration(run: Run, p: dict) -> CheckResult:
    from bodhan_core import audio

    files, fail = _need(run, p)
    if fail:
        return fail
    for f in files:
        d = audio.probe(f)["duration_s"]
        if not p.get("min_s", 0) <= d <= p.get("max_s", 1e9):
            return False, f"{f.name}: {d:.1f}s (expected {p.get('min_s', 0)}–{p.get('max_s', '∞')}s)"
    return True, f"{len(files)} audio file(s) within {p.get('min_s', 0)}–{p.get('max_s', '∞')}s"


def srt_valid(run: Run, p: dict) -> CheckResult:
    files, fail = _need(run, p)
    if fail:
        return fail
    for f in files:
        try:
            cues = list(srt.parse(_text(f)))
        except Exception as exc:  # noqa: BLE001 - any parse error fails the check
            return False, f"{f.name}: does not parse ({exc})"
        if not cues or any(not c.content.strip() for c in cues):
            return False, f"{f.name}: empty file or empty cue"
        if any(a.end > b.start for a, b in zip(cues, cues[1:], strict=False)) or any(c.end <= c.start for c in cues):
            return False, f"{f.name}: overlapping or zero-length cues"
    return True, f"{len(files)} valid SRT file(s)"


CHECKS = {f.__name__: f for f in [
    status, max_cost_inr, max_steps, verification_passed, tool_called, tool_not_called, speech_voice_gender,
    answer_contains, answer_not_contains, answer_has_no_indic_text,
    artifacts_count, artifact_script, artifact_contains, artifact_not_contains, table_rows, audio_duration, srt_valid,
]}


def grade(out: Path, checks: list[dict]) -> list[dict]:
    """Run every check against a finished run. A missing report fails every check."""
    try:
        run = Run(out)
    except (OSError, json.JSONDecodeError) as exc:
        return [{"type": c["type"], "passed": False, "detail": f"no readable report.json ({exc.__class__.__name__})"}
                for c in checks]
    results = []
    for c in checks:
        fn = CHECKS.get(c["type"])
        if fn is None:
            results.append({"type": c["type"], "passed": False, "detail": "unknown check type"})
            continue
        try:
            passed, detail = fn(run, c)
        except Exception as exc:  # noqa: BLE001 - a grader crash is a failed check, not a crashed suite
            passed, detail = False, f"grader error: {exc.__class__.__name__}: {exc}"
        results.append({"type": c["type"], "passed": bool(passed), "detail": detail})
    return results
