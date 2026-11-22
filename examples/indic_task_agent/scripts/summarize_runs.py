"""Print a Markdown table of every run in a folder (reads each run's report.json).

Run from examples/indic_task_agent:  python scripts/summarize_runs.py out
"""

import json
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
print("| Run | Status | Steps | Time (s) | Cost (Rs) | Files | Notes |")
print("|---|---|---|---|---|---|---|")
for rep in sorted(root.glob("*/report.json")):
    r = json.loads(rep.read_text(encoding="utf-8"))
    notes = []
    if r.get("downgraded_from"):
        notes.append(f"downgraded from {r['downgraded_from']}")
    if r.get("injection_flags"):
        notes.append(f"{len(r['injection_flags'])} injection flag(s)")
    if r.get("warnings"):
        notes.append(f"{len(r['warnings'])} warning(s)")
    if r["stop_reason"] != "finished":
        notes.append(f"stop: {r['stop_reason']}")
    files = sum(1 for o in r["outputs"] if o["exists"])
    print(f"| {rep.parent.name} | {r['status']} | {r['steps']} | {r['elapsed_s']} | {r['cost_inr']} | "
          f"{files} | {'; '.join(notes) or '-'} |")