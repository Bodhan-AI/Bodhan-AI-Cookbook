import argparse
import sys

from .agent import run_task


def main() -> None:
    parser = argparse.ArgumentParser(prog="bodhan_agent")
    parser.add_argument("--task", required=True)
    parser.add_argument("--files", default=None, help="A file or a folder of input files.")
    parser.add_argument("--out", default="./out")
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument("--max-cost-inr", type=float, default=20.0)
    parser.add_argument("--call-timeout", type=int, default=300, help="Seconds allowed per tool call.")
    args = parser.parse_args()

    report = run_task(args.task, args.files, args.out, args.max_steps, args.max_cost_inr, args.call_timeout)
    print(f"Status: {report['status']} | steps: {report['steps']} | cost: Rs {report['total_cost_inr']}")
    print(f"Results: {args.out}/result.md")
    sys.exit(0 if report["status"] in ("completed", "impossible") else 2)


main()