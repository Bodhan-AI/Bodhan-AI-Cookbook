"""Command line entry: python -m bodhan_agent --task "..." --files path/ --out ./out

Exit codes: 0 completed, or impossible task correctly refused; 2 partial; 3 budget exhausted; 1 failed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from .agent import Agent, RunConfig
from .llm import LLMError, OpenAICompatLLM

EXIT = {"completed": 0, "impossible": 0, "partial": 2, "budget_exhausted": 3, "failed": 1}


class JsonFormatter(logging.Formatter):
    SKIP = set(vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()) | {"message", "asctime"}

    def format(self, record: logging.LogRecord) -> str:
        data = {"level": record.levelname.lower(), "logger": record.name, "msg": record.getMessage()}
        data.update({k: v for k, v in record.__dict__.items() if k not in self.SKIP})
        return json.dumps(data, ensure_ascii=False, default=str)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bodhan_agent", description="Run a language task with Bodhan's Indic models.")
    parser.add_argument("--task", required=True, help="The task in plain language.")
    parser.add_argument("--files", default=None, help="Input folder, file, or comma-separated files.")
    parser.add_argument("--out", required=True, type=Path, help="Output folder for result.md, artifacts/, trace.jsonl, report.json.")
    parser.add_argument("--max-steps", type=int, default=30, help="Maximum tool calls (default 30).")
    parser.add_argument("--max-cost-inr", type=float, default=20.0, help="Maximum Bodhan spend in rupees (default 20).")
    parser.add_argument("--max-minutes", type=float, default=20.0, help="Wall-clock limit (default 20).")
    parser.add_argument("--tool-timeout-s", type=float, default=300.0, help="Per tool call timeout (default 300).")
    parser.add_argument("--no-judge", action="store_true", help="Skip the LLM judge (deterministic checks still run).")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    load_dotenv()
    args.out.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(args.out / "run.log", encoding="utf-8")
    handler.setFormatter(JsonFormatter())
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    console.setLevel(logging.INFO if args.verbose else logging.WARNING)
    logging.basicConfig(level=logging.INFO, handlers=[handler, console], force=True)
    for noisy in ("httpx", "mcp", "openai", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    try:
        llm = OpenAICompatLLM()
    except LLMError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    cfg = RunConfig(task=args.task, files=args.files, out=args.out, max_steps=args.max_steps, max_cost_inr=args.max_cost_inr,
                    max_minutes=args.max_minutes, tool_timeout_s=args.tool_timeout_s, judge=not args.no_judge)
    report = asyncio.run(Agent(cfg, llm).run())
    print(f"status: {report['status']}  cost: ₹{report['cost'].get('total_inr', 0):.3f}  time: {report['wall_time_s']:.0f}s  "
          f"-> {args.out / 'result.md'}")
    return EXIT.get(report["status"], 1)


if __name__ == "__main__":
    sys.exit(main())
