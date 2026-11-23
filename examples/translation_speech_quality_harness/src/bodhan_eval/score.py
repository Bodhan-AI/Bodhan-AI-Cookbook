"""Scorecard: how a finished report does against gold labels, the way a hidden test would score it.

    python -m bodhan_eval.score --report outputs/demo --gold data/demo_gold.jsonl [--labels data/saksham_labels.jsonl] \
        [--exclude data/demo_labels.jsonl]

The harness never sees these labels: the run is blind, and this command compares its ``items.jsonl`` with the
gold file afterwards. Matching is the same as in calibration (``calibrate.match_item``): a label is *detected*
when a flag of the same category group overlaps it (or, without spans, exists on the item), *localised* when
the spans overlap, and the category is *correct* when the groups agree. Clean gold items (``labels: []``)
measure false alarms; unlabelled ones are skipped. A gold item missing from the report counts as missed.

``--labels`` (repeatable) reads ``id`` + ``lang`` + ``labels`` lines, e.g. a human review: they replace the gold
labels of the items they name, so reviewed items the gold file left unlabelled are scored too.
``--exclude`` drops the items used to calibrate the run (any JSONL with ``id`` and ``lang``), so the scores
are out of sample. Outputs ``scorecard.json`` and ``scorecard.md`` (deterministic).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from . import report
from .calibrate import evaluate, match_item
from .cli import setup_logging
from .dataset import apply_calibration_file, load_dataset
from .models import Flag, ItemResult

log = logging.getLogger("bodhan_eval.score")


def _flag(d: dict) -> Flag:
    span = tuple(d["span"]) if d.get("span") is not None else None
    source_span = tuple(d["source_span"]) if d.get("source_span") is not None else None
    return Flag(
        category=d["category"],
        severity=d.get("severity", "major"),
        confidence=float(d.get("confidence", 0.0)),
        span=span,
        source_span=source_span,
        text=d.get("text"),
        source_text=d.get("source_text"),
        evidence=d.get("evidence", []),
        checks=d.get("checks", []),
    )


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            obj = json.loads(line)
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def _ratio(a: int, b: int) -> float | None:
    return round(a / b, 4) if b else None


def score(
    report_dir: Path, gold: Path, exclude: Path | None = None, labels: list[Path] | None = None
) -> dict[str, Any]:
    reported = {(r["id"], r["lang"]): r for r in _read_jsonl(report_dir / "items.jsonl")}
    gold_items, rep = load_dataset(gold, None)
    before = len(rep.notes)
    for path in labels or []:
        apply_calibration_file(path, gold_items, None, rep, "translation")
    for note in rep.notes[before:]:  # a label line that matched no gold item
        log.warning("labels: %s: %s", note["item"], note["reason"])
    skip: set[tuple[str, str | None]] = set()
    if exclude is not None:
        skip = {(str(r["id"]), r.get("lang")) for r in _read_jsonl(exclude) if "id" in r}
    coverage = {"missing_from_report": 0, "not_ok_in_report": 0, "unlabelled_skipped": 0, "excluded": 0}
    results: list[ItemResult] = []
    uncertain: dict[str, list[Flag]] = {}
    for it in gold_items:
        if it.labels is None:
            coverage["unlabelled_skipped"] += 1
            continue
        if (it.id, it.lang) in skip or (it.id, None) in skip:
            coverage["excluded"] += 1
            continue
        rec = reported.get((it.id, it.lang))
        if rec is None:
            coverage["missing_from_report"] += 1
            rec = {"flags": [], "uncertain": []}
        elif rec.get("status") != "ok":
            coverage["not_ok_in_report"] += 1
        # every scored item counts, whatever its run status: an item the run could not check is a missed item
        results.append(ItemResult(item=it, status="ok", flags=[_flag(f) for f in rec.get("flags", [])]))
        uncertain[it.key] = [_flag(f) for f in rec.get("uncertain", [])]
    ev = evaluate(results)
    return {
        "headline": {
            "scored_items": ev["labelled_items"],
            "error_items": ev["labelled_items"] - ev["clean_items"],
            "clean_items": ev["clean_items"],
            **{k: ev[k] for k in ("labels", "recall", "item_recall", "false_alarm_rate", "precision")},
            **{k: ev[k] for k in ("localization", "category_accuracy", "category_accuracy_exact")},
        },
        "coverage": coverage,
        "per_category": ev["per_category"],
        "per_language": ev["per_language"],
        **_breakdowns(results, uncertain),
    }


def _breakdowns(results: list[ItemResult], uncertain: dict[str, list[Flag]]) -> dict[str, Any]:
    """Recall by who made the label (injector method or human), by severity, by language x category; and how
    many missed labels the review queue (uncertain flags) holds."""
    by_method: defaultdict[str, list[int]] = defaultdict(lambda: [0, 0])
    by_severity: defaultdict[str, list[int]] = defaultdict(lambda: [0, 0])
    matrix: defaultdict[str, defaultdict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    missed = caught = 0
    for r in results:
        labels = r.item.labels or []
        matches, _ = match_item(r.flags, labels)
        for lab, m in zip(labels, matches):
            hit = int(bool(m["detected"]))
            for counts in (
                by_method[lab.annotator or "unknown"],
                by_severity[lab.severity],
                matrix[r.item.lang][lab.category or f"unmapped:{lab.raw_type}"],
            ):
                counts[0] += 1
                counts[1] += hit
            if not hit:
                missed += 1
                caught += bool(match_item(uncertain.get(r.item.key, []), [lab])[0][0]["detected"])

    def table(d: dict[str, list[int]]) -> dict[str, dict]:
        return {k: {"labels": n, "detected": h, "recall": _ratio(h, n)} for k, (n, h) in sorted(d.items())}

    return {
        "per_method": table(by_method),
        "per_severity": table(by_severity),
        "matrix": {
            lang: {c: {"labels": n, "detected": h} for c, (n, h) in sorted(cats.items())}
            for lang, cats in sorted(matrix.items())
        },
        "review_queue": {"missed_labels": missed, "caught_by_review_queue": caught},
    }


# ---------------------------------------------------------------------------
# scorecard.md
# ---------------------------------------------------------------------------


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.1f} %"


def render_markdown(card: dict[str, Any]) -> str:
    h, cov = card["headline"], card["coverage"]
    out = [
        "# Scorecard",
        "",
        f"{h['scored_items']} items scored ({h['error_items']} with errors, {h['clean_items']} clean), "
        f"{h['labels']} labels. Missing from the report: {cov['missing_from_report']}; not checked (status not ok): "
        f"{cov['not_ok_in_report']}; excluded (calibration): {cov['excluded']}; unlabelled, skipped: "
        f"{cov['unlabelled_skipped']}.",
        "",
        "| metric | value |",
        "|---|---|",
    ]
    for key in ("recall", "item_recall", "false_alarm_rate", "precision", "localization", "category_accuracy"):
        out.append(f"| {key.replace('_', ' ')} | {_pct(h[key])} |")
    rq = card["review_queue"]
    out += ["", f"Review queue: {rq['caught_by_review_queue']} of {rq['missed_labels']} missed labels are in it.", ""]
    out += [
        "## Per category",
        "",
        "| category | labels | recall | precision | localisation |",
        "|---|---:|---:|---:|---:|",
    ]
    for c, v in card["per_category"].items():
        out.append(
            f"| {c} | {v['labels']} | {_pct(v['recall'])} | {_pct(v['precision'])} | {_pct(v['localization'])} |"
        )
    out += ["", "## Per language", "", "| language | labels | recall | false alarms |", "|---|---:|---:|---:|"]
    for lang, v in card["per_language"].items():
        out.append(f"| {lang} | {v['labels']} | {_pct(v['recall'])} | {_pct(v['false_alarm_rate'])} |")
    for title, key in (
        ("Per label source (injector method or annotator)", "per_method"),
        ("Per severity", "per_severity"),
    ):
        out += ["", f"## {title}", "", "| | labels | detected | recall |", "|---|---:|---:|---:|"]
        for k, v in card[key].items():
            out.append(f"| {k} | {v['labels']} | {v['detected']} | {_pct(v['recall'])} |")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# command
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m bodhan_eval.score", description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--report", type=Path, required=True, help="a run's output folder (reads items.jsonl)")
    ap.add_argument("--gold", type=Path, required=True, help="JSONL with the gold labels (labels: [] = clean)")
    ap.add_argument("--labels", type=Path, action="append", help="id + lang + labels lines replacing gold labels")
    ap.add_argument("--exclude", type=Path, help="JSONL of items to leave out (e.g. the calibration subset)")
    ap.add_argument("--out", type=Path, help="where to write scorecard.json/.md (default: --report)")
    ap.add_argument("--log-level", choices=("debug", "info", "warning", "error"), default="info")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.log_level)
    if not (args.report / "items.jsonl").is_file():
        log.error("no items.jsonl in %s: run the harness first", args.report)
        return 1
    try:
        card = score(args.report, args.gold, args.exclude, args.labels)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError) as e:
        log.error("cannot score: %s: %s", type(e).__name__, e)
        return 1
    if not card["headline"]["scored_items"]:
        log.error("no labelled items in %s (labels: [] marks a clean item; no labels key = unlabelled)", args.gold)
        return 1
    out = args.out or args.report
    out.mkdir(parents=True, exist_ok=True)
    report.write_json(out / "scorecard.json", card)
    md = render_markdown(card)
    (out / "scorecard.md").write_text(md, encoding="utf-8", newline="\n")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
