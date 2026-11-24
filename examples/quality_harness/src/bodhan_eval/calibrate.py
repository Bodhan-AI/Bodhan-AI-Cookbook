"""Calibration (plan §14): measure flags against labels, learn the check weights and the flag threshold.

    python -m bodhan_eval.calibrate --report report --labels data/labels.jsonl data/injected.jsonl

Labels are JSONL rows `{id, lang, labels: [{type, severity, side?, span?}]}`; `labels: []` means
checked and clean. A row with `base` is an injected variant: only what it adds over its base counts
(the base's own errors aren't labelled), so candidates the base also has are left out. Rows without
`labels` (the bases in an injected dataset) are not labels.

Matching: a flag finds a label when one of its candidates points at the label's place (fuse.overlaps);
a label with a span but no side may point into either text; a label without a place (an error the
reviewer found that no check raised) is found by a flag of its category anywhere in the item. Label
names are mapped by categories.label_categories ("number_unit", "truncation", "Garbled audio" ...). Fitting: weight = share of a signal's candidates that point at a real
error, shrunk toward the default by `shrinkage` pseudo-counts (per category toward the signal's
pooled weight). The flag threshold is swept for the most recall with false alarms on clean items at
or under `max_false_alarm_rate`. Held-out numbers: 5 folds by source text, so an item's variants and
its human/Bodhan twin never sit on both sides of a split. "keep" (change nothing) competes too, and
with fewer than MIN_CLEAN clean labelled items nothing is tuned: false alarms can't be measured.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from .fuse import fuse, load_thresholds, overlaps, places, signal
from .categories import label_categories
from .dataset import normalize_lang

FOLDS = 5
MIN_CLEAN = 10
GRID = [round(0.2 + 0.05 * i, 2) for i in range(16)]  # 0.20 .. 0.95


def load_labels(paths) -> dict:
    out = {}
    for path in paths:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line) if line.strip() else {}
            except json.JSONDecodeError:
                continue  # the dataset loader reports bad lines
            if not isinstance(row, dict) or not isinstance(row.get("labels"), list) or "id" not in row or "lang" not in row:
                continue
            gold = []
            for lab in row["labels"]:
                cat, accept = label_categories(lab)
                span = lab.get("span")
                locs = None
                if isinstance(span, list) and len(span) == 2:
                    sides = [lab["side"]] if lab.get("side") in ("source", "translation") else ["translation", "source"]
                    locs = [(side, int(span[0]), int(span[1])) for side in sides]
                gold.append({"category": cat, "accept": accept, "severity": lab.get("severity"), "locs": locs})
            out[(str(row["id"]), normalize_lang(str(row["lang"])) or str(row["lang"]))] = {"gold": gold, "base": row.get("base"),
                                             "kind": "injected" if row.get("base") else "human"}
    return out


def keys(c: dict) -> set:
    """What makes two candidates the same finding: same text, or same evidence (a span that grew with the
    edit, e.g. a missing heading's gap, has new text but the same evidence)."""
    head = (c["check"], c["category"], c["side"])
    return {(*head, "text", c["text"]), (*head, "evidence", json.dumps(c["evidence"], sort_keys=True, ensure_ascii=False))}


def examples(records: list[dict], labels: dict) -> tuple[list[dict], int]:
    """Labelled, scored items with the candidates that count; and how many labelled items were not usable."""
    recs = {(r["id"], r["lang"]): r for r in records}
    out, missing = [], 0
    for (id_, lang), lab in sorted(labels.items()):
        rec = recs.get((id_, lang))
        base = recs.get((lab["base"], lang)) if lab["base"] else None
        if rec is None or "translation" in rec["checks"] or (lab["base"] and base is None):
            missing += 1
            continue
        old = set().union(*(keys(c) for c in base["candidates"])) if base else set()
        out.append({"key": (id_, lang), "record": rec, "cands": [c for c in rec["candidates"] if not keys(c) & old],
                    "gold": lab["gold"], "kind": lab["kind"], "lang": lang,
                    "origin": rec.get("translation_origin") or "?", "group": rec["source"]})
    return out, missing


def _lengths(rec):
    return {"source": len(rec["source"]), "translation": len(rec["translation"] or "")}


def _at(c, lab, lengths):
    return any(overlaps(places(c), [loc], lengths) for loc in lab["locs"])


def _cand_finds(c, lab, lengths):
    return c["category"] in lab["accept"] if lab["locs"] is None else _at(c, lab, lengths)


def _flag_finds(f, cands, lab, lengths):
    if lab["locs"] is None:
        return bool(lab["accept"] & {f["category"], *f["alt_categories"]})
    return any(_at(cands[i], lab, lengths) for i in f["candidates"])


def evaluate(exs: list[dict], th: dict) -> Counter:
    """Raw counts, keyed (scope, name, count); add Counters across folds, then `rates`."""
    t: Counter = Counter()
    for ex in exs:
        rec, cands, lengths = ex["record"], ex["cands"], _lengths(ex["record"])
        flags = [f for f in fuse(rec, th, cands)[0] if f["decision"] == "flag"]
        scopes = [("all", "all"), ("lang", ex["lang"]), ("kind", ex["kind"]), ("origin", ex["origin"])]
        if not ex["gold"]:
            for s in scopes:
                t[(*s, "clean")] += 1
                t[(*s, "clean_flagged")] += bool(flags)
        for lab in ex["gold"]:
            hits = [f for f in flags if _flag_finds(f, cands, lab, lengths)]
            for s in [*scopes, ("category", lab["category"])]:
                t[(*s, "labels")] += 1
                t[(*s, "item_flagged")] += bool(flags)
                t[(*s, "found")] += bool(hits)
                t[(*s, "right_category")] += any(f["category"] in lab["accept"] for f in hits)
        for f in flags:
            true = [lab for lab in ex["gold"] if _flag_finds(f, cands, lab, lengths)]
            for s in [*scopes, ("category", f["category"])]:
                t[(*s, "flags")] += 1
                t[(*s, "flags_true")] += bool(true)
                t[(*s, "flags_right_category")] += any(f["category"] in lab["accept"] for lab in true)
    return t


def rates(t: Counter) -> dict:
    def r(a, b):
        return round(a / b, 3) if b else None

    out: dict = defaultdict(dict)
    for scope, name in sorted({k[:2] for k in t}):
        g = {k[2]: v for k, v in t.items() if k[:2] == (scope, name)}
        n, flags = g.get("labels", 0), g.get("flags", 0)
        out[scope][name] = {
            "labels": n, "recall": r(g.get("found", 0), n), "recall_right_category": r(g.get("right_category", 0), n),
            "item_recall": r(g.get("item_flagged", 0), n),
            "flags": flags, "precision": r(g.get("flags_true", 0), flags),
            "precision_right_category": r(g.get("flags_right_category", 0), flags),
            "clean_items": g.get("clean", 0), "false_alarm_rate": r(g.get("clean_flagged", 0), g.get("clean", 0)),
        }
    return dict(out)


def fit(exs: list[dict], th: dict) -> tuple[dict, dict]:
    """Weights from the labels: (thresholds with new weights, counts per signal/category/kind)."""
    counts: dict = defaultdict(lambda: [0, 0])
    for ex in exs:
        lengths = _lengths(ex["record"])
        for c in ex["cands"]:
            right = any(_cand_finds(c, lab, lengths) for lab in ex["gold"])
            for cat in (c["category"], "*"):
                for kind in (ex["kind"], "all"):
                    counts[(signal(c), cat, kind)][0] += right
                    counts[(signal(c), cat, kind)][1] += 1
    new, k = copy.deepcopy(th), th["shrinkage"]
    for sig in sorted({s for s, _, _ in counts}):
        prior = th["weights"].get(sig, {})
        pooled_prior = prior.get("*", th["default_weight"])
        right, n = counts[(sig, "*", "all")]
        pooled = (right + k * pooled_prior) / (n + k)
        w = new["weights"].setdefault(sig, {})
        w["*"] = round(pooled, 3)
        for cat in sorted({c for s, c, _ in counts if s == sig and c != "*"}):
            right, n = counts[(sig, cat, "all")]
            w[cat] = round((right + k * pooled) / (n + k), 3)
    return new, {f"{s}|{c}|{kd}": v for (s, c, kd), v in sorted(counts.items())}


def _score(a: dict | None, max_far: float) -> tuple:
    """Higher is better: false alarms within the limit, then recall (else fewer false alarms), then precision."""
    if a is None:  # nothing to measure on
        return False, -1.0, 0.0
    far = a["false_alarm_rate"] or 0.0
    ok = far <= max_far
    return ok, (a["recall"] or 0.0) if ok else -far, a["precision"] or 0.0


def sweep(exs: list[dict], th: dict) -> dict:
    """The flag threshold with the most recall at false alarms <= max_false_alarm_rate (else the fewest)."""
    band = th["flag"] - th["uncertain"]
    best = None
    for t in GRID:
        cand = {**th, "flag": t, "uncertain": round(max(t - band, 0.0), 2)}
        score = (*_score(rates(evaluate(exs, cand))["all"]["all"], th["max_false_alarm_rate"]), t)
        if best is None or score > best[0]:
            best = (score, cand)
    return best[1]


# What calibration may change. Each is tried on held-out folds; the best one is used. The adjudicator's
# answers are stored in the records, so turning it on or off needs no new calls.
STRATEGIES = {
    "keep": lambda exs, th: th,
    "threshold": lambda exs, th: sweep(exs, {**th, "adjudicate": False}),
    "adjudicator+threshold": lambda exs, th: sweep(exs, {**th, "adjudicate": True}),
    "weights+threshold": lambda exs, th: sweep(exs, fit(exs, {**th, "adjudicate": False})[0]),
}


def _fold(ex):
    return int(hashlib.sha256(ex["group"].encode("utf-8")).hexdigest(), 16) % FOLDS


def calibrate(records: list[dict], labels: dict, th: dict) -> tuple[dict, dict]:
    """(calibration.json content, tuned thresholds)."""
    exs, missing = examples(records, labels)
    clean = sum(1 for e in exs if not e["gold"])
    strategies = STRATEGIES if clean >= MIN_CLEAN else {"keep": STRATEGIES["keep"]}
    held = {}
    for name, tune in strategies.items():
        held[name] = Counter()
        for f in range(FOLDS):
            train, test = [e for e in exs if _fold(e) != f], [e for e in exs if _fold(e) == f]
            if train and test:
                held[name] += evaluate(test, tune(train, th))
    # Ties go to the earlier strategy: "keep" wins unless tuning measurably helps.
    chosen = max(strategies, key=lambda n: (_score(rates(held[n]).get("all", {}).get("all"), th["max_false_alarm_rate"]),
                                            -list(strategies).index(n)))
    tuned = strategies[chosen](exs, th)
    fitted, counts = fit(exs, th)
    report = {
        "labelled_items": {"usable": len(exs), "not_in_report": missing,
                           **Counter(e["kind"] for e in exs),
                           "clean": sum(1 for e in exs if not e["gold"]),
                           "labels": sum(len(e["gold"]) for e in exs)},
        "chosen": chosen,
        **({} if clean >= MIN_CLEAN else {"tuning": f"skipped: only {clean} clean labelled items, {MIN_CLEAN} needed"}),
        "thresholds": {"default": {"flag": th["flag"], "uncertain": th["uncertain"]},
                       "tuned": {"flag": tuned["flag"], "uncertain": tuned["uncertain"]}},
        "metrics": {"default": rates(evaluate(exs, th)), "tuned_in_sample": rates(evaluate(exs, tuned)),
                    **{f"held_out:{n}": rates(c) for n, c in held.items()}},
        "weights": {"used": tuned["weights"], "default": th["weights"], "fitted": fitted["weights"],
                    "support": {k: {"right": r, "n": n} for k, (r, n) in counts.items()}},
        "notes": [
            "recall: labels a flag points at (same place; a label with no place: a flag of its category in the item). "
            "item_recall: labelled errors whose item has any flag. precision: flags that point at a label.",
            "false_alarm_rate: clean labelled items (labels: []) with any flag.",
            "Injected variants are scored on what they add over their base; the base's own errors are not labelled.",
            "held_out:<strategy>: tuned on 4 folds, measured on the 5th (folds by source text). `chosen` is the "
            "strategy with the better held-out result; tuned_in_sample is it refitted on all labels (optimistic).",
            "support: per signal|category|kind, how many of its candidates pointed at a labelled error (right) of n.",
        ],
    }
    return report, tuned


def apply(records: list[dict], th: dict) -> None:
    for r in records:
        r["flags"], r["verdict"], r["mqm"] = fuse(r, th)


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m bodhan_eval.calibrate")
    p.add_argument("--report", required=True, type=Path, help="report directory with items.jsonl")
    p.add_argument("--labels", required=True, nargs="+", type=Path, help="labels JSONL files")
    p.add_argument("--thresholds", type=Path, help="starting thresholds (default: thresholds.default.json)")
    p.add_argument("--out", type=Path, help="where to write calibration.json and thresholds.tuned.json (default: --report)")
    args = p.parse_args(argv)
    records = [json.loads(line) for line in (args.report / "items.jsonl").open(encoding="utf-8")]
    report, tuned = calibrate(records, load_labels(args.labels), load_thresholds(args.thresholds))
    write(args.out or args.report, report, tuned)
    print_summary(report)


def write(out: Path, report: dict, tuned: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "calibration.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "thresholds.tuned.json").write_text(json.dumps(tuned, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def print_summary(report: dict) -> None:
    print(json.dumps(report["labelled_items"]), json.dumps(report["thresholds"]))
    cols = ("labels", "recall", "recall_right_category", "item_recall", "flags", "precision", "clean_items", "false_alarm_rate")
    for name, m in report["metrics"].items():
        print(f"\n## {name}\n| scope | " + " | ".join(cols) + " |")
        for scope in ("all", "kind", "origin", "lang", "category"):
            for k, v in m.get(scope, {}).items():
                print(f"| {scope}:{k} | " + " | ".join("" if v[c] is None else str(v[c]) for c in cols) + " |")


if __name__ == "__main__":
    main()
