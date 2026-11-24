"""Report writer. Produces the required files deterministically: byte-identical on a cached
rerun. All wall-clock / environment data lives in meta.json, never in the scored outputs.

Files: summary.md, summary.json, items.jsonl, calibration.json, diff.md (with --baseline),
plus logs/run.jsonl (written live by the logger), review_queue.csv and meta.json.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from . import __version__
from .judge import load_rubric
from .models import SEVERITY_WEIGHT, ItemResult
from .pipeline import RunConfig, RunState

TARGETS = {"recall": 0.80, "false_alarms": 0.10, "localization": 0.70,
           "category_accuracy": 0.70, "judge_consistency": 0.90}


def _dump_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def write_reports(out: str, state: RunState, cfg: RunConfig, calibration: dict | None,
                  calib_notes: dict | None, meta: dict) -> None:
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    results = [state.results[k] for k in sorted(state.results)]

    _write_items(out_dir / "items.jsonl", results)
    summary = _build_summary(state, results, calibration)
    summary["evaluation_scope"] = {"judge_enabled": not cfg.no_judge,
                                   "speech_enabled": not cfg.no_speech,
                                   "judge_priced": state.judge_priced}
    _dump_json(out_dir / "summary.json", summary)
    _dump_json(out_dir / "calibration.json",
               {"metrics": calibration or {}, "fit": calib_notes or {},
                "note": "Only explicitly labelled items are scored; spans use the declared space."})
    _write_summary_md(out_dir / "summary.md", summary, results, calibration)
    _write_review_queue(out_dir / "review_queue.csv", results)
    _dump_json(out_dir / "meta.json", meta)


def write_dashboard_data(out: str, state: RunState, summary: dict, calibration: dict | None,
                         diff: dict | None) -> dict:
    """Self-contained, deterministic data bundle the static dashboard inlines. Includes the
    source/translation TEXT (joined from the loaded items) so the proof sheet can highlight the
    flagged spans. No timestamps."""
    out_dir = Path(out)
    items_by_id = {it.id: it for it in state.load_report.items} if state.load_report else {}
    item_rows = []
    for r in [state.results[k] for k in sorted(state.results)]:
        it = items_by_id.get(r.id)
        flags = []
        for f in r.flags:
            flags.append({
                "category": f.category, "severity": f.severity,
                "confidence": round(float(f.confidence), 3),
                "source_span": list(f.source_span) if f.source_span else None,
                "target_span": list(f.target_span) if f.target_span else None,
                "evidence": [{
                    "check": e.check, "strength": e.strength, "noisy": e.noisy,
                    "score": round(float(e.score), 4),
                    "explanation": e.details.get("explanation") or e.details.get("issue")
                    or e.details.get("reason") or "",
                    "details": e.details,
                } for e in f.evidence],
            })
        item_rows.append({
            "id": r.id, "lang": r.lang, "status": r.status,
            "source": (it.source if it else "") or "",
            "translation": (it.translation if it else None) or "",
            "reference": (it.reference if it else None) or "",
            "flags": flags,
            "uncertain_categories": r.details.get("uncertain_categories", []),
            "judge_verdict": r.details.get("judge_verdict"),
            "checker_disagreement": bool(r.details.get("checker_disagreement")),
            "signal": r.details.get("signal"),
            "cost_inr": round(r.cost_inr, 6),
        })
    data = {"summary": summary, "items": item_rows,
            "calibration": calibration or {}, "diff": diff or None}
    _dump_json(out_dir / "dashboard_data.json", data)
    return data


# ---- items.jsonl ----------------------------------------------------------------------


def _write_items(path: Path, results: list[ItemResult]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for res in results:
            fh.write(json.dumps(res.to_json(), ensure_ascii=False, sort_keys=True) + "\n")


# ---- summary.json ---------------------------------------------------------------------


def _build_summary(state: RunState, results: list[ItemResult], calibration: dict | None) -> dict:
    status_counts: Counter = Counter(r.status for r in results)
    by_lang: dict = defaultdict(lambda: {"total": 0, "flagged": 0, "uncertain": 0, "clean": 0,
                                          "skipped": 0, "categories": Counter()})
    cat_counts: Counter = Counter()
    sev_counts: Counter = Counter()
    mqm_by_lang: dict = defaultdict(float)
    disagreements = 0
    logical_cost = 0.0

    for r in results:
        L = by_lang[r.lang]
        L["total"] += 1
        L[r.status] = L.get(r.status, 0) + 1
        logical_cost += r.cost_inr
        if r.details.get("checker_disagreement"):
            disagreements += 1
        for fl in r.flags:
            cat_counts[fl.category] += 1
            sev_counts[fl.severity] += 1
            L["categories"][fl.category] += 1
            mqm_by_lang[r.lang] += SEVERITY_WEIGHT.get(fl.severity, 1)

    skip_reasons = Counter(r.skip_reason for r in results if r.status == "skipped" and r.skip_reason)

    by_lang_out = {}
    for lang, d in sorted(by_lang.items()):
        by_lang_out[lang] = {
            "total": d["total"], "clean": d.get("clean", 0), "flagged": d.get("flagged", 0),
            "uncertain": d.get("uncertain", 0), "skipped": d.get("skipped", 0),
            "flags_by_category": dict(sorted(d["categories"].items())),
            "mqm_penalty": round(mqm_by_lang.get(lang, 0.0), 2),
        }

    return {
        "totals": {
            "items": len(results),
            "clean": status_counts.get("clean", 0),
            "flagged": status_counts.get("flagged", 0),
            "uncertain": status_counts.get("uncertain", 0),
            "skipped": status_counts.get("skipped", 0),
        },
        "by_language": by_lang_out,
        "flags_by_category": dict(sorted(cat_counts.items())),
        "flags_by_severity": dict(sorted(sev_counts.items())),
        "checker_disagreements": disagreements,
        "skipped_reasons": dict(sorted(skip_reasons.items())),
        "skipped_for_budget": sorted(state.skipped_for_budget),
        "judge_consistency": _judge_consistency(state),
        "judge_tokens": {k: v for k, v in state.judge_usage.items() if not k.startswith("uncached_")},
        # logical cost only (deterministic). ACTUAL spend (0 on a cached rerun) lives in meta.json.
        "cost": {"logical_total_inr": round(logical_cost, 4)},
        "calibration_summary": _calibration_headline(calibration),
        "load": state.load_report.counts() if state.load_report else {},
    }


def _judge_consistency(state: RunState) -> dict:
    if not state.judge_consistency:
        return {"measured": False}
    vals = list(state.judge_consistency.values())
    v = sum(x["verdict_agreement"] for x in vals) / len(vals)
    c = sum(x["category_agreement"] for x in vals) / len(vals)
    return {"measured": True, "items": len(vals), "verdict_agreement": round(v, 4),
            "category_agreement": round(c, 4)}


def _calibration_headline(calibration: dict | None) -> dict | None:
    if not calibration:
        return None
    allcell = calibration.get("cells", {}).get("_all|_all", {})
    fa = calibration.get("false_alarms", {}).get("_all", {})
    return {"recall": allcell.get("recall"), "precision": allcell.get("precision"),
            "f1": allcell.get("f1"), "localization": allcell.get("localization"),
            "category_accuracy": allcell.get("category_accuracy"),
            "false_alarm_rate": fa.get("rate")}


# ---- summary.md -----------------------------------------------------------------------


def _write_summary_md(path: Path, summary: dict, results: list[ItemResult],
                      calibration: dict | None) -> None:
    t = summary["totals"]
    lines: list[str] = []
    lines.append("# Quality harness summary\n")
    scope = summary.get("evaluation_scope", {})
    lines.append(f"- Judge requested: **{'no' if not scope.get('judge_enabled') else 'yes'}**; "
                 f"speech requested: **{'no' if not scope.get('speech_enabled') else 'yes'}**. "
                 "See item details and logs for failures or unavailable checks.")
    lines.append("- Status 'clean' means no flag was raised; it is not a human-verified clean label.\n")
    if scope.get("judge_priced"):
        lines.append("- The budget includes estimated judge charges at the supplied rates; "
                     "provider billing may differ. The logical cost below covers Bodhan only.\n")
    else:
        lines.append("- Costs and the configured budget cover Bodhan calls only; judge-provider charges "
                     "are separate and are not included.\n")
    judge_tokens = summary.get("judge_tokens", {})
    if judge_tokens.get("calls"):
        lines.append(f"- Judge usage: {judge_tokens['calls']} calls, "
                     f"{judge_tokens.get('prompt_tokens', 0)} input tokens, "
                     f"{judge_tokens.get('completion_tokens', 0)} output tokens "
                     f"({judge_tokens.get('usage_missing', 0)} calls without usage data).")
    lines.append(f"- Items scored: **{t['items']}** "
                 f"(clean {t['clean']}, flagged {t['flagged']}, uncertain {t['uncertain']}, "
                 f"skipped {t['skipped']})")
    lines.append(f"- Logical Bodhan cost (cache-independent): "
                 f"**₹{summary['cost']['logical_total_inr']}** "
                 f"(actual spend this run is in meta.json; ₹0 on a cached rerun)")
    lines.append(f"- Checker disagreements (routed to review): {summary['checker_disagreements']}")
    jc = summary["judge_consistency"]
    if jc.get("measured"):
        lines.append(f"- Judge consistency: verdict {jc['verdict_agreement']:.0%}, "
                     f"category {jc['category_agreement']:.0%} (target ≥ 90%)")
    lines.append("")

    # headline metrics vs targets
    head = summary.get("calibration_summary")
    if head:
        lines.append("## Detection vs targets (on labelled items)\n")
        lines.append("| Metric | Value | Target | Met |")
        lines.append("| --- | --- | --- | --- |")
        rows = [("Recall", head.get("recall"), TARGETS["recall"], True),
                ("False-alarm rate", calibration.get("false_alarms", {}).get("_all", {}).get("rate")
                 if calibration else None, TARGETS["false_alarms"], False),
                ("Localization", head.get("localization"), TARGETS["localization"], True),
                ("Category accuracy", head.get("category_accuracy"), TARGETS["category_accuracy"], True)]
        for name, val, target, higher_better in rows:
            met = "—"
            if val is not None:
                ok = val >= target if higher_better else val <= target
                met = "✅" if ok else "❌"
            vs = f"{val:.2f}" if isinstance(val, (int, float)) else "n/a"
            lines.append(f"| {name} | {vs} | {target:.2f} | {met} |")
        lines.append("")

    # per-language table
    lines.append("## Per-language\n")
    lines.append("| Lang | Items | Clean | Flagged | Uncertain | MQM penalty |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for lang, d in summary["by_language"].items():
        lines.append(f"| {lang} | {d['total']} | {d['clean']} | {d['flagged']} | "
                     f"{d['uncertain']} | {d['mqm_penalty']} |")
    lines.append("")

    # per-category counts
    lines.append("## Flags by category\n")
    if summary["flags_by_category"]:
        lines.append("| Category | Flags |")
        lines.append("| --- | --- |")
        for cat, n in summary["flags_by_category"].items():
            lines.append(f"| {cat} | {n} |")
    else:
        lines.append("_No flags raised._")
    lines.append("")

    # top failure examples with evidence
    lines.append("## Top failures (with evidence)\n")
    flagged = [r for r in results if r.status == "flagged"]
    flagged.sort(key=lambda r: -max((SEVERITY_WEIGHT.get(f.severity, 1) for f in r.flags), default=0))
    for r in flagged[:5]:
        for fl in r.flags[:2]:
            ev = fl.evidence[0] if fl.evidence else None
            detail = ""
            if ev:
                detail = ", ".join(f"{k}={v}" for k, v in list(ev.details.items())[:3])
            span = fl.target_span or fl.source_span
            lines.append(f"- **{r.id}** [{r.lang}] — `{fl.category}`/{fl.severity} "
                         f"(conf {fl.confidence:.2f}), span {span}. {detail}")
    if not flagged:
        lines.append("_No flagged items._")
    lines.append("")

    lines.append("## Limitations\n")
    lines.append("- Back-translation and ASR are noisy; lone noisy signals are routed to the "
                 "review queue, not flagged, which trades recall for precision.")
    lines.append("- Number-word (spelled-out) quantities are handled only conservatively.")
    lines.append("- Low-support categories/languages fall back to pooled thresholds (see "
                 "calibration.json).")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---- review queue ---------------------------------------------------------------------


def _write_review_queue(path: Path, results: list[ItemResult]) -> None:
    rows = [r for r in results if r.status == "uncertain"]
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "lang", "uncertain_categories", "evidence_checks", "note"])
        for r in sorted(rows, key=lambda x: x.id):
            checks = ";".join(sorted({e.check for e in r.evidence}))
            cats = ";".join(r.details.get("uncertain_categories", []))
            w.writerow([r.id, r.lang, cats, checks, "needs human label"])


# ---- meta -----------------------------------------------------------------------------


def build_meta(cfg: RunConfig, started_at: float, ended_at: float, state: RunState) -> dict:
    _, rubric_hash = load_rubric()
    return {
        "version": __version__,
        "rubric_hash": rubric_hash,
        "started_at": started_at,
        "ended_at": ended_at,
        "runtime_seconds": round(ended_at - started_at, 2),
        "actual_spend_inr": state.ledger.summary() if state.ledger else {},
        "judge_token_usage": state.judge_usage,
        "config": {k: getattr(cfg, k) for k in ("langs", "generate", "no_judge", "no_speech",
                                                "speech_sample_rate", "judge_repeats",
                                                "concurrency", "seed", "max_cost_inr",
                                                "judge_input_inr_per_million",
                                                "judge_output_inr_per_million")},
    }
