
import argparse
import asyncio
import math
from pathlib import Path


def build_parser():
    """Create the OmniAgent AI command-line parser."""

    parser = argparse.ArgumentParser(
        prog="omniagent",
        description=(
            "OmniAgent AI — Agentic workflows "
            "powered by Bodhan MCP tools."
        ),
    )

    parser.add_argument(
        "--task",
        type=str,
        required=True,
        help="Natural-language task for the agent.",
    )

    parser.add_argument(
        "--files",
        type=Path,
        default=Path("data"),
        help="Directory containing input files.",
    )

    parser.add_argument(
        "--out",
        type=Path,
        default=Path("out"),
        help="Directory for generated outputs.",
    )

    parser.add_argument(
        "--max-steps",
        type=int,
        default=30,
        help="Maximum agent planning steps.",
    )

    parser.add_argument(
        "--max-tool-calls",
        type=int,
        default=10,
        help="Maximum MCP tool attempts.",
    )

    parser.add_argument(
        "--max-cost-inr",
        type=float,
        default=None,
        help=(
            "Optional INR spending limit. "
            "Not yet supported for live execution."
        ),
    )

    return parser


def parse_args(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.task.strip():
        parser.error("--task cannot be empty.")

    if args.max_steps <= 0:
        parser.error("--max-steps must be positive.")

    if args.max_tool_calls <= 0:
        parser.error("--max-tool-calls must be positive.")

    if (
        args.max_cost_inr is not None
        and (
            not math.isfinite(args.max_cost_inr)
            or args.max_cost_inr <= 0
        )
    ):
        parser.error(
            "--max-cost-inr must be a finite positive number."
        )

    return args


def resolve_cli_path(path, project_root):
    """
    Resolve relative paths from the project root.
    Absolute paths are preserved.
    """

    path = Path(path)

    if not path.is_absolute():
        path = project_root / path

    return path.resolve()


def validate_execution_options(args, project_root):
    """Validate file paths and supported execution options."""

    parser = build_parser()

    input_dir = resolve_cli_path(
        args.files,
        project_root,
    )

    output_dir = resolve_cli_path(
        args.out,
        project_root,
    )

    if not input_dir.is_dir():
        parser.error(
            f"Input directory does not exist: {input_dir}"
        )

    if input_dir == output_dir:
        parser.error(
            "Input and output directories must be different."
        )

    if (
        input_dir.is_relative_to(output_dir)
        or output_dir.is_relative_to(input_dir)
    ):
        parser.error(
            "Input and output directories must not "
            "be nested inside each other."
        )

    if args.max_cost_inr is not None:
        parser.error(
            "--max-cost-inr cannot be enforced "
            "until verified pricing is configured."
        )

    return input_dir, output_dir


async def run_cli(args):
    """Execute OmniAgent with CLI-provided settings."""

    from .agent import OmniAgent

    project_root = Path(
        __file__
    ).resolve().parents[2]

    input_dir, output_dir = validate_execution_options(
        args,
        project_root,
    )

    agent = OmniAgent(
        input_dir=input_dir,
        output_dir=output_dir,
    )

    print("\nOmniAgent AI — CLI Mode")
    print("-----------------------")
    print("Task:", args.task)
    print("Input directory:", input_dir)
    print("Output directory:", output_dir)
    print("Maximum steps:", args.max_steps)
    print("Maximum tool calls:", args.max_tool_calls)
    print()

    result = await agent.run(
        task=args.task,
        max_steps=args.max_steps,
        max_tool_calls=args.max_tool_calls,
        max_attempts=3,
    )

    print("\nCLI Execution Summary")
    print("---------------------")
    print("Status:", result["status"])
    print(
        "Tools used:",
        [
            item["tool"]
            for item in result["history"]
        ],
    )

    if result.get("report_path"):
        print(
            "Report:",
            result["report_path"],
        )

    if result.get("result_path"):
        print(
            "Markdown:",
            result["result_path"],
        )

    print(
        "Trace:",
        result["trace_path"],
    )

    if result["status"] != "success":
        raise RuntimeError(
            "Agent did not finish successfully."
        )

    print("\nPASS: CLI workflow completed!")

    return result


def main():
    args = parse_args()
    asyncio.run(run_cli(args))


if __name__ == "__main__":
    main()
