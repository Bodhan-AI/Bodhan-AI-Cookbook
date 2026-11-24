"""Command line: python -m bodhan_agent --task "..." --files ... --out ./out

Exit codes: 0 = the agent ran and wrote a report (whatever its status: completed, partial,
failed or impossible; read report.json); 2 = it could not start (bad settings or arguments).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from bodhan_agent.llm import LLMClient
from bodhan_agent.loop import Agent
from bodhan_mcp import costs
from bodhan_mcp.client import BodhanClient
from bodhan_mcp.config import load_settings
from bodhan_mcp.toolkit import Toolkit


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bodhan_agent", description="Run a plain-language task with Bodhan tools.")
    p.add_argument("--task", required=True, help="Plain-language task description.")
    p.add_argument("--files", nargs="*", default=[], help="Input file(s) or folder(s).")
    p.add_argument("--out", required=True, help="Output folder for this run.")
    p.add_argument("--max-steps", type=int, default=30, help="Maximum agent steps (default 30).")
    p.add_argument("--max-cost-inr", type=float, default=20.0, help="Maximum spend in rupees (default 20).")
    p.add_argument("--max-seconds", type=float, default=900.0, help="Wall-clock limit in seconds (default 900).")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    settings = load_settings()
    missing = [n for n, v in (("LLM_BASE_URL", settings.llm_base_url), ("LLM_MODEL", settings.llm_model),
                              ("LLM_API_KEY", settings.llm_api_key)) if not v]
    if missing:
        print(f"Cannot start: set {', '.join(missing)} in .env or the environment.", file=sys.stderr)
        return 2
    tracker = costs.CostTracker(args.max_cost_inr)
    toolkit = Toolkit(BodhanClient(settings, tracker=tracker), args.out)
    llm = LLMClient(settings.llm_base_url, settings.llm_model, settings.llm_api_key)
    report = Agent(llm, toolkit, task=args.task, files=args.files, max_steps=args.max_steps,
                   max_cost_inr=args.max_cost_inr, max_seconds=args.max_seconds).run()
    print(f"Status: {report['status']}  (stop reason: {report['stop_reason']})")
    print(report["summary"])
    for o in report["outputs"]:
        print(f"  - {o['path']}{'' if o['exists'] else '  (MISSING)'}")
    print(f"Cost: Rs {report['cost_inr']} | steps: {report['steps']} | time: {report['elapsed_s']} s")
    print(f"Details: {toolkit.out_dir / 'result.md'}")
    return 0