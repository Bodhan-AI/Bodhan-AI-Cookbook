"""Task agent CLI:

    python -m bodhan_agent --task "..." --files PATH --out DIR [--max-steps 30] [--max-cost-inr 20]

Writes DIR/result.md, DIR/artifacts/, DIR/trace.jsonl and DIR/report.json.
"""

import argparse
import asyncio
import json
import os
import time
from pathlib import Path

from openai import AsyncOpenAI

from bodhan_mcp import tools
from bodhan_mcp.bodhan import Bodhan, Budget, load_env

from .agent import Agent


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--task", required=True, help="what to do, in plain language")
    p.add_argument("--files", required=True, help="input file or folder")
    p.add_argument("--out", required=True, help="output folder")
    p.add_argument("--max-steps", type=int, default=30, help="max tool calls")
    p.add_argument("--max-cost-inr", type=float, default=20.0, help="max rupees spent on Bodhan calls")
    p.add_argument("--max-seconds", type=float, default=600, help="wall-clock limit for the whole run")
    p.add_argument("--call-timeout", type=float, default=120, help="timeout per Bodhan call, seconds")
    return p.parse_args(argv)


async def run(args: argparse.Namespace, llm: AsyncOpenAI | None = None, bodhan: Bodhan | None = None) -> dict:
    """`llm` and `bodhan` can be injected (tests); by default they are built from the environment."""
    t0 = time.monotonic()
    out = Path(args.out)
    (out / "artifacts").mkdir(parents=True, exist_ok=True)
    bodhan = bodhan or Bodhan(budget=Budget(args.max_cost_inr), timeout_s=args.call_timeout)
    budget = bodhan.budget
    tools.configure(bodhan, out / "artifacts")
    model = os.environ.get("LLM_MODEL", "")
    outcome = {"final": None, "stop_reason": None}
    agent = None

    # Preflight in code: free, deterministic, and no LLM needed to notice these.
    files = Path(args.files).expanduser()
    if not files.exists():
        outcome["final"] = {"status": "impossible", "answer": f"The input path `{args.files}` does not exist, so "
                            "there is nothing to work on. No output was produced.", "assumptions": [], "artifacts": []}
    elif llm is None and not all(os.environ.get(k) for k in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY")):
        outcome["stop_reason"] = "LLM_BASE_URL, LLM_MODEL and LLM_API_KEY must be set (see .env.example)"
    else:
        manifest = await tools.inspect_media(str(files))
        llm = llm or AsyncOpenAI(base_url=os.environ["LLM_BASE_URL"], api_key=os.environ["LLM_API_KEY"],
                                 timeout=90, max_retries=4)
        agent = Agent(out=out, bodhan=bodhan, llm=llm, model=model, max_steps=args.max_steps,
                      max_seconds=args.max_seconds)
        outcome = await agent.run(args.task, manifest)
    await bodhan.aclose()
    report = write_outputs(args, out, outcome, budget, agent, model, time.monotonic() - t0)
    return report


def write_outputs(args, out: Path, outcome: dict, budget: Budget, agent, model: str, elapsed: float) -> dict:
    final = outcome["final"]
    if final and final["status"] == "impossible":  # nothing is delivered for an impossible task: no partial or substitute files
        for p in (out / "artifacts").rglob("*"):
            if p.is_file():
                p.unlink()
        final["artifacts"] = []
    produced = sorted(str(p.resolve()) for p in (out / "artifacts").rglob("*") if p.is_file())
    if final is None:  # stopped by a budget or an error before the LLM called finish
        final = {"status": "stopped", "answer": f"The agent stopped before finishing: {outcome['stop_reason']}. "
                 "Files written so far are listed below; they may be incomplete.", "assumptions": [],
                 "artifacts": produced}
    by_model: dict[str, float] = {}
    for c in budget.calls:
        by_model[c["model"]] = round(by_model.get(c["model"], 0) + c["cost_inr"], 4)
    report = {
        "status": final["status"], "task": args.task, "files": args.files, "stop_reason": outcome["stop_reason"],
        "steps": agent.steps if agent else 0, "max_steps": args.max_steps,
        "cost_inr": round(budget.spent_inr, 4), "max_cost_inr": args.max_cost_inr, "cost_by_model": by_model,
        "bodhan_calls": len(budget.calls), "cache_hits": sum(c["cached"] for c in budget.calls),
        "elapsed_s": round(elapsed, 1), "llm": {"model": model, "total_tokens": agent.tokens if agent else 0},
        "artifacts": final["artifacts"], "missing_artifacts": final.get("missing_artifacts", []),
        "errors": agent.errors if agent else [], "verification": [],
    }
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if not (out / "trace.jsonl").exists():
        (out / "trace.jsonl").write_text("")
    (out / "result.md").write_text(render_result(report, final, out), encoding="utf-8")
    return report


def render_result(report: dict, final: dict, out: Path) -> str:
    def link(p: str) -> str:
        rel = os.path.relpath(p, out)
        return f"- [{Path(p).name}]({rel})"

    lines = [f"# Result: {report['status']}", "", f"**Task:** {report['task']}", "", "## Answer", "", final["answer"], ""]
    lines += ["## Output files", ""] + ([link(p) for p in final["artifacts"]] or ["None."]) + [""]
    if final.get("missing_artifacts"):
        lines += ["Claimed but not found (not produced): " + ", ".join(final["missing_artifacts"]), ""]
    lines += ["## Assumptions", ""] + ([f"- {a}" for a in final["assumptions"]] or ["None."]) + [""]
    lines += ["## Run", "", f"- Steps: {report['steps']} of {report['max_steps']}",
              f"- Bodhan cost: Rs {report['cost_inr']:.4f} of Rs {report['max_cost_inr']:g} "
              f"({report['bodhan_calls']} calls, {report['cache_hits']} from cache)",
              f"- Time: {report['elapsed_s']} s", f"- LLM: {report['llm']['model']}"]
    if report["stop_reason"]:
        lines.append(f"- Stopped: {report['stop_reason']}")
    if report["errors"]:
        lines.append(f"- Tool errors: {len(report['errors'])} (see report.json)")
    if any(p.endswith(".mp3") for p in final["artifacts"]):
        lines += ["", "Audio files are AI-generated speech (Bodhan Indic-Speak voices)."]
    lines += ["", "Step-by-step trace: `trace.jsonl`. Totals: `report.json`."]
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    load_env()
    report = asyncio.run(run(args))
    print(f"{report['status']}: {Path(args.out) / 'result.md'}  (Rs {report['cost_inr']:.4f}, {report['steps']} steps, "
          f"{report['elapsed_s']} s)")


if __name__ == "__main__":
    main()
