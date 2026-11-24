import argparse
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from .agent import run


def main() -> int:
    ap = argparse.ArgumentParser(prog="bodhan_agent", description="Bodhan task agent")
    ap.add_argument("--task", required=True)
    ap.add_argument("--files", nargs="*", default=[], help="files or directories")
    ap.add_argument("--out", default="./out")
    ap.add_argument("--max-steps", type=int, default=30)
    ap.add_argument("--max-cost-inr", type=float, default=20.0)
    ap.add_argument("--timeout-per-call", type=int, default=120, help="seconds per tool/LLM call")
    ap.add_argument("--max-seconds", type=int, default=600, help="wall-clock limit")
    ap.add_argument("--no-cache", action="store_true")
    a = ap.parse_args()
    load_dotenv()
    status = asyncio.run(run(a))
    print(f"status: {status}  (see {a.out}/result.md)", file=sys.stderr)
    try:
        r = json.loads((Path(a.out) / "report.json").read_text())
        arts = ", ".join(Path(d["path"]).name for d in r.get("deliverables", []))
        print(f"cost: Rs {r['cost_inr']['total']} of Rs {a.max_cost_inr}  steps: {r.get('steps', '?')}  files: {arts or 'none'}", file=sys.stderr)
    except (OSError, KeyError, ValueError):  # the summary line is a convenience, never a failure
        return 0 if status in ("completed", "completed_with_warnings", "unsupported") else 2 if status in ("partial", "budget_exhausted") else 3
    return {"completed": 0, "completed_with_warnings": 0, "unsupported": 0, "partial": 2, "budget_exhausted": 2}.get(status, 3)
