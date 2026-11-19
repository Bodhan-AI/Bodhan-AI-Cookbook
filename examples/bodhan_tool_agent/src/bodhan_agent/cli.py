"""Command line entry point.

    python -m bodhan_agent --task "..." --files PATH --out DIR [--max-steps 30] [--max-cost-inr 20]

Exit codes: 0 completed, 2 partial or impossible (an honest, clean stop),
1 failed or an error before the run could start.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from bodhan_agent.agent import AgentConfig, TaskAgent
from bodhan_agent.llm import LLMError, OpenAICompatLLM
from bodhan_agent.report import RunOutcome
from bodhan_agent.tools_backend import McpToolBackend

log = logging.getLogger("bodhan_agent")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m bodhan_agent",
                                description="Run a language task on Bodhan models through the MCP tool server.")
    p.add_argument("--task", required=True, help="The task in plain language.")
    p.add_argument("--files", action="append", default=[],
                   help="Input file or directory. Repeat the flag or give a comma-separated list.")
    p.add_argument("--out", default="out", help="Output directory (default: ./out).")
    p.add_argument("--max-steps", type=int, default=30, help="Maximum planner turns (LLM calls). Default 30.")
    p.add_argument("--max-cost-inr", type=float, default=20.0,
                   help="Maximum spend on Bodhan models in rupees. Default 20.")
    p.add_argument("--call-timeout", type=float, default=300.0,
                   help="Timeout in seconds for one tool call (long media may need more). Default 300.")
    p.add_argument("--llm-timeout", type=float, default=120.0, help="Timeout in seconds per LLM request.")
    p.add_argument("--max-repair-rounds", type=int, default=2,
                   help="How often failed verification is sent back for repair before reporting partial.")
    p.add_argument("--transport", choices=("inprocess", "stdio"), default="inprocess",
                   help="Reach the MCP server in-process (default) or by spawning `python -m bodhan_mcp`.")
    p.add_argument("--verbose", "-v", action="store_true", help="Log each step to stderr.")
    return p


def split_files(values: list[str]) -> list[str]:
    """Accept repeated flags and comma lists, without breaking paths that contain commas."""
    out: list[str] = []
    for value in values:
        value = value.strip()
        if not value:
            continue
        if "," in value and not Path(value).expanduser().exists():
            out.extend(part.strip() for part in value.split(",") if part.strip())
        else:
            out.append(value)
    return out


def _load_dotenv() -> None:
    try:
        from dotenv import find_dotenv, load_dotenv
    except ImportError:
        return
    # Search from the working directory; the default starts next to this module.
    load_dotenv(find_dotenv(usecwd=True), override=False)


def prepare_environment(out_dir: Path, max_cost_inr: float) -> None:
    """Must run before the MCP runtime is created: the server reads these at start-up."""
    work = (out_dir / "work").resolve()
    work.mkdir(parents=True, exist_ok=True)
    os.environ["BODHAN_WORKDIR"] = str(work)
    os.environ["BODHAN_MAX_COST_INR"] = repr(float(max_cost_inr))


async def run_agent(config: AgentConfig, transport: str, llm_timeout: float) -> RunOutcome:
    llm = OpenAICompatLLM.from_env(timeout_s=llm_timeout)
    backend = McpToolBackend.stdio() if transport == "stdio" else McpToolBackend.inprocess()
    outcome: RunOutcome | None = None
    try:
        async with backend:
            outcome = await TaskAgent(config, llm, backend).run()
    except Exception:
        if outcome is None:
            raise
        # Outputs are already written; a noisy server shutdown should not turn the run into a failure.
        log.warning("tool server shutdown failed", exc_info=True)
    return outcome


def _write_startup_failure(out_dir: Path, task: str, message: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {"status": "failed", "exit_code": 1, "stop_reason": "startup_error", "task": task,
              "answer": message, "steps": 0, "tool_calls": 0, "total_cost_inr": 0.0, "artifacts": []}
    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out_dir / "result.md").write_text(f"# Task result\n\n**Status:** failed\n\n**Task:** {task}\n\n"
                                       f"The run could not start: {message}\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    _load_dotenv()
    out_dir = Path(args.out).expanduser().resolve()
    prepare_environment(out_dir, args.max_cost_inr)
    config = AgentConfig(
        task=args.task,
        inputs=split_files(args.files),
        out_dir=out_dir,
        max_steps=max(1, args.max_steps),
        max_cost_inr=max(0.0, args.max_cost_inr),
        call_timeout_s=args.call_timeout,
        max_repair_rounds=max(0, args.max_repair_rounds),
    )
    try:
        outcome = asyncio.run(run_agent(config, args.transport, args.llm_timeout))
    except LLMError as err:
        _write_startup_failure(out_dir, args.task, str(err))
        print(f"failed: {err}", file=sys.stderr)
        return 1
    except Exception as err:
        log.exception("run failed")
        _write_startup_failure(out_dir, args.task, f"{type(err).__name__}: {err}")
        print(f"failed: {type(err).__name__}: {err}", file=sys.stderr)
        return 1
    print(f"status: {outcome.status}  artifacts: {len(outcome.artifacts)}  result: {out_dir / 'result.md'}")
    return outcome.exit_code
