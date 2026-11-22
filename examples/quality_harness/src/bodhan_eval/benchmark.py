"""Run an evaluation twice and verify zero-network, byte-identical cache replay."""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from .artifacts import file_hash


def validate_assessment(summary, rows, require_live):
    issues = []
    if not rows or summary.get("items") != len(rows):
        issues.append("empty population or summary/item count mismatch")
    if any(summary.get(name, 0) for name in ("invalid_items", "filtered_items", "failed_checks")):
        issues.append("invalid/filtered records or failed checks")
    if any(row.get("status") != "evaluated" for row in rows):
        issues.append("unassessed items remain")
    judged = 0
    if require_live:
        for row in rows:
            required = ["back_translation", "judge"] if (row.get("translation") or "").strip() else []
            if row.get("audio"):
                required += ["bodhan_asr", "audio_signal"]
            for check in required:
                value = row.get("checks", {}).get(check)
                state = value.get("status", "completed") if isinstance(value, dict) else value
                if state != "completed":
                    issues.append(f"{row.get('id')}/{row.get('lang')}: incomplete {check}")
            if "judge" in required and isinstance(row.get("checks", {}).get("judge"), dict):
                judged += row["checks"]["judge"].get("status") == "completed"
        if not judged:
            issues.append("no completed live judge assessment")
    return {"complete": not issues, "issues": issues, "population": len(rows), "scope": "model_backed" if require_live else "offline_engineering_only"}


def hashes(directory):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(directory.iterdir()) if p.is_file()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--langs", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--cache-dir", default=".cache/benchmark")
    parser.add_argument("options", nargs=argparse.REMAINDER, help="harness flags after --")
    args = parser.parse_args(argv)
    options = args.options[1:] if args.options[:1] == ["--"] else args.options
    if any(value.split("=", 1)[0] in ("--out", "--dataset", "--langs", "--cache-dir", "--baseline") for value in options):
        parser.error("dataset, langs, out, cache-dir and baseline cannot be overridden in extra options")
    output, cache = Path(args.out), Path(args.cache_dir)
    for child in ("first", "replay"):
        if (output / child).exists():
            parser.error("benchmark output must be fresh; choose another --out directory")
    executions, assessments = [], []
    initial_cache_entries = sum(len(p.stem) == 64 and all(c in "0123456789abcdef" for c in p.stem) for p in cache.glob("*.json"))
    dataset_hash = file_hash(args.dataset)
    for child in ("first", "replay"):
        command = [
            sys.executable,
            "-m",
            "bodhan_eval",
            "--dataset",
            args.dataset,
            "--langs",
            args.langs,
            "--out",
            str(output / child),
            "--cache-dir",
            str(cache),
        ] + options
        if child == "replay":
            command += ["--max-cost-inr", "0"]
        subprocess.run(command, check=True)
        executions.append(json.loads((cache / "last_execution.json").read_text(encoding="utf-8")))
        summary = json.loads((output / child / "summary.json").read_text(encoding="utf-8"))
        rows = [json.loads(line) for line in (output / child / "items.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        assessments.append(validate_assessment(summary, rows, require_live="--offline" not in options))
    first, replay = hashes(output / "first"), hashes(output / "replay")
    identical = first == replay
    zero_replay = executions[1]["network_attempts"] == 0 and executions[1]["reserved_inr_including_failed_attempts"] == 0
    unchanged = dataset_hash == file_hash(args.dataset)
    verified = identical and zero_replay and unchanged and all(assessment["complete"] for assessment in assessments)
    result = {
        "version": 2,
        "dataset_sha256": dataset_hash,
        "dataset_unchanged": unchanged,
        "initial_cache_entries": initial_cache_entries,
        "cold_cache": initial_cache_entries == 0,
        "identical_artifacts": identical,
        "replay_zero_network_and_budget": zero_replay,
        "assessment_complete": all(assessment["complete"] for assessment in assessments),
        "assessments": assessments,
        "verified": verified,
        "executions": executions,
        "artifact_sha256": first,
        "note": "Offline benchmarks measure local orchestration only. A warm cache is not a cold hosted benchmark.",
    }
    (output / "benchmark.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not verified:
        raise SystemExit("Replay verification failed; inspect benchmark.json and per-check failures")
    print("Verified assessed population, byte-identical artifacts and zero-network replay.")


if __name__ == "__main__":
    main()
