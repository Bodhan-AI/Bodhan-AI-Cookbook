"""Run the demo tasks in tasks.json with the real agent (spends Bodhan credit).

    python demo/run_demo.py --list
    python demo/run_demo.py --only 06-impossible-french        # free
    python demo/run_demo.py                                    # all six
    python demo/run_demo.py --collect                          # copy small outputs to demo/results/

Runs write to outputs/demo/<id>/ (git-ignored). --collect copies result.md, report.json, trace.jsonl and text
artifacts (no audio) to demo/results/<id>/ so they can be committed and linked from the README.
"""
from __future__ import annotations

import argparse, json, shutil, subprocess, sys, time
from pathlib import Path

RECIPE = Path(__file__).resolve().parents[1]
TASKS = json.loads((Path(__file__).parent / "tasks.json").read_text(encoding="utf-8"))
OUT = RECIPE / "outputs" / "demo"
RESULTS = Path(__file__).parent / "results"
LEDGER = RECIPE / "outputs" / "credit_ledger.json"
TEXT_SUFFIXES = {".md", ".txt", ".srt", ".json"}


def python_exe() -> str:
    """The recipe's own venv Python when it exists (so the script works even if the venv is not activated)."""
    for rel in (".venv/Scripts/python.exe", ".venv/bin/python"):
        if (RECIPE / rel).exists():
            return str(RECIPE / rel)
    return sys.executable


def remaining() -> float | None:
    try:
        return float(json.loads(LEDGER.read_text())["remaining_inr"])
    except Exception:
        return None


def run(t: dict) -> dict:
    out = OUT / t["id"]
    if out.exists():
        shutil.rmtree(out)
    cmd = [python_exe(), "-m", "bodhan_agent", "--task", t["task"], "--out", str(out), "--max-cost-inr", str(t["max_cost_inr"]),
           "--files", *[str(RECIPE / f) for f in t["files"]]]
    print(f"\n=== {t['id']}: {t['task']}\n    expect: {t['expect']}", flush=True)
    subprocess.run(cmd, cwd=RECIPE)
    try:
        rep = json.loads((out / "report.json").read_text(encoding="utf-8"))
        return {"id": t["id"], "status": rep["status"], "cost": rep["total_cost_inr"], "time": rep["total_time_s"], "expect": t["expect"]}
    except Exception:
        return {"id": t["id"], "status": "no report", "cost": 0.0, "time": 0.0, "expect": t["expect"]}


def collect() -> None:
    for t in TASKS:
        src, dst = OUT / t["id"], RESULTS / t["id"]
        if not src.exists():
            print(f"skip {t['id']} (not run)")
            continue
        shutil.rmtree(dst, ignore_errors=True)
        (dst / "artifacts").mkdir(parents=True)
        for name in ("result.md", "report.json", "trace.jsonl"):
            if (src / name).exists():
                shutil.copy2(src / name, dst / name)
        for f in (src / "artifacts").rglob("*"):
            if f.is_file() and f.suffix.lower() in TEXT_SUFFIXES:
                shutil.copy2(f, dst / "artifacts" / f.name)
        print(f"collected {t['id']}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--list", action="store_true")
    p.add_argument("--only", help="comma-separated task ids")
    p.add_argument("--collect", action="store_true")
    p.add_argument("--pause-s", type=float, default=30, help="seconds to wait between tasks, to stay under LLM per-minute limits (default 30)")
    a = p.parse_args()
    if a.list:
        for t in TASKS:
            print(f"{t['id']:26} max Rs {t['max_cost_inr']:<4} {t['task']}")
        return
    if a.collect:
        return collect()
    left = remaining()
    if left is not None and left < 2:
        raise SystemExit(f"Only Rs {left:.2f} of credit left in the ledger; stopping.")
    chosen = [t for t in TASKS if not a.only or t["id"] in a.only.split(",")]
    rows = []
    for i, t in enumerate(chosen):
        if i and a.pause_s:
            print(f"\n(waiting {a.pause_s:.0f}s to avoid LLM rate limits)", flush=True)
            time.sleep(a.pause_s)
        rows.append(run(t))
    print("\n| task | status | cost Rs | time s | expected |\n|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['id']} | {r['status']} | {r['cost']:.4f} | {r['time']:.0f} | {r['expect']} |")
    print(f"\ntotal Rs {sum(r['cost'] for r in rows):.4f}. Update outputs/credit_ledger.json with this amount.")


if __name__ == "__main__":
    main()
