"""Command line: python -m bodhan_agent --task "..." --files path/ --out ./out [--max-steps 30] [--max-cost-inr 20]"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from .graph import run_task
from .llm import ConfigError, LLMClient, LLMSettings, RunConfig


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m bodhan_agent",
                                description="Complete an Indian-language media task with Bodhan's models.")
    p.add_argument("--task", required=True, help="The task in plain language.")
    p.add_argument("--files", nargs="*", default=[], help="Input files and/or folders (folders are read recursively).")
    p.add_argument("--out", default="./out", help="Output folder (result.md, artifacts/, trace.jsonl, report.json).")
    p.add_argument("--max-steps", type=int, default=30, help="Maximum tool calls (default 30).")
    p.add_argument("--max-cost-inr", type=float, default=20.0, help="Maximum Bodhan spend in INR (default 20).")
    p.add_argument("--call-timeout", type=float, default=180.0, help="Timeout per tool call in seconds (default 180).")
    p.add_argument("--max-seconds", type=float, default=600.0, help="Wall-clock limit for the run (default 600).")
    p.add_argument("--verbose", action="store_true", help="Log progress to stderr.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_dotenv(find_dotenv(usecwd=True), override=False)
    cfg = RunConfig(task=args.task, files=[Path(f) for f in args.files], out_dir=Path(args.out).resolve(),
                    max_steps=max(1, args.max_steps), max_cost_inr=max(0.0, args.max_cost_inr),
                    call_timeout=max(10.0, args.call_timeout), max_seconds=max(30.0, args.max_seconds))
    try:
        settings = LLMSettings.from_env()
    except ConfigError as exc:
        cfg.out_dir.mkdir(parents=True, exist_ok=True)
        report = {"task": cfg.task, "status": "failed", "status_reason": str(exc)}
        (cfg.out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        (cfg.out_dir / "result.md").write_text(f"# Task result\n\n**Status:** `failed` — {exc}\n", encoding="utf-8")
        print(f"error: {exc}", file=sys.stderr)
        return 1
    report = asyncio.run(run_task(cfg, LLMClient(settings, timeout=cfg.call_timeout)))
    print(f"status: {report.get('status')} — {report.get('status_reason', '')}")
    print(f"cost: Rs {report.get('cost', {}).get('total_inr', 0):.4f}   outputs: {cfg.out_dir}")
    return 1 if report.get("status") == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
