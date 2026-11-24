"""Command-line interface for Bodhan Task Agent.

Usage:
    python -m bodhan_agent --task "..." --files DIR --out ./out [--max-steps 30] [--max-cost-inr 20]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import dotenv

from bodhan_agent.loop import AgentLoop
from bodhan_agent.report import unwrap_finish_explanation


def parse_args(args=None):
    parser = argparse.ArgumentParser(
        description="Bodhan Task Agent: Autonomous Indian Language Workflow Runner."
    )
    parser.add_argument(
        "--task",
        type=str,
        required=True,
        help="Task description or goal for the agent to achieve.",
    )
    parser.add_argument(
        "--files",
        type=str,
        required=True,
        help="Path to directory containing input files for the task.",
    )
    parser.add_argument(
        "--out",
        type=str,
        default="./out",
        help="Path to directory where audit logs, reports, and artifacts are saved (default: ./out).",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=30,
        help="Maximum number of loop execution steps allowed (default: 30).",
    )
    parser.add_argument(
        "--max-cost-inr",
        type=float,
        default=20.0,
        help="Hard ceiling budget limit in INR across all Bodhan tools (default: Rs 20.00).",
    )
    return parser.parse_args(args)


async def run_agent_cli(args):
    # Load .env without echoing secrets
    dotenv.load_dotenv(override=True)

    files_dir = Path(args.files).resolve()
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir = out_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    if not files_dir.exists():
        print(f"ERROR: Files directory does not exist: {files_dir}", file=sys.stderr)
        sys.exit(1)

    print("=" * 60)
    print("BODHAN TASK AGENT")
    print("=" * 60)
    print(f"Task: {args.task}")
    print(f"Files Directory: {files_dir}")
    print(f"Output Directory: {out_dir}")
    print(f"Budget Limit: Rs {args.max_cost_inr:.2f}")
    print(f"Max Steps: {args.max_steps}")
    print("=" * 60)

    loop = AgentLoop(
        task=args.task,
        files_dir=files_dir,
        out_dir=out_dir,
        max_steps=args.max_steps,
        max_cost_inr=args.max_cost_inr,
    )

    result = await loop.run()

    print("\n" + "=" * 60)
    print("AGENT EXECUTION FINISHED")
    print("=" * 60)
    status_str = result.get("status", "unknown")
    print(f"Final Status: {status_str.upper()}")
    print(f"Total Steps: {result.get('steps', 0)}")
    print(f"Total Cost: Rs {result.get('total_cost_inr', 0.0):.4f}")
    print(
        f"Duration: {result.get('duration_seconds', 0.0):.2f}s "
        f"(LLM: {result.get('llm_ms', 0.0):.1f}ms, Tools: {result.get('tool_ms', 0.0):.1f}ms, "
        f"Rate-Limit Wait: {result.get('rate_limit_wait_ms', 0.0):.1f}ms, Overhead: {result.get('overhead_ms', 0.0):.1f}ms)"
    )
    print(f"Bodhan HTTP Requests (Ledger): {result.get('http_requests_count', 0)}")
    print(f"LLM Calls: {result.get('llm_calls', 0)}")
    print(f"LLM Tokens: {result.get('llm_tokens', 0)}")
    print(f"Groq 429s: {result.get('groq_429_count', 0)}")
    expl = unwrap_finish_explanation(result.get("explanation") or "") or "(none)"
    if status_str.lower() in (
        "failed",
        "partial",
        "budget_stopped",
        "budget_exceeded",
        "max_steps",
    ):
        print(f"Failure Explanation: {expl}")
    else:
        print(f"Explanation: {expl}")
    misses = result.get("quality_target_misses") or []
    if misses and status_str.lower() != "completed":
        print(f"Quality Misses: {'; '.join(str(m) for m in misses)}")
    print(f"Audit Trail Saved To: {out_dir / 'trace.jsonl'}")
    print(f"Report Saved To: {out_dir / 'report.json'}")
    print(f"Result Summary Saved To: {out_dir / 'result.md'}")
    print("=" * 60)


def main():
    args = parse_args()
    asyncio.run(run_agent_cli(args))


if __name__ == "__main__":
    main()
