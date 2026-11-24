"""Capture an actual CLI execution and its trace events, without recording secrets."""

import argparse
import datetime
import json
import subprocess
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--files", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-cost-inr", required=True)
    parser.add_argument("--max-seconds", default="300")
    parser.add_argument("--max-steps", default="35")
    parser.add_argument("--input-note", required=True)
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    capture = args.out.with_name(args.out.name + "_capture.jsonl")
    command = [
        sys.executable,
        "-m",
        "bodhan_agent",
        "--task",
        args.task,
        "--files",
        args.files,
        "--out",
        str(args.out),
        "--max-cost-inr",
        args.max_cost_inr,
        "--max-seconds",
        args.max_seconds,
        "--max-steps",
        args.max_steps,
        "--call-timeout",
        "35",
        "--input-note",
        args.input_note,
    ]
    started = time.monotonic()
    with capture.open("x", encoding="utf-8") as stream:

        def emit(kind, **fields):
            stream.write(
                json.dumps(
                    {"kind": kind, "seconds": round(time.monotonic() - started, 3), **fields},
                    ensure_ascii=False,
                )
                + "\n"
            )
            stream.flush()

        emit(
            "command", command=command, utc=datetime.datetime.now(datetime.timezone.utc).isoformat()
        )
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        position = 0
        trace = args.out / "trace.jsonl"

        def drain_trace():
            nonlocal position
            if trace.exists():
                # An appending writer may be between bytes of its final line.
                lines = trace.read_text().split("\n")[:-1]
                for line in lines[position:]:
                    emit("trace", event=json.loads(line))
                position = len(lines)

        while True:
            drain_trace()
            if process.poll() is not None:
                break
            time.sleep(0.2)
        stdout, stderr = process.communicate()
        drain_trace()
        emit("process_exit", exit_code=process.returncode, stdout=stdout, stderr=stderr)
    print(stdout, end="")
    if stderr:
        print(stderr, file=sys.stderr, end="")
    if args.out.exists():
        (args.out / "execution_command.json").write_text(
            json.dumps(
                {
                    "command": command,
                    "capture": str(capture),
                    "exit_code": process.returncode,
                    "kind": "actual_subprocess_capture_not_desktop_recording",
                },
                indent=2,
            )
        )
    return process.returncode


if __name__ == "__main__":
    raise SystemExit(main())
