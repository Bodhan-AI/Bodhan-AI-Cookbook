"""Show one finished run at a glance: status, the tool sequence, files and checks.

Run from examples/indic_task_agent:  python scripts/show_run.py out/4_injection
"""

import json
import sys
from pathlib import Path

run = Path(sys.argv[1])
r = json.loads((run / "report.json").read_text(encoding="utf-8"))
print(f"STATUS: {r['status']} ({r['stop_reason']}) | steps {r['steps']} | {r['elapsed_s']} s | Rs {r['cost_inr']}")
print("SUMMARY:", r["summary"])
print("\nSTEPS:")
for line in (run / "trace.jsonl").read_text(encoding="utf-8").splitlines():
    e = json.loads(line)
    if e["type"] == "tool":
        err = "" if e["ok"] else f"  ERROR {e['result']['error']['code']}"
        flag = "  [instructions found in file content: ignored]" if e.get("injection_suspected") else ""
        print(f"  {'ok ' if e['ok'] else 'ERR'} {e['tool']:20} {e['latency_s']:>6} s   Rs {e['cost_inr']}{err}{flag}")
    elif e["type"] == "finish_request":
        print(f"  --> finish({e['status']})  accepted={e['accepted']}")
print("\nFILES:")
for o in r["outputs"]:
    print(f"  {'ok     ' if o['exists'] else 'MISSING'} {o['path']}")
for v in r["verifications"]:
    print(f"VERIFIED: passed={v['passed']} recall={v['word_recall']} precision={v['word_precision']}")
for key in ("assumptions", "limitations", "warnings", "injection_flags"):
    if r.get(key):
        print(f"{key.upper()}: {r[key]}")