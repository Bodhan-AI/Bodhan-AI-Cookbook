"""Add the "why" to missed quality targets in finished runs, from each run's own trace.

    python scripts/explain_missed_targets.py evaluation/runs [more folders ...]

Runs made before the agent wrote these notes itself carry a generic latency note. For every report.json under
the given folders, this recomputes quality_targets.latency.note (where the time went), budget.note and
verification.note with the same functions the agent now uses, marks each recomputed note with `note_source`,
and appends the latency note to the "Time:" line of result.md. Nothing is rerun and no other field changes.
Running it twice changes nothing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bodhan_agent.report import _events, budget_reason, latency_reason, verification_reason  # noqa: E402

SOURCE = "recomputed from trace.jsonl by scripts/explain_missed_targets.py"


def explain(run: Path) -> bool:
    rp = run / "report.json"
    r = json.loads(rp.read_text(encoding="utf-8"))
    q, before = r["quality_targets"], json.dumps(r["quality_targets"], sort_keys=True)
    lat = q["latency"]
    if not lat["met"] and not lat.get("note_source") and not str(lat.get("note") or "").startswith("missed by "):
        lat["note"] = latency_reason(_events(run / "trace.jsonl"), float(r["elapsed_s"]), float(lat["target_s"]))
        lat["note_source"] = SOURCE
    if "note" not in q["budget"]:
        q["budget"]["note"] = None if q["budget"]["within_budget"] else budget_reason(r["cost"])
        if q["budget"]["note"]:
            q["budget"]["note_source"] = SOURCE
    if "note" not in q["verification"]:
        unverified = r["verification"]["unverified_translations_or_audio"]
        failed = [(a["path"], "; ".join(map(str, (a.get("verification") or {}).get("problems") or [])[:2]) or "see its verification record")
                  for a in r["artifacts"] if a["kind"] in ("translation", "audio", "subtitles") and (a.get("verification") or {}).get("status") == "fail"]
        q["verification"]["note"] = verification_reason(unverified, failed)
        if q["verification"]["note"]:
            q["verification"]["note_source"] = SOURCE
    if json.dumps(q, sort_keys=True) == before:
        return False
    rp.write_text(json.dumps(r, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md = run / "result.md"
    if lat.get("note") and md.is_file():
        lines = md.read_text(encoding="utf-8").split("\n")
        for i, line in enumerate(lines):
            if line.startswith("- Time: ") and line.rstrip().endswith("missed)"):
                lines[i] = line.rstrip() + f"; {lat['note']}"
        md.write_text("\n".join(lines), encoding="utf-8")
    return True


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    changed = [rp.parent for folder in argv for rp in sorted(Path(folder).rglob("report.json")) if explain(rp.parent)]
    for run in changed:
        print(f"explained: {run}")
    print(f"{len(changed)} report(s) updated")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
