"""Run the demo task suite and report a pass rate (stretch goal: self-evaluation).

    python scripts/self_eval.py                  # all tasks in tasks/demo_tasks.yaml
    python scripts/self_eval.py --only telugu_clips,french_video_impossible
    python scripts/self_eval.py --summarize      # rebuild summary.md from the reports on disk, no model calls

Writes outputs/eval/<task id>/ (each run's result.md, artifacts, trace, report) and outputs/eval/summary.md.
The summary always covers every task with a report on disk, so rerunning one task keeps the others.
A task passes when its final status is one of `expect` and its extra checks hold. Reruns are cheap:
every Bodhan response is cached by its exact input.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bodhan_agent.agent import Agent, RunConfig  # noqa: E402
from bodhan_agent.llm import OpenAICompatLLM  # noqa: E402


def check(spec: dict, report: dict, out: Path) -> list[str]:
    problems = []
    if report["status"] not in spec["expect"]:
        problems.append(f"status {report['status']} not in {spec['expect']}")
    arts = report.get("artifacts", [])
    for ext in spec.get("artifacts", []):
        if not any(a["path"].endswith(ext) for a in arts) and len(spec["artifacts"]) == 1:
            problems.append(f"no {ext} artifact")
    if spec.get("artifacts") and len(spec["artifacts"]) > 1 and not any(any(a["path"].endswith(e) for e in spec["artifacts"]) for a in arts):
        problems.append(f"no artifact of type {spec['artifacts']}")
    if spec.get("no_artifacts") and arts:
        problems.append("produced artifacts for a task that should produce none")
    answer = (out / "result.md").read_text(encoding="utf-8")
    for word in spec.get("answer_mentions", []):
        if word not in answer:
            problems.append(f"answer does not mention {word}")
    if spec.get("warning_contains") and not any(spec["warning_contains"] in w for w in report.get("warnings", [])):
        problems.append(f"no warning containing {spec['warning_contains']!r}")
    if report["cost"].get("total_inr", 0) > spec.get("max_cost_inr", 20) + 1e-9:
        problems.append("over budget")
    return problems


async def run_all(specs: list[dict], out_root: Path) -> list[dict]:
    rows = []
    for spec in specs:
        out = out_root / spec["id"]
        t0 = time.time()
        cfg = RunConfig(task=spec["task"], files=str(ROOT / spec["files"]) if spec.get("files") else None, out=out,
                        max_steps=spec.get("max_steps", 30), max_cost_inr=spec.get("max_cost_inr", 20.0))
        report = await Agent(cfg, OpenAICompatLLM()).run()
        problems = check(spec, report, out)
        rows.append({"id": spec["id"], "task": spec["task"], "status": report["status"], "passed": not problems, "problems": problems,
                     "cost_inr": report["cost"].get("total_inr", 0), "time_s": round(time.time() - t0, 1),
                     "tool_calls": report["steps"]["tool_calls"]})
        print(f"{'PASS' if not problems else 'FAIL'}  {spec['id']:32} {report['status']:16} ₹{rows[-1]['cost_inr']:.3f}  {rows[-1]['time_s']}s  {'; '.join(problems)}")
    return rows


def summarize(specs: list[dict], out_root: Path) -> list[dict]:
    """Rebuild summary.md and summary.json from every task's report.json on disk."""
    rows = []
    for spec in specs:
        out = out_root / spec["id"]
        if not (out / "report.json").exists():
            continue
        report = json.loads((out / "report.json").read_text(encoding="utf-8"))
        problems = check(spec, report, out)
        rows.append({"id": spec["id"], "task": spec["task"], "status": report["status"], "passed": not problems, "problems": problems,
                     "cost_inr": report["cost"].get("total_inr", 0), "time_s": round(report.get("wall_time_s", 0), 1),
                     "tool_calls": report["steps"]["tool_calls"]})
    passed = sum(r["passed"] for r in rows)
    lines = [f"# Self-evaluation: {passed}/{len(rows)} tasks passed", "",
             "| Task | Status | Pass | Cost (₹) | Time (s) | Tool calls | Problems |", "| --- | --- | --- | --- | --- | --- | --- |"]
    lines += [f"| {r['id']} | {r['status']} | {'yes' if r['passed'] else 'no'} | {r['cost_inr']:.3f} | {r['time_s']} | {r['tool_calls']} | "
              f"{'; '.join(r['problems'])} |" for r in rows]
    lines.append(f"\nTotal cost ₹{sum(r['cost_inr'] for r in rows):.3f}")
    (out_root / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out_root / "summary.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"\n{passed}/{len(rows)} passed. Summary: {out_root / 'summary.md'}")
    return rows


def main() -> int:
    import logging

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s", force=True)
    for noisy in ("httpx", "mcp", "openai", "httpcore", "bodhan_mcp.client"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", default=str(ROOT / "tasks" / "demo_tasks.yaml"))
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default=str(ROOT / "outputs" / "eval"))
    ap.add_argument("--summarize", action="store_true", help="only rebuild the summary from reports on disk")
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")
    all_specs = yaml.safe_load(Path(args.tasks).read_text(encoding="utf-8"))
    wanted = set(args.only.split(",")) if args.only else None
    specs = [s for s in all_specs if wanted is None or s["id"] in wanted]
    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    if not args.summarize:
        asyncio.run(run_all(specs, out_root))
    rows = summarize(all_specs, out_root)
    return 0 if all(r["passed"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
