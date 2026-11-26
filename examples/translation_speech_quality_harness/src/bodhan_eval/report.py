"""Write summary.md, summary.json, items.jsonl, calibration.json, diff.md (+ review_queue.csv).
Everything here is deterministic given the same cache: no timestamps, sorted keys, rounded floats."""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from .calibrate import evaluate
from .dashboard import write_dashboard
from .normalize import tokenize

SEV_WEIGHT = {"minor": 1, "major": 5, "critical": 10}
TARGETS = [
    ("detection_recall", "Detection recall", 0.80, "min"),
    ("false_alarm_rate", "False alarms on clean items", 0.10, "max"),
    ("localization_rate", "Span overlaps seeded error", 0.70, "min"),
    ("category_accuracy", "Category accuracy", 0.70, "min"),
]


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2)


def _pct(x):
    return "n/a" if x is None else f"{x:.1%}"


def mqm(results) -> dict:
    out = defaultdict(lambda: {"penalty": 0, "words": 0})
    for r in results:
        if r["status"] != "ok":
            continue
        words = max(1, len(tokenize(r.get("translation") or r["source"])))
        out[r["lang"]]["words"] += words
        out[r["lang"]]["penalty"] += sum(SEV_WEIGHT.get(f["severity"], 1) for f in r["flags"])
    return {l: round(max(0.0, 100 - 100 * d["penalty"] / d["words"]), 2) for l, d in sorted(out.items())}


def build_summary(run: dict, cfg, cost_summary: dict, budget_hit: bool) -> dict:
    results = run["results"]
    ok = [r for r in results if r["status"] == "ok"]
    per_lang = {}
    for lang in sorted({r["lang"] for r in results}):
        rs = [r for r in ok if r["lang"] == lang]
        cats = Counter(f["category"] for r in rs for f in r["flags"])
        per_lang[lang] = {"items": len(rs), "flagged_items": sum(1 for r in rs if r["flags"]),
                          "flags_by_category": dict(sorted(cats.items()))}
    per_cat = Counter(f["category"] for r in ok for f in r["flags"])
    per_sev = Counter(f["severity"] for r in ok for f in r["flags"])
    split = run["split"]
    metrics_all = evaluate(results)
    if split["enabled"]:
        metrics = evaluate([r for r in results if r.get("split") == "holdout"])
        metrics_tune = evaluate([r for r in results if r.get("split") == "tune"])
    else:
        metrics, metrics_tune = metrics_all, None
    errors = Counter(e["stage"] for r in results for e in r["errors"])
    return {
        "dataset": Path(cfg.dataset).name,
        "items_total": len(results) + len(run["skipped"]), "items_evaluated": len(ok),
        "items_skipped_no_input": sum(1 for r in results if r["status"] == "skipped"),
        "items_crashed": sum(1 for r in results if r["status"] == "error"),
        "items_malformed_skipped": len(run["skipped"]), "items_filtered_by_lang": run["filtered"],
        "stage_errors": dict(sorted(errors.items())),
        "items_flagged": sum(1 for r in ok if r["flags"]),
        "per_language": per_lang, "flags_by_category": dict(sorted(per_cat.items())),
        "flags_by_severity": dict(sorted(per_sev.items())),
        "mqm_quality_score_by_language": mqm(results),
        "calibration_metrics": metrics, "metrics_tuning_set": metrics_tune, "metrics_all_labelled": metrics_all,
        "split": split, "judge": run["judge_stats"],
        "cost": cost_summary, "budget_exhausted": budget_hit,
        "thresholds": {k: round(v, 4) if isinstance(v, float) else v for k, v in sorted(run["thresholds"].items())},
        "threshold_tuning": run["tune_report"],
    }


def _item_row(r: dict) -> dict:
    sig = r["signals"]
    compact = {}
    tx = sig.get("text")
    if tx:
        compact["text"] = {"length_ratio": tx.get("length_ratio"), "chrf_ref": tx.get("chrf_ref"),
                           "script_frac_expected": (tx.get("script") or {}).get("frac_expected"),
                           "back_translation": (tx.get("bt") or {}).get("back_translation"),
                           "min_sentence_coverage": min([s["coverage"] for s in (tx.get("bt") or {}).get("src_sents", [])], default=None)}
    if sig.get("judge"):
        compact["judge"] = sig["judge"]
    if sig.get("speech"):
        sp = sig["speech"]
        compact["speech"] = {k: sp.get(k) for k in ("audio", "transcript", "cer", "deleted_frac", "max_deleted_run", "tail_deleted", "repetition")}
    return {"id": r["id"], "lang": r["lang"], "status": r["status"], "skip_reason": r.get("skip_reason"), "split": r.get("split"),
            "checks_run": r["checks_run"], "errors": r["errors"], "flags": r.get("flags", []),
            "suppressed": r.get("suppressed", []), "labels": r.get("labels"), "source": r["source"],
            "translation": r["translation"], "translation_generated": r.get("translation_generated", False),
            "speech_triggered_by": r.get("speech_triggered_by"), "signals": compact}


def write_report(run: dict, cfg, cost, budget_hit: bool, baseline_dir: str | None = None) -> dict:
    out = Path(cfg.out)
    out.mkdir(parents=True, exist_ok=True)
    summary = build_summary(run, cfg, cost.summary(), budget_hit)
    (out / "summary.json").write_text(_dump(summary), encoding="utf-8")
    rows = [_item_row(r) for r in run["results"]]
    with open(out / "items.jsonl", "w", encoding="utf-8") as f:
        f.writelines(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    write_dashboard(rows, summary, out / "dashboard.html")
    cal = dict(summary["calibration_metrics"])
    cal["thresholds"] = summary["thresholds"]
    cal["tuning"] = summary["threshold_tuning"]
    prim = [r for r in run["results"] if not summary["split"]["enabled"] or r.get("split") == "holdout"]
    cal["evaluated_on"] = "held-out items" if summary["split"]["enabled"] else "all labelled items (no held-out split)"
    cal["split"] = summary["split"]
    cal["metrics_tuning_set_in_sample"] = summary["metrics_tuning_set"]
    cal["metrics_all_labelled"] = summary["metrics_all_labelled"]
    cal["judge_only"] = _judge_only_metrics(prim)
    cal["checks_only"] = _checks_only_metrics(prim)
    (out / "calibration.json").write_text(_dump(cal), encoding="utf-8")
    _write_review_queue(run["results"], out / "review_queue.csv")
    diff = None
    if baseline_dir:
        diff = write_diff(run, summary, baseline_dir, out / "diff.md")
    (out / "summary.md").write_text(render_summary_md(summary, run, diff), encoding="utf-8")
    return {"summary": summary, "diff": diff}


def _judge_only_metrics(results):
    rs = []
    for r in results:
        fl = [f for f in r.get("flags", []) if "judge" in f["checks"]]
        rs.append({"status": r["status"], "labels": r.get("labels"), "flags": fl})
    return evaluate(rs)


def _checks_only_metrics(results):
    rs = []
    for r in results:
        fl = [f for f in r.get("flags", []) if f["checks"] != ["judge"]]
        rs.append({"status": r["status"], "labels": r.get("labels"), "flags": fl})
    return evaluate(rs)


def _write_review_queue(results, path: Path):
    """Uncertain items for human labelling: suppressed candidates, split judge votes, low confidence flags."""
    rows = []
    for r in results:
        if r["status"] != "ok":
            continue
        reasons = [f"suppressed:{s['category']}:{s['reason']}" for s in r["suppressed"]]
        reasons += [f"low_confidence:{f['category']}" for f in r["flags"] if f["confidence"] < 0.6]
        j = r["signals"].get("judge")
        if j and not j["verdict_consistent"]:
            reasons.append("judge_runs_disagree")
        if reasons:
            rows.append([r["id"], r["lang"], "; ".join(reasons), r["source"], r["translation"] or "", ""])
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "lang", "why_uncertain", "source", "translation", "human_label_type(s) [blank=clean]"])
        w.writerows(rows)


# ------------------------------------------------------------------ diff
def _load_items(d: Path) -> dict:
    p = d / "items.jsonl"
    if not p.exists():
        raise FileNotFoundError(f"baseline has no items.jsonl: {d}")
    return {o["id"]: o for o in (json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip())}


def write_diff(run: dict, summary: dict, baseline_dir: str, path: Path) -> dict:
    base_dir = Path(baseline_dir)
    try:
        base = _load_items(base_dir)
    except (OSError, ValueError) as e:
        path.write_text(f"# Diff vs baseline\n\nBaseline could not be read: {e}\n", encoding="utf-8")
        return {"error": str(e), "new_flags": 0, "resolved_flags": 0, "regressed_items": 0}
    base_sum = {}
    if (base_dir / "summary.json").exists():
        base_sum = json.loads((base_dir / "summary.json").read_text(encoding="utf-8"))
    new, resolved, only_cur = [], [], 0
    cur = {r["id"]: r for r in run["results"]}
    for iid, r in sorted(cur.items()):
        b = base.get(iid)
        if b is None:
            only_cur += 1
            continue
        cf = {(f["category"]) for f in r.get("flags", [])}
        bf = {(f["category"]) for f in b.get("flags", [])}
        for c in sorted(cf - bf):
            f = next(f for f in r["flags"] if f["category"] == c)
            new.append((iid, r["lang"], c, f["severity"], f["evidence"], r))
        for c in sorted(bf - cf):
            f = next(f for f in b["flags"] if f["category"] == c)
            resolved.append((iid, b["lang"], c, f["severity"], f["evidence"], b))
    only_base = len(set(base) - set(cur))
    cat_now = summary["flags_by_category"]
    cat_then = base_sum.get("flags_by_category", {})
    lines = ["# Diff vs baseline", "",
             f"Baseline: `{baseline_dir}`. Items only in this run: {only_cur}; only in baseline: {only_base}.", "",
             f"- Regressions (new flags): **{len(new)}**", f"- Improvements (flags gone): **{len(resolved)}**", "",
             "## Flags by category", "", "| category | baseline | now | change |", "| --- | --- | --- | --- |"]
    for c in sorted(set(cat_now) | set(cat_then)):
        a, b2 = cat_then.get(c, 0), cat_now.get(c, 0)
        lines.append(f"| {c} | {a} | {b2} | {b2 - a:+d} |")
    ml_now, ml_then = summary["mqm_quality_score_by_language"], base_sum.get("mqm_quality_score_by_language", {})
    lines += ["", "## Quality score by language (MQM-style, higher is better)", "", "| language | baseline | now |", "| --- | --- | --- |"]
    for l in sorted(set(ml_now) | set(ml_then)):
        lines.append(f"| {l} | {ml_then.get(l, 'n/a')} | {ml_now.get(l, 'n/a')} |")

    def examples(title, rows):
        lines.extend(["", f"## {title}", ""])
        if not rows:
            lines.append("None.")
        for iid, lang, c, sev, ev, _r in rows[:10]:
            lines.append(f"- `{iid}` ({lang}) **{c}** [{sev}] — {ev[:300]}")
        if len(rows) > 10:
            lines.append(f"- ... and {len(rows) - 10} more")

    examples("Regressions: flags that appeared", new)
    examples("Improvements: flags that disappeared", resolved)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"new_flags": len(new), "resolved_flags": len(resolved), "regressed_items": len({n[0] for n in new})}


# ------------------------------------------------------------------ summary.md
def render_summary_md(s: dict, run: dict, diff: dict | None) -> str:
    L = ["# Quality report", "",
         f"Dataset `{s['dataset']}`: {s['items_evaluated']} evaluated of {s['items_total']} "
         f"({s['items_malformed_skipped']} malformed skipped, {s['items_skipped_no_input']} without input, "
         f"{s['items_crashed']} crashed, {s['items_filtered_by_lang']} filtered by language). "
         f"**{s['items_flagged']} items flagged.**", ""]
    if s["stage_errors"]:
        L += ["Stage errors (items continued without that check): " + ", ".join(f"{k}={v}" for k, v in s["stage_errors"].items()), ""]
    if s["budget_exhausted"]:
        L += ["> **Budget exhausted:** some model calls were skipped; affected checks are missing for later items.", ""]
    m = s["calibration_metrics"]
    sp, mt = s["split"], s.get("metrics_tuning_set")
    L += ["## Targets (measured on labelled / injected items)", ""]
    if s["metrics_all_labelled"]["n_labelled_items"] == 0:
        L += ["No labelled items in this dataset; add `labels` or pass `--calibration` to measure targets.", ""]
    else:
        if sp["enabled"]:
            L += [f"**Held-out evaluation.** Thresholds were tuned on {sp['n_tune']} labelled items; the numbers below are measured on "
                  f"{sp['n_holdout']} other items the tuner never saw ({m['n_error_items']} with errors, {m['n_clean_items']} clean). "
                  "The last column is the tuning set, i.e. in-sample and optimistic. With this few items, per-category numbers are very noisy.", "",
                  "| Target | Goal | Held-out | Status | Tuning set (in-sample) |", "| --- | --- | --- | --- | --- |"]
        else:
            why = "fewer than 16 labelled items or --holdout-frac 0"
            L += [f"{m['n_labelled_items']} labelled items ({m['n_error_items']} with errors, {m['n_clean_items']} clean). "
                  f"No held-out split ({why}), so thresholds were tuned and scored on the same items: treat as optimistic.", "",
                  "| Target | Goal | Measured | Status |", "| --- | --- | --- | --- |"]
        for key, name, goal, kind in TARGETS:
            v = m[key]
            ok = v is not None and (v >= goal if kind == "min" else v <= goal)
            row = f"| {name} | {'≥' if kind == 'min' else '≤'} {goal:.0%} | {_pct(v)} | {'met' if ok else ('MISSED' if v is not None else 'n/a')} |"
            L.append(row + (f" {_pct(mt[key])} |" if sp["enabled"] else ""))
        j = s["judge"]
        row = (f"| Judge consistency (same verdict across re-runs) | ≥ 90% | {_pct(j['verdict_consistency']) if j else 'n/a'} | "
               f"{'n/a' if not j else ('met' if j['verdict_consistency'] >= 0.9 else 'MISSED')} |")
        L.append(row + (" - |" if sp["enabled"] else ""))
        L += ["", "Where targets are missed, see the per-category table below: low-recall categories rely on the judge "
              "(no deterministic check exists for them) and low-precision ones usually come from back-translation noise.", "",
              "### Precision / recall per category" + (" (held-out items)" if sp["enabled"] else ""), "",
              "| category | TP | FP | FN | precision | recall |", "| --- | --- | --- | --- | --- | --- |"]
        for c, d in m["per_category"].items():
            L.append(f"| {c} | {d['tp']} | {d['fp']} | {d['fn']} | {_pct(d['precision'])} | {_pct(d['recall'])} |")
        L.append("")
    L += ["## By language", "", "| language | items | flagged | MQM score | flags |", "| --- | --- | --- | --- | --- |"]
    for l, d in s["per_language"].items():
        L.append(f"| {l} | {d['items']} | {d['flagged_items']} | {s['mqm_quality_score_by_language'].get(l, 'n/a')} | "
                 f"{', '.join(f'{k}:{v}' for k, v in d['flags_by_category'].items()) or '-'} |")
    L += ["", "## By category and severity", "",
          "Categories: " + (", ".join(f"{k}={v}" for k, v in s["flags_by_category"].items()) or "none"),
          "", "Severities: " + (", ".join(f"{k}={v}" for k, v in s["flags_by_severity"].items()) or "none"), ""]
    # top failures
    sev = {"minor": 1, "major": 5, "critical": 10}
    ranked = sorted([r for r in run["results"] if r.get("flags")],
                    key=lambda r: (-sum(sev.get(f["severity"], 1) for f in r["flags"]), r["id"]))[:15]
    L += ["## Top failures", ""]
    if not ranked:
        L.append("No flags.")
    for r in ranked:
        L.append(f"### `{r['id']}` ({r['lang']})")
        L.append(f"Source: {r['source'][:200]}")
        for f in r["flags"]:
            where = []
            if f["span_source"]:
                where.append(f"source[{f['span_source'][0]}:{f['span_source'][1]}]=`{r['source'][f['span_source'][0]:f['span_source'][1]][:80]}`")
            if f["span_translation"] and r["translation"]:
                a, b = f["span_translation"]
                where.append(f"translation[{a}:{b}]=`{r['translation'][a:b][:80]}`")
            L.append(f"- **{f['category']}** ({f['severity']}, checks: {', '.join(f['checks'])}) {'; '.join(where)}  \n  {f['evidence'][:400]}")
        L.append("")
    if diff:
        L += ["## Regressions vs baseline", "",
              f"{diff.get('new_flags', 0)} new flags across {diff.get('regressed_items', 0)} items; {diff.get('resolved_flags', 0)} flags resolved. See `diff.md`.", ""]
    c = s["cost"]
    L += ["## Cost", "", f"Uncached cost of this run: **₹{c['uncached_total_inr']}** "
          + ", ".join(f"{k}: ₹{v}" for k, v in c["uncached_cost_inr"].items()) + ". "
          "A rerun from cache spends ₹0 and reproduces this report byte-for-byte.", ""]
    if s["judge"]:
        j = s["judge"]
        L += ["## Judge reliability", "",
              f"{j['items_judged']} items judged x {j['runs_per_item']} runs. Verdict consistency {_pct(j['verdict_consistency'])}, "
              f"category consistency {_pct(j['category_consistency'])}. {j['ungrounded_quotes_dropped']} findings dropped because the quoted span "
              f"was not found verbatim; {j['parse_failures']} unparseable responses.", ""]
    t = s["threshold_tuning"]
    L += ["## Thresholds", "", ("Tuned from labels: " + json.dumps(t.get("changed", {}), sort_keys=True)) if t.get("tuned") else f"Defaults used ({t.get('reason')}).", ""]
    return "\n".join(L)
