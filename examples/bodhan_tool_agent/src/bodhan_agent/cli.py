"""CLI: python -m bodhan_agent --task "..." --files path/ --out ./out [--max-steps 30] [--max-cost-inr 20]"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from bodhan_core.config import load_dotenv_if_present
from bodhan_core.runtime import Runtime

from .agent import Agent
from .llm import LLMConfigError, OpenAICompatLLM


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bodhan_agent", description="Plan and run a multi-step Indic-language task with Bodhan tools.")
    p.add_argument("--task", required=True, help="Plain-language task.")
    p.add_argument("--files", nargs="*", default=[], help="Input files or folders (may be omitted for text-only tasks).")
    p.add_argument("--out", default="./out", help="Output folder (result.md, artifacts/, trace.jsonl, report.json).")
    p.add_argument("--max-steps", type=int, default=30, help="Maximum agent turns (default 30).")
    p.add_argument("--max-cost-inr", type=float, default=20.0, help="Hard cap on Bodhan model spend in rupees (default 20).")
    p.add_argument("--max-seconds", type=float, default=600.0, help="Wall-clock limit for the whole run (default 600).")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    load_dotenv_if_present()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    (out / "artifacts").mkdir(exist_ok=True)
    rt = Runtime.create(work_dir=out, max_cost_inr=args.max_cost_inr)
    llm = None
    llm_error = ""
    try:
        llm = OpenAICompatLLM.from_env()
    except LLMConfigError as e:
        llm_error = str(e)
    agent = Agent(rt, llm, out, max_steps=args.max_steps, max_seconds=args.max_seconds)
    state = agent.run(args.task, args.files)
    if llm_error and state.status == "failed":
        state.errors.append(llm_error)
    print(f"status: {state.status}")
    print(f"summary: {state.summary}")
    print(f"cost: Rs {rt.ledger.spent:.3f}  steps: {state.steps}  results: {out / 'result.md'}")
    return 0 if state.status in ("completed", "impossible") else 1


if __name__ == "__main__":
    sys.exit(main())
