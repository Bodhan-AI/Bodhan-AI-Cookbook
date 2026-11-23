"""Build a labelled calibration set by injecting errors into a dataset.

    python -m bodhan_eval.build_calibration \\
        --dataset sample_data/demo_100.jsonl \\
        --dataset sample_data/formatting_demo.generated.jsonl \\
        --out sample_data/calibration.jsonl --per-category 9 --clean 12

Only records that already have a translation are used. ``--keep-existing``
keeps the cases already in ``--out`` (matched by id) at the top of the file.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bodhan_eval.calibration import build_calibration_cases
from bodhan_eval.dataset import load_dataset
from bodhan_eval.injector import SUPPORTED_CATEGORIES


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", action="append", required=True,
                        help="Dataset JSONL with translations (repeatable)")
    parser.add_argument("--out", required=True)
    parser.add_argument("--langs", default=None, help="Comma-separated language filter")
    parser.add_argument("--categories", default=",".join(SUPPORTED_CATEGORIES))
    parser.add_argument("--per-category", type=int, default=9)
    parser.add_argument("--clean", type=int, default=12, help="Number of clean (no error) cases")
    parser.add_argument("--seed", type=int, default=13)
    parser.add_argument("--keep-existing", action="store_true")
    args = parser.parse_args(argv)

    languages = [lang.strip() for lang in args.langs.split(",")] if args.langs else None
    records = [r for path in args.dataset for r in load_dataset(path, languages)]

    cases = build_calibration_cases(
        records,
        categories=[c.strip() for c in args.categories.split(",") if c.strip()],
        per_category=args.per_category,
        clean_cases=args.clean,
        seed=args.seed,
    )

    out = Path(args.out)
    existing = []
    if args.keep_existing and out.exists():
        existing = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
    existing_ids = {case["id"] for case in existing}
    cases = existing + [case for case in cases if case["id"] not in existing_ids]

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases),
        encoding="utf-8",
    )

    counts = {}
    for case in cases:
        label = case["injected_category"] or "clean"
        counts[label] = counts.get(label, 0) + 1
    print(f"Wrote {len(cases)} cases to {out}: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
