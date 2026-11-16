"""Run the task agent.

    python -m bodhan_agent --task "Make a two-minute Tamil audio summary of this chapter" \\
        --files path/to/inputs/ --out ./out [--max-steps 30] [--max-cost-inr 20]

Writes out/result.md, out/artifacts/, out/trace.jsonl and out/report.json. The exit code is 0
whenever the run ended cleanly (including honest failures such as unsupported languages or an
exhausted budget; see report.json "status"), 2 for bad arguments, 1 for an internal crash.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bodhan_agent", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--task", required=True, help="The task in plain language.")
    parser.add_argument("--files", nargs="*", default=[], help="Input files and/or folders.")
    parser.add_argument("--out", required=True, help="Output folder for this run.")
    parser.add_argument("--max-steps", type=int, default=30, help="Maximum tool calls (default 30).")
    parser.add_argument("--max-cost-inr", type=float, default=20.0, help="Maximum spend in rupees (default 20).")
    parser.add_argument("--max-runtime-s", type=float, default=900.0, help="Wall-clock limit for the run (default 900).")
    parser.add_argument("--call-timeout-s", type=float, default=300.0, help="Timeout for each tool call (default 300).")
    parser.add_argument("--mcp", choices=["stdio", "inproc"], default="stdio",
                        help="stdio: launch python -m bodhan_mcp and talk MCP over stdio (default). inproc: same server in-process.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    if args.max_steps < 1 or args.max_cost_inr < 0:
        parser.error("--max-steps must be >= 1 and --max-cost-inr >= 0")

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, stream=sys.stderr)
    from bodhan_mcp.config import load_dotenv_if_present

    load_dotenv_if_present()

    from .agent import Agent
    from .budget import Budget

    server = None
    if args.mcp == "inproc":
        from bodhan_mcp.config import Settings
        from bodhan_mcp.server import create_server

        out = Path(args.out).resolve()
        settings = Settings.from_env()
        settings.output_dir = out / "artifacts" / "intermediate"
        server = create_server(settings)

    agent = Agent(args.task, args.files, Path(args.out),
                  Budget(max_steps=args.max_steps, max_cost_inr=args.max_cost_inr, max_runtime_s=args.max_runtime_s),
                  mcp_server=server, call_timeout_s=args.call_timeout_s)
    try:
        rep = asyncio.run(agent.run())
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 1
    print(json.dumps({"status": rep["status"], "stop_reason": rep["stop_reason"], "cost_inr": rep["cost"]["total_inr"],
                      "steps": rep["steps"]["used"], "duration_s": rep["duration_s"],
                      "artifacts": [a["path"] for a in rep["artifacts"]], "result": str(Path(args.out) / "result.md")},
                     indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
