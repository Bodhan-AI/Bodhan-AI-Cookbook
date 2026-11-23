import argparse
import sys
from pathlib import Path

RECIPE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RECIPE_DIR / "src"))

from bodhan_eval.label_workflow import ingest_label_csv  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument(
        "--out",
        type=Path,
        default=RECIPE_DIR / "data" / "labels_self.jsonl",
    )
    args = parser.parse_args()
    if not args.csv.is_file():
        parser.error("label CSV does not exist")
    try:
        summary = ingest_label_csv(args.csv, args.out)
    except ValueError as error:
        parser.error(str(error))
    print(
        "Ingested labels: "
        f"{summary['labelled_items']} items, "
        f"{summary['positive_items']} positive, "
        f"{summary['clean_items']} clean"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
