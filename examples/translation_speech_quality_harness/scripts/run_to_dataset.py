"""Turn a run's items.jsonl (with --generate outputs) back into a plain dataset JSONL.

Keeps id, lang, source, reference, translation, audio and any original labels, so the
generated outputs can be fed to the injector or re-evaluated without regenerating.

    python scripts/run_to_dataset.py outputs/run/items.jsonl sample_data/generated.jsonl
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

KEEP = ("id", "lang", "source", "reference", "translation", "audio", "labels")


def main(src: str, dst: str) -> int:
    rows = []
    for line in Path(src).read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        row = {k: r[k] for k in KEEP if r.get(k) is not None}
        rows.append(row)
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    Path(dst).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")
    print(f"{len(rows)} items -> {dst}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
