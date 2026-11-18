"""Check benchmark records and Hugging Face JSONL export integrity."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parent
FILES = ("design_tasks.jsonl", "tool_catalogs.jsonl", "media_manifest.jsonl", "verifiers.jsonl")


def load_jsonl(name: str) -> list[dict]:
    rows = [json.loads(line) for line in (ROOT / name).read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise ValueError(f"{name} has no records")
    return rows


def main() -> int:
    datasets = {name: load_jsonl(name) for name in FILES}
    tasks = datasets["design_tasks.jsonl"]
    catalogs = datasets["tool_catalogs.jsonl"]
    verifiers = {row["verifier_id"] for row in datasets["verifiers.jsonl"]}
    for task in tasks:
        if task["verifier_id"] not in verifiers:
            raise ValueError(f"{task['case_id']} references unknown verifier {task['verifier_id']}")
    print(f"Loaded {len(tasks)} authored tasks, {len(catalogs)} tool catalogs, {len(datasets['media_manifest.jsonl'])} media records, and {len(verifiers)} verifiers.")
    print(f"Controlled comparisons: {len(tasks) * len(catalogs)} task/catalog pairs.")
    print("No model/API calls made. This checks dataset structure only.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
