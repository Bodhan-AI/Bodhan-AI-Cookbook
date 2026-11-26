"""Summarise per-stage latency from an app log (for the demo / Bodhan's product team).

    python -m app.main --mode subtitles --log-file out/session.log
    python -m tools.latency_report out/session.log [more.log ...]

Reads the per-utterance INFO lines ("in #3 2.4s audio | asr 0.81s mt 0.40s | e2e 1.25s | ...").
e2e is measured from the VAD cut (or push-to-talk release), so what a listener perceives is roughly
VAD_END_SILENCE_MS + e2e for subtitles. partial (subtitles, since 2026-11-26) is when the
transcript alone is ready and shown; e2e is when its translation is. For interpreting, tts and e2e are until the FIRST
part of the translated speech is ready (since 2026-11-26; older logs measured all parts).
"""
from __future__ import annotations

import argparse
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

from app.stats import STAGES, UTTERANCE_LINE

LINE = UTTERANCE_LINE  # one definition, shared with the app's live readout
# app.pipeline.incoming, live captions (local ASR): "in #4 live 5.2s so far | asr 0.50s mt 0.23s | ..."
LIVE = re.compile(r"\bin #\d+ live [\d.]+s so far \| asr (?P<asr>[\d.]+)s mt (?P<mt>[\d.]+)s")
# app.main at startup: "Speech recognition: Bodhan hosted Indic-Transcribe" / "...: local Indic-Transcribe from ..."
BACKEND = re.compile(r"Speech recognition: (?:Bodhan )?(?P<backend>hosted|local)\b")
# app.bodhan.client: "speak failed ([429 rate_limit_exceeded_error] ...); retry 1/6 in 46.0s"
RETRY = re.compile(r"\b(?P<ep>\w+) failed \(\[(?P<status>\d+) (?P<code>[^\]\s]+)\]")


def parse(lines: Iterable[str]) -> dict[str, dict[str, list[float]]]:
    """direction -> stage -> seconds, for every utterance line found."""
    out: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for line in lines:
        m = LINE.search(line)
        if not m:
            continue
        for stage in STAGES:
            if m.group(stage) is not None:
                out[m.group("dir")][stage].append(float(m.group(stage)))
    return out


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(p / 100 * (len(ordered) - 1)))]


def report(data: dict[str, dict[str, list[float]]]) -> str:
    names = {"in": "Subtitles (they speak)", "out": "Interpreting (you speak)"}
    rows: list[str] = []
    for direction in ("in", "out"):
        stages = data.get(direction)
        if not stages:
            continue
        rows.append(f"\n{names[direction]}: {len(stages['asr'])} utterances")
        rows.append(f"  {'stage':<7} {'median':>7} {'p90':>7} {'max':>7}")
        for stage in STAGES:
            values = stages.get(stage)
            if values:
                rows.append(f"  {stage:<7} {statistics.median(values):>6.2f}s {percentile(values, 90):>6.2f}s "
                            f"{max(values):>6.2f}s")
    return "\n".join(rows) if rows else "No utterance lines found. Was the app run at INFO level?"


def backends(lines: Iterable[str]) -> list[str]:
    """ASR backends of the runs in these logs, in order of first appearance."""
    seen: list[str] = []
    for line in lines:
        m = BACKEND.search(line)
        if m and m.group("backend") not in seen:
            seen.append(m.group("backend"))
    return seen


def backend_line(seen: list[str]) -> str:
    if not seen:
        return "Speech recognition: not logged (log from before 2026-11-26)"
    if len(seen) > 1:
        return f"Speech recognition: MIXED runs ({', '.join(seen)}); use one log file per backend"
    return f"Speech recognition: {seen[0]}"


def live_report(lines: Iterable[str]) -> str:
    """Live captions (local ASR): how many were shown and how long each took to produce."""
    took = [float(m.group("asr")) + float(m.group("mt")) for m in map(LIVE.search, lines) if m]
    if not took:
        return ""
    return (f"\nLive captions shown: {len(took)} (asr+mt median {statistics.median(took):.2f}s, "
            f"max {max(took):.2f}s)")


# app.pipeline.dubbing: "dub #3 | speech 3.03s | heard 3.5s after they finished | 1 part(s) | ..."
DUB = re.compile(r"\bdub #\d+ \| speech (?P<speech>[\d.]+)s \| heard (?P<heard>[\d.]+)s after they finished")
DUB_SKIP = re.compile(r"Dub of #\d+ skipped: (?P<why>keeping speech requests|[\d.]+s behind)")


def dub_report(lines: Iterable[str]) -> str:
    """Incoming dubbing: lines spoken and skipped, how long after the speaker each was heard."""
    lines = list(lines)
    spoken = [m for m in map(DUB.search, lines) if m]
    skips = Counter("rate limit" if m.group("why").startswith("keeping") else "too far behind"
                    for m in map(DUB_SKIP.search, lines) if m)
    if not spoken and not skips:
        return ""
    rows = [f"\nDubbing (their side spoken to you): {len(spoken)} lines spoken"]
    if spoken:
        heard = [float(m.group("heard")) for m in spoken]
        speech = [float(m.group("speech")) for m in spoken]
        rows.append(f"  heard after they finished: median {statistics.median(heard):.1f}s, "
                    f"p90 {percentile(heard, 90):.1f}s, max {max(heard):.1f}s")
        rows.append(f"  speech synthesis per line: median {statistics.median(speech):.1f}s")
    if skips:
        rows.append("  skipped: " + ", ".join(f"{n} {why}" for why, n in sorted(skips.items())))
    return "\n".join(rows)


def retries(lines: Iterable[str]) -> Counter[tuple[str, str]]:
    """(endpoint, "status code") -> number of retried attempts."""
    found: Counter[tuple[str, str]] = Counter()
    for line in lines:
        m = RETRY.search(line)
        if m:
            found[(m.group("ep"), f"{m.group('status')} {m.group('code')}")] += 1
    return found


def retry_report(found: Counter[tuple[str, str]]) -> str:
    if not found:
        return "\nRetries: none (no rate limits hit)."
    rows = ["\nRetries:"]
    for (ep, status), n in sorted(found.items()):
        rows.append(f"  {ep:<10} {status:<40} x{n}")
    limited = sorted({ep for ep, status in found if status.startswith("429")})
    for ep in limited:
        rows.append(f"  {ep} hit its rate limit: set BODHAN_{ep.upper()}_RPM below the rate it reached.")
    return "\n".join(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Per-stage latency summary from app logs")
    parser.add_argument("logs", nargs="+", type=Path)
    args = parser.parse_args()
    lines: list[str] = []
    for path in args.logs:
        lines.extend(path.read_text(encoding="utf-8", errors="replace").splitlines())
    print(backend_line(backends(lines)))
    print(report(parse(lines)))
    for extra in (live_report(lines), dub_report(lines)):
        if extra:
            print(extra)
    print(retry_report(retries(lines)))


if __name__ == "__main__":
    main()
