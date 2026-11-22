"""CLI entry point for the Bodhan task agent.

Usage:
    python -m bodhan_agent \\
        --task "Make a two-minute Tamil audio summary of this chapter" \\
        --files path/to/inputs/ \\
        --out ./out \\
        [--max-steps 30] [--max-cost-inr 20]
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Ensure src is on sys.path
src_dir = str(Path(__file__).resolve().parent.parent)
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import click  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv()


@click.command()
@click.option("--task", required=True, help="Plain-language description of what to do.")
@click.option(
    "--files",
    required=True,
    type=click.Path(exists=True),
    help="Path to a single file or a directory of input files.",
)
@click.option(
    "--out",
    required=True,
    type=click.Path(),
    help="Directory to write outputs (result.md, artifacts/, trace.jsonl, report.json).",
)
@click.option("--max-steps", default=30, show_default=True, help="Maximum tool-call steps.")
@click.option("--max-cost-inr", default=20.0, show_default=True, help="Maximum spend in INR.")
def main(task: str, files: str, out: str, max_steps: int, max_cost_inr: float) -> None:
    """Bodhan task agent - multi-step Indic-language AI pipeline."""
    from .agent import run

    input_path = Path(files)
    if input_path.is_dir():
        file_list = sorted(
            p for p in input_path.iterdir()
            if p.is_file() and not p.name.startswith(".")
        )
    else:
        file_list = [input_path]

    if not file_list:
        click.echo("Error: no input files found.", err=True)
        sys.exit(1)

    asyncio.run(
        run(
            task=task,
            files=file_list,
            out_dir=Path(out),
            max_steps=max_steps,
            max_cost_inr=max_cost_inr,
        )
    )


if __name__ == "__main__":
    main()
