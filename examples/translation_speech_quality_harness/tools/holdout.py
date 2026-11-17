"""Honest numbers on our own demo data: calibrate on half A, score half B.

python tools/holdout.py --dataset data/demo.jsonl --out report_holdout --env-file ../../../.env

Items are split by SHA-256 of their id, so every language of one source sentence lands in the same half. Half B rows
keep their text but lose their labels, the harness runs as usual (calibrating on half A only), and the flags on half B
are then scored against half B's labels. Model calls come from the cache of earlier runs.
"""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "src"))

from bodhan_eval.calibrate import canon, evaluate  # noqa: E402
from bodhan_eval.combine import SPAN_SIDE  # noqa: E402
from bodhan_eval.dataset import _labels  # noqa: E402


def half(item_id: str) -> str:
    return "A" if hashlib.sha256(item_id.encode()).digest()[0] % 2 == 0 else "B"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--langs", default="hi,ta,bn")
    p.add_argument("--env-file")
    p.add_argument("--max-cost-inr", default="0.5")
    p.add_argument("--skip", help="passed to the harness, for ablations")
    a = p.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    src = Path(a.dataset)
    rows = [json.loads(x) for x in src.read_text(encoding="utf-8").splitlines() if x.strip()]
    held = {}
    with open(out / "half_a_labels.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            r = dict(r)
            if half(r["id"]) == "B":
                held[(r["id"], r["lang"])] = [{**lab, "category": canon(lab["type"])} for lab in _labels(r.pop("labels", None), []) or []]
            if r.get("audio"):
                r["audio"] = str((src.parent / r["audio"]).resolve())  # the copy lives elsewhere; keep audio paths valid
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    cmd = [sys.executable, "-m", "bodhan_eval", "--dataset", str(out / "half_a_labels.jsonl"), "--langs", a.langs, "--out", str(out / "report"),
           "--max-cost-inr", a.max_cost_inr, "--concurrency", "1", "--cache-dir", str(HERE / ".bodhan_eval_cache")]
    if a.env_file:
        cmd += ["--env-file", a.env_file]
    if a.skip:
        cmd += ["--skip", a.skip]
    subprocess.run(cmd, check=True, cwd=HERE)

    results = [json.loads(x) for x in (out / "report" / "items.jsonl").read_text(encoding="utf-8").splitlines()]
    cal = json.loads((out / "report" / "calibration.json").read_text(encoding="utf-8"))
    res_b = [r for r in results if (r["id"], r["lang"]) in held]
    score = evaluate(res_b, held, cal.get("per_check", {}), {"half_b_inline": len(held)}, SPAN_SIDE)
    score["split"] = {"rule": "sha256(id)[0] % 2: even is A (calibration), odd is B (scored)",
                      "half_a_items": len(rows) - len(held), "half_b_items": len(held)}
    (out / "holdout.json").write_text(json.dumps(score, ensure_ascii=False, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    o = score["overall"]
    print(f"half B: {len(held)} items; recall {o['recall']}, false alarms {o['false_alarm_rate_clean']}, "
          f"localization {o['localization']}, category accuracy {o['category_accuracy']}")
    for cat, v in score["per_category"].items():
        print(f"  {cat}: {v['tp']} / {v['tp'] + v['fn']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
