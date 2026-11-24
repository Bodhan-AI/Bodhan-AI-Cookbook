"""summary.json and summary.md. Deterministic: no timestamps, sorted keys, rounded floats."""

from __future__ import annotations

from collections import Counter, defaultdict
from statistics import mean

from ..calibration import TARGETS
from ..models import Category, ItemResult, Severity

SEV_WEIGHT = {s.value: s.weight for s in Severity}


def _r(x: float | None, nd: int = 4) -> float | None:
    return None if x is None else round(x, nd)


def build_summary(
    results: list[ItemResult],
    *,
    bad_rows: list[dict],
    filtered: int,
    cost: dict,
    calibration: dict | None,
    consistency: dict | None,
    config: dict,
    thresholds: dict,
    diff: dict | None,
) -> dict:
    scored = [r for r in results if r.status == "scored"]
    per_lang: dict[str, dict] = {}
    by_lang: dict[str, list[ItemResult]] = defaultdict(list)
    for r in results:
        by_lang[r.lang].append(r)
    for lang, rs in sorted(by_lang.items()):
        sc = [r for r in rs if r.status == "scored"]
        flagged = [r for r in sc if r.flags]
        cats = Counter(f.category.value for r in sc for f in r.flags)
        per_lang[lang] = {
            "items": len(rs),
            "scored": len(sc),
            "failed": sum(r.status == "failed" for r in rs),
            "skipped": sum(r.status == "skipped" for r in rs),
            "flagged_items": len(flagged),
            "flag_rate": _r(len(flagged) / len(sc)) if sc else None,
            "flags_by_category": dict(sorted(cats.items())),
            "mqm_quality_mean": _r(mean(r.quality_score for r in sc if r.quality_score is not None), 2) if sc else None,
            "mqm_penalty_per_item": _r(mean(r.mqm_penalty for r in sc), 3) if sc else None,
            "speech_checked": sum(1 for r in sc if r.audio),
            "back_translated": sum(1 for r in sc if r.back_translation),
        }
    per_cat = {}
    for cat in Category:
        fl = [(r, f) for r in scored for f in r.flags if f.category == cat]
        per_cat[cat.value] = {
            "area": cat.area,
            "flags": len(fl),
            "items": len({r.id for r, _ in fl}),
            "by_severity": dict(sorted(Counter(f.severity.value for _, f in fl).items())),
            "by_language": dict(sorted(Counter(r.lang for r, _ in fl).items())),
        }
    content = Counter(r.content_type for r in scored)
    check_status: dict[str, Counter] = defaultdict(Counter)
    skip_reasons: Counter = Counter()
    for r in results:
        for c in r.checks:
            check_status[c.name][c.status] += 1
            if c.status == "skipped" and c.reason:
                skip_reasons[f"{c.name}: {c.reason}"] += 1
    warnings = Counter(w.split(":")[0] for r in results for w in r.warnings)
    summary = {
        "config": config,
        "items": {
            "rows_rejected": len(bad_rows),
            "filtered_by_lang": filtered,
            "evaluated": len(results),
            "scored": len(scored),
            "failed": sum(r.status == "failed" for r in results),
            "skipped": sum(r.status == "skipped" for r in results),
            "flagged": sum(1 for r in scored if r.flags),
            "clean": sum(1 for r in scored if not r.flags),
            "by_content_type": dict(sorted(content.items())),
        },
        "per_language": per_lang,
        "per_category": per_cat,
        "checks": {k: dict(sorted(v.items())) for k, v in sorted(check_status.items())},
        "skip_reasons": dict(skip_reasons.most_common(15)),
        "warnings": dict(sorted(warnings.items())),
        "bad_rows": bad_rows,
        "cost": cost,
        "thresholds": thresholds,
        "judge_consistency": consistency,
        "quality_targets": quality_targets(calibration, consistency),
        "baseline_diff": diff["headline"] if diff else None,
    }
    return summary


def quality_targets(calibration: dict | None, consistency: dict | None) -> dict:
    out: dict = {}
    source = None
    if calibration:
        source = calibration.get("dataset") or calibration.get("calibration_set")
    head = source["headline"] if source else {}
    for key in ("detection_recall", "false_alarm_rate", "localization", "category_accuracy"):
        value = head.get(key)
        target = TARGETS[key]
        met = None if value is None else (value <= target if key == "false_alarm_rate" else value >= target)
        out[key] = {"value": value, "target": target, "met": met}
    value = consistency.get("verdict_agreement") if consistency else None
    out["judge_consistency"] = {"value": value, "target": TARGETS["judge_consistency"], "met": None if value is None else value >= TARGETS["judge_consistency"]}
    out["measured_on"] = ("dataset labels" if calibration and calibration.get("dataset") else "calibration set") if source else None
    return out


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.0%}"


def _top_failures(results: list[ItemResult], n: int = 12) -> list[tuple[ItemResult, object]]:
    pairs = [(r, f) for r in results if r.status == "scored" for f in r.flags]
    pairs.sort(key=lambda p: (-(SEV_WEIGHT[p[1].severity.value] * p[1].confidence), p[0].id, p[1].category.value))
    return pairs[:n]


def _md_escape(text: str | None) -> str:
    if not text:
        return ""
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _miss_reasons(summary: dict, calibration: dict | None) -> list[str]:
    reasons = []
    targets = summary["quality_targets"]
    src = (calibration or {}).get("dataset") or (calibration or {}).get("calibration_set")
    if src:
        cats = src["per_category"]
        weak = sorted(((k, v["recall"]) for k, v in cats.items() if v.get("labels") and v.get("recall") is not None), key=lambda x: x[1])[:3]
        if targets["detection_recall"]["met"] is False and weak:
            reasons.append("Recall is lowest for " + ", ".join(f"`{k}` ({_pct(v)})" for k, v in weak) + ".")
        noisy = sorted(((k, v["precision"]) for k, v in cats.items() if v.get("flags") and v.get("precision") is not None), key=lambda x: x[1])[:3]
        if targets["false_alarm_rate"]["met"] is False and noisy:
            reasons.append("False alarms come mostly from low-precision categories: " + ", ".join(f"`{k}` (precision {_pct(v)})" for k, v in noisy) + ".")
        if targets["localization"]["met"] is False:
            reasons.append("Some detected errors point at the wrong span; localization is measured among detected labels with spans.")
        if targets["category_accuracy"]["met"] is False:
            reasons.append("Category confusions are listed per category in `calibration.json` (precision vs recall).")
    if targets["judge_consistency"]["met"] is False:
        reasons.append("The judge changed its verdict on some re-reads; use `--judge-votes 3` to majority-vote, at 3x the LLM calls.")
    return reasons


def _coverage_notes(summary: dict) -> list[str]:
    notes = []
    checks = summary["checks"]
    bt = checks.get("back_translation", {})
    if bt and not bt.get("ok"):
        notes.append("Back-translation did not run (no indic-translate key or budget), so omission, addition and term checks relied on the judge alone.")
    if not checks.get("back_transcription", {}).get("ok"):
        notes.append("No audio was transcribed, so speech categories were not measured.")
    judge = checks.get("llm_judge", {})
    if judge and not judge.get("ok"):
        notes.append("The LLM judge did not run; mistranslation is only detectable by the judge.")
    elif judge.get("skipped"):
        notes.append(f"The LLM judge completed {judge['ok']} items and skipped {judge['skipped']}; inspect skip reasons before interpreting judge coverage.")
    return notes


def render_markdown(summary: dict, results: list[ItemResult], calibration: dict | None, diff: dict | None) -> str:
    items = summary["items"]
    lines = ["# Quality report", ""]
    cfg = summary["config"]
    lines += [
        f"Dataset `{cfg.get('dataset')}`, languages {', '.join(cfg.get('langs') or []) or 'all'}, "
        f"judge `{cfg.get('llm_model') or 'off'}` ({cfg.get('judge_policy')}).",
        "",
    ]
    lines += [
        "## Headline",
        "",
        "| Items scored | Flagged | Clean | Failed | Skipped | Rows rejected |",
        "|---|---|---|---|---|---|",
        f"| {items['scored']} | {items['flagged']} | {items['clean']} | {items['failed']} | {items['skipped']} | {items['rows_rejected']} |",
        "",
    ]

    lines += ["## Quality targets", ""]
    qt = summary["quality_targets"]
    if qt.get("measured_on"):
        lines += [f"Measured on {qt['measured_on']}.", "", "| Check | Target | Value | Met |", "|---|---|---|---|"]
        names = {
            "detection_recall": "Detection recall",
            "false_alarm_rate": "False alarms (clean items flagged)",
            "localization": "Localization (span overlap)",
            "category_accuracy": "Category accuracy",
            "judge_consistency": "Judge consistency",
        }
        for key, label in names.items():
            t = qt[key]
            sign = "<=" if key == "false_alarm_rate" else ">="
            met = {True: "yes", False: "**no**", None: "not measured"}[t["met"]]
            lines.append(f"| {label} | {sign} {t['target']:.0%} | {_pct(t['value'])} | {met} |")
        lines.append("| Determinism | cached rerun identical at INR 0 | see `run.json` (`spent_this_run_inr`) | |")
        reasons = _miss_reasons(summary, calibration)
        if reasons:
            lines += ["", "Why targets are missed:", ""] + [f"- {r}" for r in reasons]
    notes = _coverage_notes(summary)
    if notes:
        lines += ["", "Coverage notes:", ""] + [f"- {n}" for n in notes]
    if not qt.get("measured_on"):
        lines += ["No labelled items, so detection metrics were not measured. Pass `--calibration labels.jsonl` or include `labels` in the dataset."]
    lines.append("")

    lines += ["## Per language", "", "| Lang | Scored | Flagged | Flag rate | MQM quality | Top categories | Speech checked |", "|---|---|---|---|---|---|---|"]
    for lang, v in summary["per_language"].items():
        top = ", ".join(f"{k} {n}" for k, n in sorted(v["flags_by_category"].items(), key=lambda x: -x[1])[:3]) or "-"
        lines.append(f"| {lang} | {v['scored']} | {v['flagged_items']} | {_pct(v['flag_rate'])} | {v['mqm_quality_mean']} | {top} | {v['speech_checked']} |")
    lines.append("")

    lines += ["## Per category", "", "| Category | Area | Flags | Items | Critical | Major | Minor |", "|---|---|---|---|---|---|---|"]
    for cat, v in summary["per_category"].items():
        sev = v["by_severity"]
        lines.append(f"| {cat} | {v['area']} | {v['flags']} | {v['items']} | {sev.get('critical', 0)} | {sev.get('major', 0)} | {sev.get('minor', 0)} |")
    lines.append("")

    src = (calibration or {}).get("dataset") or (calibration or {}).get("calibration_set")
    if src:
        lines += ["## Detection quality per category", "", "| Category | Labels | Flags | Precision | Recall | F1 |", "|---|---|---|---|---|---|"]
        for cat, v in src["per_category"].items():
            lines.append(
                f"| {cat} | {v['labels']} | {v['flags']} | {_pct(v['precision'])} | {_pct(v['recall'])} | {v['f1'] if v['f1'] is not None else 'n/a'} |"
            )
        lines.append("")

    lines += ["## Top failures", ""]
    top = _top_failures(results)
    if not top:
        lines.append("No flags.")
    for r, f in top:
        where = f"translation chars {list(f.span)}" if f.span else (f"source chars {list(f.source_span)}" if f.source_span else "whole item")
        if f.audio_span:
            where += f", audio {f.audio_span[0]:.1f}-{f.audio_span[1]:.1f}s"
        lines.append(f"- **{r.id}** ({r.lang}) `{f.category.value}` {f.severity.value}, confidence {f.confidence:.2f}, {where}")
        if f.source_excerpt:
            lines.append(f'  - source: "{_md_escape(f.source_excerpt)}"')
        if f.excerpt:
            lines.append(f'  - translation: "{_md_escape(f.excerpt)}"')
        for ev in f.evidence[:3]:
            lines.append(f"  - {_md_escape(ev)}")
    lines.append("")

    if diff:
        h = diff["headline"]
        lines += [
            "## Regressions against baseline",
            "",
            f"{h['regressed']} items regressed, {h['improved']} improved, {h['unchanged']} unchanged. Details in `diff.md`.",
            "",
        ]
        for row in diff["regressions"][:5]:
            lines.append(
                f"- **{row['id']}** ({row['lang']}): new {', '.join(row['new_categories']) or 'flags'}; penalty {row['baseline_penalty']} -> {row['penalty']}"
            )
        lines.append("")

    bad = summary["bad_rows"]
    failed = [r for r in results if r.status in {"failed", "skipped"}]
    if bad or failed:
        lines += ["## Bad or partial items", ""]
        for b in bad[:20]:
            lines.append(f"- line {b['line']} id `{b['id']}`: {b['error']}")
        for r in failed[:20]:
            lines.append(f"- `{r.id}` ({r.lang}) {r.status}: {r.error}")
        lines.append("")

    cost = summary["cost"]
    lines += [
        "## Cost",
        "",
        "Cost is what the model outputs used by this report cost to produce (cold cache). A cached rerun spends INR 0; see `run.json`.",
        "",
        "| Model | INR |",
        "|---|---|",
    ]
    for model, inr in cost["attributed_inr"].items():
        lines.append(f"| {model} | {inr:.4f} |")
    lines.append(f"| **total** | {cost['total_inr']:.4f} |")
    proj = cost.get("per_100_items_inr") or {}
    if proj:
        lines += ["", f"Per 100 items: text checks INR {proj.get('text_checks', 0):.3f}; speech synthesis + transcription INR {proj.get('speech', 0):.3f}."]
    lines += [
        "",
        "Files: `items.jsonl` (every check and flag with evidence), `calibration.json`, `dashboard.html`, `review_queue.csv`"
        + (", `diff.md`" if diff else "")
        + ".",
    ]
    return "\n".join(lines) + "\n"
