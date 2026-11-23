"""python -m bodhan_agent --task "..." --files path/ --out ./out [--max-steps 30] [--max-cost-inr 20]"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from bodhan_mcp import config
from bodhan_mcp.client import BodhanClient
from bodhan_mcp.tools import Toolkit

from .agent import Agent, Budget, Outcome, artifacts_dir
from .trace import Trace
from .verify import verify


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(override=False)
    except ImportError:
        pass


def inspect_inputs(kit: Toolkit, paths: list[str]) -> dict:
    files, missing = [], []
    for p in paths:
        if not Path(p).exists():
            missing.append(p)
            continue
        r = kit.inspect_media(p)
        files.extend(r["files"] if "files" in r else [r["file"]])
    return {"files": files, "missing": missing}


def write_result(out: Path, task: str, outcome: Outcome, ver: dict, report: dict) -> None:
    lines = [f"# Result: {ver['status']}", "", f"**Task:** {task}", "", "## Answer", "", outcome.answer.strip() or "_(none)_", ""]
    if ver["artifacts"]:
        lines += ["## Artifacts", ""] + [f"- [{Path(a).name}]({os.path.relpath(a, out)})" for a in ver["artifacts"]] + [""]
    if outcome.assumptions:
        lines += ["## Assumptions", ""] + [f"- {a}" for a in outcome.assumptions] + [""]
    if ver["checks"]:
        lines += ["## Verification", "", "| Artifact | Check | Passed |", "|---|---|---|"]
        lines += [f"| {Path(c['artifact']).name} | {c['check']} | {c['passed']} |" for c in ver["checks"]] + [""]
    if ver["notes"]:
        lines += ["## Notes", ""] + [f"- {n}" for n in ver["notes"]] + [""]
    lines += ["## Run", "", f"- Steps (tool calls): {report['steps']}", f"- Bodhan cost: Rs {report['cost_inr']['total']}",
              f"- Wall time: {report['elapsed_sec']} s", "- Generated audio is AI-generated speech (Bodhan Indic-Speak).",
              "- Full trace: [trace.jsonl](trace.jsonl)", ""]
    (out / "result.md").write_text("\n".join(lines), encoding="utf-8")


class _NullAgent:
    steps = 0
    plan = None
    tool_results: list = []


def main(argv: list[str] | None = None, chat=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m bodhan_agent")
    ap.add_argument("--task", required=True)
    ap.add_argument("--files", nargs="*", default=[], help="input files and/or folders")
    ap.add_argument("--out", default="./out")
    ap.add_argument("--max-steps", type=int, default=30)
    ap.add_argument("--max-cost-inr", type=float, default=20.0)
    ap.add_argument("--tool-timeout", type=float, default=300.0, help="seconds per tool call")
    args = ap.parse_args(argv)
    _load_dotenv()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    settings = config.Settings()
    client = BodhanClient(settings)
    kit = Toolkit(client, artifacts_dir(out), strict_provenance=True)
    trace = Trace(out / "trace.jsonl")
    budget = Budget(args.max_steps, args.max_cost_inr, args.tool_timeout)
    t0 = time.monotonic()

    overview = inspect_inputs(kit, args.files)
    try:
        if overview["missing"] and not overview["files"]:
            agent = _NullAgent()
            # Decided in code, not by the LLM: with no readable input there is nothing any tool can do.
            trace.log("task", task=args.task, inputs=overview)
            outcome = Outcome(status="not_possible", stop_reason="missing_inputs", answer=(
                "None of the input files exist: " + ", ".join(overview["missing"]) +
                ". Nothing was transcribed, translated, read or synthesised, and no output was produced. "
                "Please check the path and run again."))
        else:
            agent = Agent(kit, trace, budget, chat=chat)
            outcome = agent.run(args.task, overview)
    except (SystemExit, Exception) as e:  # config or LLM outage: still write an honest report
        msg = str(e) or e.__class__.__name__
        trace.log("error", error=msg)
        agent = _NullAgent()
        outcome = Outcome(status="failed", answer=f"The task could not be run: {msg}. No output was produced.",
                          stop_reason="error")
    ver = verify(outcome, agent.tool_results, artifacts_dir(out))
    trace.log("verify", **{k: ver[k] for k in ("status", "checks", "missing_artifacts", "notes")})

    by_model: dict[str, float] = {}
    for r in client.ledger.records:
        if not r.cached:
            by_model[r.model] = round(by_model.get(r.model, 0) + r.cost_inr, 4)
    report = {
        "status": ver["status"], "llm_status": outcome.status, "stop_reason": outcome.stop_reason,
        "task": args.task, "steps": agent.steps, "max_steps": budget.max_steps,
        "cost_inr": {"total": client.ledger.total_cost, "by_model": by_model, "budget": budget.max_cost_inr,
                     "within_budget": client.ledger.total_cost <= budget.max_cost_inr},
        "model_calls": {"made": sum(not r.cached for r in client.ledger.records),
                        "cache_hits": sum(r.cached for r in client.ledger.records)},
        "elapsed_sec": round(time.monotonic() - t0, 1),
        "plan": agent.plan, "assumptions": outcome.assumptions,
        "verification": ver, "artifacts": ver["artifacts"], "inputs": overview,
        "llm_model": os.environ.get("LLM_MODEL"),
    }
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_result(out, args.task, outcome, ver, report)
    print(f"[{ver['status']}] {out / 'result.md'}  (Rs {client.ledger.total_cost}, {agent.steps} steps)")
    return 0 if ver["status"] in ("completed", "not_possible") else 1
