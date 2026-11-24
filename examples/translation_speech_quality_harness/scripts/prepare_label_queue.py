import argparse
import json
import sys
from pathlib import Path

RECIPE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RECIPE_DIR / "src"))

from bodhan_eval.label_workflow import (  # noqa: E402
    build_label_queue,
    write_label_queue,
)


def read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=20261121)
    args = parser.parse_args()
    if not args.dataset.is_file():
        parser.error("dataset does not exist")
    rows = read_jsonl(args.dataset)
    queue = build_label_queue(rows, seed=args.seed, total=80)
    write_label_queue(args.out, queue)
    print(
        "Prepared label queue: "
        f"{len(queue)} rows "
        f"(hi={sum(row['lang']=='hi' for row in queue)}, "
        f"ta={sum(row['lang']=='ta' for row in queue)}, "
        f"bn={sum(row['lang']=='bn' for row in queue)})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
