"""Cost and runtime per 100 items, with and without speech checks, from a report's run_log.jsonl.

python tools/cost_runtime.py report_demo --concurrency 2

Cost: each distinct model call once (cache records keep the original cost), so a cached rerun gives the same figure.
Runtime: the original latency of every call (kept in the cache), summed per service and divided by the concurrency,
which estimates a cold run on a fresh cache. Calls cached before latencies were recorded are counted and reported, not
guessed.
"""

import argparse
import json
import sys
from pathlib import Path

SPEECH = {"transcribe", "speak"}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("report", help="report folder with run_log.jsonl and summary.json")
    p.add_argument("--concurrency", type=int, default=4, help="the --concurrency the cold run would use")
    a = p.parse_args()
    folder = Path(a.report)
    items = json.loads((folder / "summary.json").read_text(encoding="utf-8"))["items"]
    calls = {}
    for line in (folder / "run_log.jsonl").read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        if e.get("kind") == "call":
            lat = e.get("latency_s") if e.get("cache") == "miss" else e.get("orig_latency_s")
            calls[e["key"]] = (e["service"], e.get("cost_inr", 0.0), lat)
    rows = {}
    for service, cost, lat in calls.values():
        r = rows.setdefault(service, {"calls": 0, "cost_inr": 0.0, "seconds": 0.0, "timed": 0})
        r["calls"] += 1
        r["cost_inr"] += cost
        if lat is not None:
            r["seconds"] += lat
            r["timed"] += 1
    scale = 100 / max(1, items)
    print(f"{items} items, {len(calls)} distinct model calls, concurrency {a.concurrency}\n")
    print("| service | calls | timed | cost per 100 items (INR) | minutes per 100 items |")
    print("| --- | --- | --- | --- | --- |")
    for s, r in sorted(rows.items()):
        mins = r["seconds"] / a.concurrency / 60 * scale
        print(f"| {s} | {r['calls']} | {r['timed']} | {r['cost_inr'] * scale:.3f} | {mins:.1f} |")
    for name, keep in (("with speech checks", lambda s: True), ("without speech checks", lambda s: s not in SPEECH)):
        sel = [r for s, r in rows.items() if keep(s)]
        cost = sum(r["cost_inr"] for r in sel) * scale
        mins = sum(r["seconds"] for r in sel) / a.concurrency / 60 * scale
        untimed = sum(r["calls"] - r["timed"] for r in sel)
        note = f" ({untimed} calls have no recorded latency)" if untimed else ""
        print(f"\nPer 100 items {name}: INR {cost:.2f}, about {mins:.1f} min{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
