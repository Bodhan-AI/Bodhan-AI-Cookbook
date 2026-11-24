"""Self-evaluation: run every task in demo/tasks.json and report a pass rate.

    python demo/make_inputs.py   # once
    python demo/run_demos.py     # writes demo/runs/<id>/ and demo/runs/summary.md
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
INPUTS, RUNS = HERE / "inputs", HERE / "runs"


def check(spec: dict, out: Path) -> tuple[bool, list[str]]:
    why = []
    try:
        rep = json.loads((out / "report.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False, ["no report.json"]
    if rep["status"] not in spec["expect_status"]:
        why.append(f"status {rep['status']} not in {spec['expect_status']}")
    arts = rep.get("artifacts", [])
    for suffix in spec.get("expect_artifacts", []):
        if not any(a.endswith(suffix) for a in arts):
            why.append(f"no {suffix} artifact")
    if spec.get("expect_no_artifacts") and arts:
        why.append(f"unexpected artifacts {arts}")
    answer = (out / "result.md").read_text(encoding="utf-8")
    for s in spec.get("expect_answer_contains", []):
        if s not in answer:
            why.append(f"answer does not mention {s}")
    if not rep["cost_inr"]["within_budget"]:
        why.append("over budget")
    return not why, why


def main() -> int:
    tasks = json.loads((HERE / "tasks.json").read_text(encoding="utf-8"))
    summarize_only = "--summarize-only" in sys.argv  # rebuild summary.md from existing runs, no API calls
    only = {a for a in sys.argv[1:] if not a.startswith("--")}
    rows = []
    for spec in tasks:
        if only and spec["id"] not in only:
            continue
        out = RUNS / spec["id"]
        cmd = [sys.executable, "-m", "bodhan_agent", "--task", spec["task"], "--files",
               *[str(INPUTS / f) for f in spec["files"]], "--out", str(out), *spec.get("extra_args", [])]
        t0 = time.monotonic()
        if not summarize_only:
            subprocess.run(cmd, check=False)
        ok, why = check(spec, out)
        rep = json.loads((out / "report.json").read_text()) if (out / "report.json").exists() else {}
        elapsed = rep.get("elapsed_sec") if summarize_only else round(time.monotonic() - t0, 1)
        rows.append((spec["id"], ok, rep.get("status"), rep.get("cost_inr", {}).get("total"), elapsed,
                     rep.get("steps"), "; ".join(why)))
        print(rows[-1])
    passed = sum(r[1] for r in rows)
    lines = [f"# Demo run: {passed}/{len(rows)} passed", "", "| Task | Pass | Status | Cost (Rs) | Time (s) | Steps | Notes |",
             "|---|---|---|---|---|---|---|"]
    lines += [f"| [{r[0]}]({r[0]}/result.md) | {'yes' if r[1] else 'no'} | {r[2]} | {r[3]} | {r[4]} | {r[5]} | {r[6]} |"
              for r in rows]
    RUNS.mkdir(exist_ok=True)
    (RUNS / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
