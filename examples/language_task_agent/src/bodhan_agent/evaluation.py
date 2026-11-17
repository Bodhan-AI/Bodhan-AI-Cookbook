"""Run a declared task suite through the real CLI and check independent outcomes.

This is development evaluation, never a hidden-task score. No mock transports are
installed. Each case records its command, exit code, report, trace and assertions.
"""

import argparse
from collections import Counter
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
import sys


def assess(output, expected, max_cost):
    errors, selected, events = [], [], []
    report_path = output / "report.json"
    if not report_path.is_file():
        return {"passed": False, "errors": ["Missing report.json"]}
    report = json.loads(report_path.read_text())
    if report.get("status") not in expected["statuses"]:
        errors.append(f"Unexpected status: {report.get('status')}")
    cost = Decimal(report["total_cost_inr"])
    if not cost.is_finite() or not Decimal(0) <= cost <= Decimal(max_cost):
        errors.append("Cost exceeds declared case limit or is invalid")
    for artifact in report.get("artifacts", []):
        if not artifact.get("selected"):
            continue
        path = (output / artifact["path"]).resolve()
        if not path.is_relative_to(output.resolve()) or not path.is_file():
            errors.append("Selected artifact is missing or escapes output")
            continue
        selected.append(path)
        provenance = artifact.get("provenance", "")
        if provenance in {"bodhan_translation", "bodhan_ai_generated"} and not any(
            check.get("ref") == artifact.get("ref")
            and check.get("checked") is True
            and check.get("passed") is True
            for check in report.get("verification", [])
        ):
            errors.append("Selected generated translation/audio lacks its matching passed check")
    if report.get("status") == "completed":
        if not selected:
            errors.append("Completed without selected artifacts")
        if any(
            check.get("checked") is not True or check.get("passed") is not True
            for check in report.get("verification", [])
        ):
            errors.append("Completed with failed or unchecked verification")
    text = "\n".join(
        path.read_text()
        for path in selected
        if path.suffix.lower() in {".md", ".txt", ".csv", ".srt", ".html"}
    ).casefold()
    for value in expected.get("required_text", []):
        if value.casefold() not in text:
            errors.append(f"Missing required text: {value}")
    for value in expected.get("forbidden_text", []):
        if value.casefold() in text:
            errors.append(f"Forbidden text appeared: {value}")
    explanation = report.get("explanation", "").casefold()
    if expected.get("explanation_any") and not any(
        value.casefold() in explanation for value in expected["explanation_any"]
    ):
        errors.append("Failure explanation does not identify expected limitation")
    trace = output / "trace.jsonl"
    if trace.is_file():
        events = [json.loads(line) for line in trace.read_text().splitlines()]
    tools = Counter(event["tool"] for event in events if event["kind"] == "tool")
    for name, count in expected.get("min_tools", {}).items():
        if tools[name] < count:
            errors.append(f"Expected at least {count} {name} calls")
    apis = Counter(event["model"] for event in events if event["kind"] == "api")
    cache = Counter(event["model"] for event in events if event["kind"] == "cache_hit")
    if expected.get("no_bodhan_dispatch") and any(not name.startswith("planner:") for name in apis):
        errors.append("A Bodhan request was dispatched during a cache-only evaluation")
    if sum(cache.values()) < expected.get("min_cache_hits", 0):
        errors.append("Expected cached real response was not used")
    if expected.get("no_api") and apis:
        errors.append("Expected failure before any API request")
    return {
        "passed": not errors,
        "errors": errors,
        "status": report["status"],
        "cost_inr": str(cost),
        "seconds": report["total_seconds"],
        "tools": dict(tools),
        "api_responses": dict(apis),
        "cache_hits": dict(cache),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--runs-root", type=Path, required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--cache-only", action="store_true")
    args = parser.parse_args()
    suite = json.loads(args.suite.read_text())
    if args.out.exists():
        raise SystemExit("Choose a new evaluation summary path; existing evidence is immutable.")
    if not args.prefix.replace("_", "").replace("-", "").isalnum():
        raise SystemExit("Use an alphanumeric output prefix.")
    ids = [case["id"] for case in suite["cases"]]
    if len(ids) != len(set(ids)) or any(
        not name.replace("_", "").replace("-", "").isalnum() for name in ids
    ):
        raise SystemExit("Case IDs must be unique alphanumeric names.")
    args.runs_root.mkdir(parents=True, exist_ok=True)
    outputs = [args.runs_root / f"{args.prefix}_{name}" for name in ids]
    if any(path.exists() for path in outputs):
        raise SystemExit("A case output already exists; choose a new prefix.")
    result = {
        "scope": suite["scope"],
        "suite": str(args.suite.resolve()),
        "cache_only": args.cache_only,
        "cases": [],
        "warning": "Development compositions, not hidden-test or fluent-quality evidence.",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    for case, output in zip(suite["cases"], outputs):
        files = [str((args.suite.parent / name).resolve()) for name in case["files"]]
        cap = str(case.get("max_cost_inr", "0"))
        seconds = int(case.get("max_seconds", 150))
        command = [
            sys.executable,
            "-m",
            "bodhan_agent",
            "--task",
            case["task"],
            "--files",
            *files,
            "--out",
            str(output),
            "--max-cost-inr",
            cap,
            "--max-steps",
            str(case.get("max_steps", 24)),
            "--max-seconds",
            str(seconds),
            "--call-timeout",
            "35",
        ]
        environment = os.environ.copy()
        if args.cache_only:
            environment["BODHAN_CACHE_ONLY"] = "1"
        try:
            process = subprocess.run(
                command, capture_output=True, text=True, env=environment, timeout=seconds + 10
            )
            item = assess(output, case["expected"], cap)
            item.update(exit_code=process.returncode, stdout=process.stdout, stderr=process.stderr)
            correct_exit = 0 if item.get("status") == "completed" else 2
            if process.returncode != correct_exit:
                item["errors"].append("CLI exit code disagrees with report status")
                item["passed"] = False
        except subprocess.TimeoutExpired:
            item = {"passed": False, "errors": ["Process exceeded its outer time bound"]}
        except (OSError, ValueError, KeyError, TypeError):
            item = {"passed": False, "errors": ["Invalid or unreadable report/trace evidence"]}
        item.update(id=case["id"], command=command, output=str(output))
        result["cases"].append(item)
        result["passed"] = sum(item["passed"] for item in result["cases"])
        result["total"] = len(suite["cases"])
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(
            json.dumps({k: item.get(k) for k in ["id", "passed", "status", "errors"]}), flush=True
        )
    return 0 if result["passed"] == result["total"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
