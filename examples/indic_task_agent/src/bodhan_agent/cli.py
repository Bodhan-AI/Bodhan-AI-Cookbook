"""Command line entry point.

    python -m bodhan_agent --task "..." --files path/to/inputs/ --out ./out [--max-steps 30] [--max-cost-inr 20]
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path

from bodhan_mcp.client import BodhanClient
from bodhan_mcp.config import BodhanKeys, load_env_file
from bodhan_mcp.runtime import CostLedger, ResultCache, RunContext

from .llm import LLMConfigError, PlannerLLM
from .loop import Agent, Outcome, Settings, expand_inputs
from .report import write_outputs
from .trace import TraceWriter

OUR_OUTPUTS = ("result.md", "report.json", "trace.jsonl", "artifacts")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m bodhan_agent", description="Run an Indian-language media task with Bodhan tools.")
    p.add_argument("--task", required=True, help="The task in plain language.")
    p.add_argument("--files", nargs="*", default=[], help="Input files and/or folders.")
    p.add_argument("--out", required=True, help="Output folder (result.md, artifacts/, trace.jsonl, report.json).")
    p.add_argument("--max-steps", type=int, default=30, help="Maximum tool calls (default 30).")
    p.add_argument("--max-cost-inr", type=float, default=20.0, help="Maximum Bodhan spend in rupees (default 20).")
    p.add_argument("--max-seconds", type=float, default=600.0, help="Wall-clock limit for the whole run (default 600).")
    p.add_argument("--call-timeout", type=float, default=60.0, help="Per-request timeout for Bodhan calls in seconds (default 60).")
    p.add_argument("--llm-timeout", type=float, default=120.0, help="Per-request timeout for planner LLM calls (default 120).")
    p.add_argument("--concurrency", type=int, default=4, help="Parallel requests per Bodhan model (default 4).")
    p.add_argument("--cache-dir", default="~/.cache/bodhan_tool_agent", help="Result cache (reruns do not repeat paid calls).")
    p.add_argument("--no-cache", action="store_true", help="Disable the on-disk cache (in-run de-duplication still applies).")
    p.add_argument("--env-file", default=None, help="Optional .env file with the Bodhan/LLM variables.")
    p.add_argument("--llm-input-inr-per-1m", type=float, default=None, help="Optional LLM price (INR per 1M prompt tokens) to count LLM spend in the budget.")
    p.add_argument("--llm-output-inr-per-1m", type=float, default=None, help="Optional LLM price (INR per 1M completion tokens).")
    return p.parse_args(argv)


def prepare_out(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    stale = [out / n for n in OUR_OUTPUTS if (out / n).exists()]
    if stale:  # keep earlier results instead of deleting them, but never mix them with this run's artifacts
        keep = out / ".previous_runs" / datetime.now().strftime("%Y%m%d-%H%M%S")
        keep.mkdir(parents=True, exist_ok=True)
        for s in stale:
            shutil.move(str(s), str(keep / s.name))
    (out / "artifacts").mkdir()


async def run(args: argparse.Namespace, *, transport=None, llm: PlannerLLM | None = None) -> dict:
    """Run one task. `transport`/`llm` are injection points for the offline tests only."""
    load_env_file(args.env_file)
    out = Path(args.out).resolve()
    prepare_out(out)
    started_at = datetime.now(UTC)
    t0 = time.monotonic()
    trace = TraceWriter(out / "trace.jsonl")
    files, missing, roots = expand_inputs(args.files)
    ledger = CostLedger(max_inr=args.max_cost_inr)
    cache = ResultCache(None if args.no_cache else Path(args.cache_dir).expanduser())
    client = BodhanClient(BodhanKeys.from_env(), ledger, cache, trace.tool_event, timeout=args.call_timeout, concurrency=args.concurrency,
                          transport=transport, deadline=time.monotonic() + args.max_seconds)
    ctx = RunContext(artifacts_dir=out / "artifacts", ledger=ledger, cache=cache, client=client, read_roots=roots, trace=trace.tool_event)
    settings = Settings(max_steps=max(2, args.max_steps), max_cost_inr=args.max_cost_inr, max_seconds=args.max_seconds,
                        llm_input_inr_per_1m=args.llm_input_inr_per_1m, llm_output_inr_per_1m=args.llm_output_inr_per_1m)
    llm_error = None
    if llm is None:
        try:
            llm = PlannerLLM(timeout=args.llm_timeout)
        except LLMConfigError as exc:
            llm_error = str(exc)
    agent = Agent(args.task, ctx, trace, settings, llm, files, missing, llm_error)
    try:
        await agent.run()
    except Exception as exc:  # never leave a run without a report
        trace.write({"type": "internal_error", "error": repr(exc), "traceback": traceback.format_exc()[-3000:]})
        produced = [str(a.path) for a in ctx.artifacts.values() if (a.verification or {}).get("status") not in ("fail",)]
        agent.outcome = Outcome("failed", "internal_error", f"The agent hit an internal error and stopped: {type(exc).__name__}: {exc}",
                                produced, [], ["Internal error; outputs listed (if any) were not confirmed by the planner."])
    finally:
        await client.aclose()
    elapsed = time.monotonic() - t0
    trace.write({"type": "run_end", "status": agent.outcome.status, "elapsed_s": round(elapsed, 2), "cost": ledger.summary()})
    return write_outputs(agent, out, started_at, elapsed)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    report = asyncio.run(run(args))
    c = report["cost"]
    print(f"status={report['status']} stop={report['stop_reason']} steps={report['steps']['used']} "
          f"bodhan_inr={c['total_counted_inr']} elapsed_s={report['elapsed_s']} out={Path(args.out).resolve()}")
    sys.exit(0)
