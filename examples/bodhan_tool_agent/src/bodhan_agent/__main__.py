import argparse
import asyncio
import pathlib
from dotenv import load_dotenv

from .loop import run_task


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Bodhan Task Agent")
    parser.add_argument("--task", required=True, help="Plain-language task description")
    parser.add_argument("--files", required=True, help="Path to input files directory or file")
    parser.add_argument("--out", default="./out", help="Output directory (default: ./out)")
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument("--max-cost-inr", type=float, default=20.0)
    parser.add_argument(
        "--use-mcp",
        action="store_true",
        help="Use MCP server over stdio (default: in-process)",
    )
    args = parser.parse_args()
    asyncio.run(
        run_task(
            task=args.task,
            files=pathlib.Path(args.files),
            out_dir=pathlib.Path(args.out),
            max_steps=args.max_steps,
            max_cost_inr=args.max_cost_inr,
        )
    )


if __name__ == "__main__":
    main()
