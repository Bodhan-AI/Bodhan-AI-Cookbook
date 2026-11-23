"""How well a run caught the injected errors: per category, language and translation origin.

    python scripts/injection_report.py data/injected.jsonl report-injected [check]

With a check name (e.g. judge), only that check's candidates count.

Each variant is compared with its own base (the item it was made from): only candidates the base did
not have count. `caught` = any new candidate; `right kind` = a new candidate of the injected category;
`flagged` = fusion (§13, default thresholds) flags the item on the new candidates alone.
For hard negatives (harmless changes) every new candidate is a false alarm.

Item level; span-matched recall and precision are in calibration.json (python -m bodhan_eval.calibrate).
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from bodhan_eval.calibrate import keys  # noqa: E402
from bodhan_eval.fuse import fuse, load_thresholds  # noqa: E402


def main(dataset, report, only=None):
    rows = [json.loads(line) for line in Path(dataset).read_text(encoding="utf-8").splitlines()]
    recs = {(r["id"], r["lang"]): r for r in map(json.loads, (Path(report) / "items.jsonl").open(encoding="utf-8"))}
    table = defaultdict(lambda: defaultdict(int))
    th = load_thresholds()
    for v in rows:
        if "base" not in v:
            continue
        rec, base = recs[(v["id"], v["lang"])], recs[(v["base"], v["lang"])]
        old = set().union(*(keys(c) for c in base["candidates"]))
        new = [c for c in rec["candidates"] if not keys(c) & old and (only is None or c["check"] == only)]
        cat = v["injection"]["category"]
        for group in ((cat, "all"), (cat, v["lang"]), (cat, v.get("translation_origin") or "?")):
            t = table[group]
            t["n"] += 1
            t["caught"] += bool(new)
            t["right kind"] += any(c["category"] == cat for c in new)
            t["flagged"] += fuse(rec, th, new)[1] == "flagged"
            t["unscored"] += "translation" in rec["checks"] or rec["checks"].get("back_translation", "ok") != "ok"

    cols = ("n", "caught", "right kind", "flagged", "unscored")
    print("| category | group | " + " | ".join(cols) + " |\n|---|---|" + "---|" * len(cols))
    for (cat, group), t in sorted(table.items(), key=lambda kv: (kv[0][0] == "clean", kv[0][0], kv[0][1] != "all", kv[0][1])):
        cells = [str(t["n"])] + [f"{t[c]} ({t[c] / t['n']:.0%})" for c in cols[1:4]] + [str(t["unscored"])]
        print(f"| {cat} | {group} | " + " | ".join(cells) + " |")


if __name__ == "__main__":
    main(*sys.argv[1:4])
