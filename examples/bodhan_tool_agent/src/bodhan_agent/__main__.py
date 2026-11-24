"""CLI entry point for running the Bodhan Task Agent."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from bodhan_agent.agent import TaskAgent
from bodhan_mcp.client.client import _auto_load_env

_auto_load_env()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bodhan Autonomous Task Agent for Multi-Step Indic AI Workflows"
    )
    parser.add_argument(
        "--task",
        type=str,
        required=True,
        help="Plain-language description of the task to perform",
    )
    parser.add_argument(
        "--files",
        type=str,
        required=True,
        help="Path to input file or directory containing input files",
    )
    parser.add_argument(
        "--out",
        type=str,
        default="./out",
        help="Output directory for results, trace, artifacts and report (default: ./out)",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=30,
        help="Maximum number of tool execution steps before stopping (default: 30)",
    )
    parser.add_argument(
        "--max-cost-inr",
        type=float,
        default=20.0,
        help="Maximum total cost in INR before stopping cleanly (default: 20.0)",
    )
    parser.add_argument(
        "--llm-base-url",
        type=str,
        default=None,
        help="Base URL for OpenAI-compatible LLM endpoint (default: reads LLM_BASE_URL)",
    )
    parser.add_argument(
        "--llm-model",
        type=str,
        default=None,
        help="Model name for LLM (default: reads LLM_MODEL)",
    )
    parser.add_argument(
        "--llm-api-key",
        type=str,
        default=None,
        help="API key for LLM endpoint (default: reads LLM_API_KEY)",
    )

    args = parser.parse_args()

    agent = TaskAgent(
        task=args.task,
        files_path=args.files,
        out_dir=args.out,
        max_steps=args.max_steps,
        max_cost_inr=args.max_cost_inr,
        llm_base_url=args.llm_base_url,
        llm_model=args.llm_model,
        llm_api_key=args.llm_api_key,
    )

    result = agent.run()
    status = result.get("status", "unknown")
    if status in ("completed", "impossible_task"):
        return 0
    elif status == "budget_exceeded":
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
