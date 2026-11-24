from __future__ import annotations

import argparse
import math
import sys
import webbrowser
from pathlib import Path

from bodhan_mcp.config import Settings
from bodhan_mcp.errors import ToolError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a Bodhan task with `python -m bodhan_agent run`, or start the "
            "independent dashboard with `python -m bodhan_agent dashboard`."
        )
    )
    parser.add_argument("--task", required=True, help="Natural-language task to perform.")
    parser.add_argument("--files", required=True, type=Path, help="Input file or directory.")
    parser.add_argument("--out", required=True, type=Path, help="Run output directory.")
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument("--max-cost-inr", type=float, default=20.0)
    parser.add_argument(
        "--runs-dir",
        type=Path,
        help="Persistent dashboard history directory (defaults to out/.bodhan-dashboard).",
    )
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Open the local dashboard for this CLI run and keep it available for inspection.",
    )
    parser.add_argument(
        "--dashboard-port",
        type=int,
        default=8765,
        help="Local dashboard port (default: 8765; use 0 for an available port).",
    )
    return parser


def _default_runs_dir() -> Path:
    return Path("out/.bodhan-dashboard").resolve()


def _result_code(status: str) -> int:
    return {
        "completed": 0,
        "partial": 3,
        "impossible": 4,
        "needs_user_input": 4,
    }.get(status, 1)


def _finish_cli_run(manager: object, run: object) -> int:
    from bodhan_agent.web import RunManager, WebRun

    if not isinstance(manager, RunManager) or not isinstance(run, WebRun):
        raise TypeError("The CLI run manager returned an invalid run.")
    if run.thread is not None:
        try:
            run.thread.join()
        except KeyboardInterrupt:
            manager.request_stop(run)
            run.thread.join()
            print("\nTask stop requested; any in-flight call was allowed to finish.", file=sys.stderr)
            return 130
    if run.error:
        print(f"Run status: {run.status}; {run.error}", file=sys.stderr)
    else:
        print(f"Run status: {run.status}; output: {run.output_dir}")
    return _result_code(run.status)


def _dashboard_run(args: argparse.Namespace, input_path: Path, output_path: Path) -> int:
    from bodhan_agent.web import RunConflict, RunManager, create_server

    if not 0 <= args.dashboard_port <= 65535:
        raise ValueError("--dashboard-port must be between 0 and 65535.")

    manager = RunManager(args.runs_dir or _default_runs_dir())
    server = create_server(manager, port=args.dashboard_port)
    try:
        run = manager.start_path_run(
            args.task,
            input_path,
            output_path,
            args.max_steps,
            args.max_cost_inr,
        )
    except (OSError, RunConflict, ValueError):
        server.server_close()
        raise

    address, port = server.server_address[:2]
    dashboard_url = f"http://{address}:{port}/?run={run.run_id}"
    print(f"Dashboard: {dashboard_url}")
    print(f"Run output: {run.output_dir}")
    try:
        if not webbrowser.open(dashboard_url):
            print("The browser did not open automatically; use the dashboard URL above.", file=sys.stderr)
    except webbrowser.Error:
        print("The browser could not be opened; use the dashboard URL above.", file=sys.stderr)

    interrupted = False
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        interrupted = True
        print("\nClosing the dashboard and requesting a cooperative task stop.")
    finally:
        if interrupted:
            manager.request_stop(run)
        if run.thread is not None:
            run.thread.join()
        server.server_close()

    if run.error:
        print(f"Run status: {run.status}; {run.error}", file=sys.stderr)
    else:
        print(f"Run status: {run.status}; output: {run.output_dir}")
    return 130 if interrupted else _result_code(run.status)


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "dashboard":
        from bodhan_agent.web import main as dashboard_main

        return dashboard_main(arguments[1:])
    if arguments and arguments[0] == "run":
        arguments = arguments[1:]

    parser = build_parser()
    args = parser.parse_args(arguments)
    if not args.task.strip():
        parser.error("--task must not be empty.")
    if args.max_steps <= 0:
        parser.error("--max-steps must be positive.")
    if not math.isfinite(args.max_cost_inr) or args.max_cost_inr <= 0:
        parser.error("--max-cost-inr must be a finite positive number.")
    if args.dashboard and not 0 <= args.dashboard_port <= 65535:
        parser.error("--dashboard-port must be between 0 and 65535.")
    try:
        input_path = args.files.expanduser().resolve(strict=True)
    except OSError:
        print(f"Input path does not exist: {args.files}", file=sys.stderr)
        return 2
    if not input_path.is_file() and not input_path.is_dir():
        print("--files must reference a file or directory.", file=sys.stderr)
        return 2
    output_path = args.out.expanduser().resolve()
    if output_path.exists() and not output_path.is_dir():
        print("--out must be a directory path.", file=sys.stderr)
        return 2
    if input_path.is_dir() and (output_path == input_path or input_path in output_path.parents):
        print("--out must not be inside the input directory.", file=sys.stderr)
        return 2
    runs_dir = (args.runs_dir or _default_runs_dir()).expanduser().resolve()
    if input_path.is_dir() and (runs_dir == input_path or input_path in runs_dir.parents):
        print("--runs-dir must not be inside the input directory.", file=sys.stderr)
        return 2
    if runs_dir == output_path or runs_dir in output_path.parents:
        print("--out must not be inside the dashboard history directory.", file=sys.stderr)
        return 2
    args.runs_dir = runs_dir
    settings = Settings.from_env(args.out)
    if not settings.llm_api_key:
        print("Configure LLM_API_KEY in the environment or local .env file.", file=sys.stderr)
        return 2
    if args.dashboard:
        try:
            return _dashboard_run(args, input_path, output_path)
        except (OSError, ValueError) as exc:
            print(f"Could not start the local dashboard: {exc}", file=sys.stderr)
            return 2

    try:
        from bodhan_agent.web import RunConflict, RunManager

        manager = RunManager(args.runs_dir or _default_runs_dir(output_path))
        run = manager.start_path_run(
            args.task,
            input_path,
            output_path,
            args.max_steps,
            args.max_cost_inr,
        )
    except ToolError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 1
    except (OSError, ValueError, RunConflict) as exc:
        print(f"Could not start task run: {exc}", file=sys.stderr)
        return 2
    return _finish_cli_run(manager, run)
