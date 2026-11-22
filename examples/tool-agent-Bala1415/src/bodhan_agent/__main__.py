"""
Entry point for: python -m bodhan_agent

CLI interface for the Bodhan Task Agent.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import click
from dotenv import load_dotenv

load_dotenv()


@click.command()
@click.option("--task", required=True, help="Task description in plain language.")
@click.option("--files", required=True, help="Path to input file or directory of input files.")
@click.option("--out", default="./out", help="Output directory for results. Default: ./out")
@click.option("--max-steps", default=30, type=int, help="Maximum number of agent steps. Default: 30")
@click.option("--max-cost-inr", default=20.0, type=float, help="Maximum cost budget in INR. Default: 20")
@click.option("--timeout", default=300, type=int, help="Overall timeout in seconds. Default: 300")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose/debug logging.")
def main(
    task: str,
    files: str,
    out: str,
    max_steps: int,
    max_cost_inr: float,
    timeout: int,
    verbose: bool,
):
    """
    Bodhan Task Agent — executes multi-step language tasks using Bodhan AI's Indic models.

    Example:
        python -m bodhan_agent \\
          --task "Transcribe this Hindi voice note and give me English subtitles" \\
          --files path/to/inputs/ \\
          --out ./out
    """
    # Configure logging
    log_level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],
    )

    # Validate inputs
    files_path = Path(files)
    if not files_path.exists():
        click.echo(f"Error: Input path does not exist: {files}", err=True)
        sys.exit(1)

    # Check required env vars
    missing = []
    for var in ["LLM_BASE_URL", "LLM_API_KEY"]:
        if not os.getenv(var):
            missing.append(var)
    if missing:
        click.echo(
            f"Error: Missing required environment variables: {', '.join(missing)}\n"
            f"Copy .env.example to .env and fill in your keys.",
            err=True,
        )
        sys.exit(1)

    # Check Bodhan keys
    bodhan_keys = [
        "BODHAN_TRANSCRIBE_KEY", "BODHAN_TRANSLATE_KEY",
        "BODHAN_SPEAK_KEY", "BODHAN_OCR_KEY",
    ]
    missing_bodhan = [k for k in bodhan_keys if not os.getenv(k)]
    if missing_bodhan:
        click.echo(
            f"Warning: Missing Bodhan API keys: {', '.join(missing_bodhan)}\n"
            f"Some tools may not work.",
            err=True,
        )

    click.echo(f"🚀 Bodhan Task Agent")
    click.echo(f"   Task: {task}")
    click.echo(f"   Files: {files_path}")
    click.echo(f"   Output: {out}")
    click.echo(f"   Budget: {max_steps} steps, ₹{max_cost_inr}, {timeout}s timeout")
    click.echo()

    # Import and run
    from bodhan_agent.agent import Agent

    agent = Agent(
        task=task,
        files_dir=str(files_path),
        output_dir=out,
        max_steps=max_steps,
        max_cost_inr=max_cost_inr,
        timeout_seconds=float(timeout),
    )

    result = asyncio.run(agent.run())

    # Print summary
    click.echo()
    click.echo(f"{'=' * 60}")
    click.echo(f"✅ Status: {result['status']}")
    click.echo(f"📝 Summary: {result['summary'][:200]}")
    if result.get("artifacts"):
        click.echo(f"📦 Artifacts:")
        for art in result["artifacts"]:
            click.echo(f"   - {art.get('name', 'unnamed')}: {art.get('description', '')}")
    click.echo(f"📊 Report: {out}/report.json")
    click.echo(f"📜 Trace: {out}/trace.jsonl")
    click.echo(f"{'=' * 60}")

    # Exit code based on status
    if result["status"] in ("completed", "partial"):
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
