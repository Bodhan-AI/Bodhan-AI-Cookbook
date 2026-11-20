import traceback
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from bodhan_agent.planner import create_plan
from bodhan_agent.executor import execute_plan, aggregate_usage, ExecutionError
from bodhan_agent.mcp_client import connect_to_mcp_server


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Bodhan Agentic AI Task Runner"
    )
    parser.add_argument("--task", required=True, help="Describe the task.")
    parser.add_argument(
        "--files",
        nargs="+",
        required=True,
        help="Input file or directory paths."
    )
    parser.add_argument("--out", default="./out", help="Output directory.")
    parser.add_argument(
        "--max-steps",
        type=int,
        default=30,
        help="Maximum allowed steps."
    )
    parser.add_argument(
        "--max-cost-inr",
        type=float,
        default=20.0,
        help="Maximum budget in INR."
    )
    return parser.parse_args()


def collect_files(inputs):
    collected_files = []

    for input_path in inputs:
        path = Path(input_path).resolve()

        if not path.exists():
            raise FileNotFoundError(
                f"Input path does not exist: {path}"
            )

        if path.is_file():
            collected_files.append(str(path))

        elif path.is_dir():
            for file_path in sorted(path.rglob("*")):
                if file_path.is_file():
                    collected_files.append(str(file_path.resolve()))

    return collected_files


def write_result_markdown(task, plan, results, output_path):
    lines = [
        "# Bodhan Agent Task Result",
        "",
        f"**Task:** {task}",
        "",
        "## Execution Plan",
        "",
    ]

    for index, step in enumerate(plan.get("steps", []), 1):
        lines.append(
            f"{index}. {step['description']} (`{step['tool']}`)"
        )

    lines.extend(["", "## Results", ""])

    for index, item in enumerate(results, 1):
        lines.extend([
            f"### Step {index}: {item.get('step', 'Unknown step')}",
            "",
            f"**Tool:** `{item.get('tool', 'unknown')}`",
            f"**Status:** {item.get('status', 'unknown')}",
            f"**Latency:** {item.get('latency_seconds', 'N/A')} seconds",
            f"**Estimated Cost:** ₹{item.get('estimated_cost_inr', 0):.4f}",
            "",
            "#### Output",
            "",
        ])

        result_data = item.get("result") or {}
        result_text = result_data.get("text", "")

        lines.append(
            str(result_text)
            if result_text
            else "No text output returned by this tool."
        )

        if item.get("error"):
            lines.extend([
                "",
                "**Error:**",
                "",
                str(item["error"]),
            ])

        verification = item.get("verification")

        if verification is not None:
            lines.extend([
                "",
                "#### Verification",
                "",
                "```json",
                json.dumps(
                    verification,
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                ),
                "```",
            ])

        lines.append("")

    output_path.write_text(
        "\n".join(lines),
        encoding="utf-8"
    )


def write_trace(plan, results, trace_path):
    with trace_path.open("w", encoding="utf-8") as file:
        plan_record = {
            "event": "plan",
            "timestamp": time.time(),
            "plan": plan,
        }

        file.write(
            json.dumps(
                plan_record,
                ensure_ascii=False,
                default=str
            ) + "\n"
        )

        for index, item in enumerate(results, 1):
            trace_record = {
                "event": "tool_execution",
                "timestamp": time.time(),
                "step_number": index,
                "step": item.get("step"),
                "tool": item.get("tool"),
                "arguments": item.get("arguments"),
                "result": item.get("result"),
                "verification": item.get("verification"),
                "latency_seconds": item.get("latency_seconds"),
                "estimated_cost_inr": item.get(
                    "estimated_cost_inr", 0
                ),
                "status": item.get("status"),
                "error": item.get("error"),
            }

            file.write(
                json.dumps(
                    trace_record,
                    ensure_ascii=False,
                    default=str
                ) + "\n"
            )


def write_report(
    task,
    plan,
    results,
    report_path,
    start_time,
    status="success",
    error=None,
    max_cost_inr=20.0,
    budget_summary=None,
):
    usage_summary = aggregate_usage(results)

    estimated_spent = usage_summary.get(
        "estimated_cost_inr", 0.0
    )

    estimated_remaining = max(
        0.0, max_cost_inr - estimated_spent
    )

    completed_steps = sum(
        1 for item in results
        if item.get("status") == "success"
    )

    failed_steps = sum(
        1 for item in results
        if item.get("status") == "failed"
    )

    if budget_summary is None:
        budget_summary = {
            "max_cost_inr": max_cost_inr,
            "estimated_spent_inr": estimated_spent,
            "estimated_remaining_inr": estimated_remaining,
            "cost_type": "estimated",
        }

    report = {
        "task": task,
        "status": status,
        "planned_steps": len(plan.get("steps", [])),
        "completed_steps": completed_steps,
        "failed_steps": failed_steps,
        "elapsed_seconds": round(
            time.time() - start_time, 2
        ),
        "max_cost_inr": max_cost_inr,
        "usage": usage_summary,
        "budget": budget_summary,
        "cost_enforcement": "estimated",
        "error": error,
    }

    report_path.write_text(
        json.dumps(
            report,
            indent=2,
            ensure_ascii=False,
            default=str
        ),
        encoding="utf-8",
    )


async def run_agent(args):
    start_time = time.time()

    output_dir = Path(args.out).resolve()
    artifacts_dir = output_dir / "artifacts"

    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result_path = output_dir / "result.md"
    trace_path = output_dir / "trace.jsonl"
    report_path = output_dir / "report.json"

    plan = {"steps": []}
    results = []
    files = []
    budget_summary = None

    status = "success"
    error_message = None

    try:
        if args.max_steps < 1:
            raise ValueError(
                "--max-steps must be greater than zero."
            )

        if args.max_cost_inr < 0:
            raise ValueError(
                "--max-cost-inr cannot be negative."
            )

        files = collect_files(args.files)

        print(f"Found {len(files)} input file(s).")

        for file_path in files:
            print(f" - {file_path}")

        print("\nCreating execution plan...")

        plan = create_plan(
            task=args.task,
            files=files,
        )

        steps = plan.get("steps", [])

        print("\nGenerated Plan:")

        for index, step in enumerate(steps, 1):
            print(
                f"{index}. {step['description']} "
                f"[{step['tool']}]"
            )

        if len(steps) > args.max_steps:
            raise RuntimeError(
                f"Plan contains {len(steps)} steps, "
                f"exceeding max-steps={args.max_steps}."
            )

        print("\nConnecting to MCP server...")

        async with connect_to_mcp_server() as session:
            results = await execute_plan(
                session=session,
                plan=plan,
                files=files,
                task=args.task,
                max_steps=args.max_steps,
                max_cost_inr=args.max_cost_inr,
                artifacts_dir=artifacts_dir,
            )

    except ExecutionError as exc:
        status = "failed"
        error_message = str(exc)
        results = exc.results
        budget_summary = getattr(
            exc, "budget_summary", None
        )

        print(
            "\n--- EXECUTION ERROR TRACEBACK ---",
            file=sys.stderr
        )

        traceback.print_exception(
            type(exc),
            exc,
            exc.__traceback__
        )

    except Exception as exc:
        status = "failed"
        error_message = str(exc)

        def find_execution_error(error):
            if isinstance(error, ExecutionError):
                return error
            nested = getattr(error, "exceptions", None)
            if nested:
                for child in nested:
                    found = find_execution_error(child)
                    if found is not None:
                        return found
            return None

        original_error = find_execution_error(exc)
        if original_error is not None:
            results = original_error.results
            budget_summary = getattr(original_error, "budget_summary", None)
            error_message = str(original_error)

        print(
            "\n--- FULL DEBUG TRACEBACK ---",
            file=sys.stderr
        )
        traceback.print_exc()

    finally:
        write_result_markdown(
            task=args.task,
            plan=plan,
            results=results,
            output_path=result_path,
        )

        write_trace(
            plan=plan,
            results=results,
            trace_path=trace_path,
        )

        write_report(
            task=args.task,
            plan=plan,
            results=results,
            report_path=report_path,
            start_time=start_time,
            status=status,
            error=error_message,
            max_cost_inr=args.max_cost_inr,
            budget_summary=budget_summary,
        )

    if status == "success":
        print("\nTask completed successfully!")
    else:
        print(
            f"\nTask failed: {error_message}",
            file=sys.stderr
        )

    print(f"Result: {result_path}")
    print(f"Trace:  {trace_path}")
    print(f"Report: {report_path}")
    print(f"Artifacts directory: {artifacts_dir}")

    return 0 if status == "success" else 1


def main():
    args = parse_arguments()
    exit_code = asyncio.run(run_agent(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()