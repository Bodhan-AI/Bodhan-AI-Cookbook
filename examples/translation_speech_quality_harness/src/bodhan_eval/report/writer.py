"""Write the report folder. Everything here is deterministic: sorted, rounded, no timestamps.
Run-specific facts (actual spend, cache hits, wall time) go to run_meta.json, outside the report proper.
"""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from ..redact import redact
from ..taxonomy import CATEGORIES, MQM_WEIGHT, SEVERITIES, SEVERITY_RANK
from .dashboard import write_dashboard
from .diff import diff_reports, render_diff

TARGETS = [
    ("detection_recall", ">=", 0.80, "Detection recall"),
    ("false_alarm_rate", "<=", 0.10, "False alarms on clean items"),
    ("localization", ">=", 0.70, "Localization"),
    ("category_accuracy", ">=", 0.70, "Category accuracy"),
]


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def _excerpt(text: str | None, span) -> str | None:
    if not text or not span:
        return None
    a, b = span
    return text[max(0, a):min(len(text), b)][:200]


def serialize_items(result: dict, dataset_items: dict) -> list[dict]:
    rows = []
    for r in sorted(result["items"], key=lambda r: r["id"]):
        it = dataset_items[r["id"]]
        tr = r["translation"] if r["translation"] is not None else (it.translation or "")
        flags = []
        for f in r["flags"]:
            flags.append({**f, "text": _excerpt(tr, f["span"]), "source_text": _excerpt(it.source, f["source_span"])})
        rows.append({
            "id": r["id"], "lang": r["lang"], "status": r["status"], "flags": flags, "checks_run": sorted(set(r["checks_run"])),
            "uncertain_categories": sorted(set(r["uncertain"])), "metrics": r["metrics"], "notes": [redact(n) for n in r["notes"]],
            "source": it.source, "translation": tr, "back_translation": r["back_translation"], "asr_text": r["asr_text"],
            "generated": r["generated"],
            "labels": None if it.labels is None else [lb.model_dump(exclude_none=True) for lb in it.labels],
        })
    return rows


def summarize(rows: list[dict], result: dict) -> dict:
    per_lang: dict[str, dict] = defaultdict(lambda: {"items": 0, "checked": 0, "flagged": 0, "skipped": 0, "errors": 0,
                                                       "by_category": Counter(), "by_severity": Counter(), "penalty": 0.0, "words": 0})
    per_cat: dict[str, dict] = {c: {"flags": 0, "items": 0, "by_severity": Counter(), "by_language": Counter()} for c in CATEGORIES}
    for r in rows:
        pl = per_lang[r["lang"]]
        pl["items"] += 1
        if r["status"] in ("skipped", "error"):
            pl["skipped" if r["status"] == "skipped" else "errors"] += 1
            continue
        pl["checked"] += 1
        pl["words"] += len(r["source"].split())
        if r["flags"]:
            pl["flagged"] += 1
        seen = set()
        for f in r["flags"]:
            pl["by_category"][f["category"]] += 1
            pl["by_severity"][f["severity"]] += 1
            pl["penalty"] += MQM_WEIGHT[f["severity"]]
            pc = per_cat.setdefault(f["category"], {"flags": 0, "items": 0, "by_severity": Counter(), "by_language": Counter()})
            pc["flags"] += 1
            pc["by_severity"][f["severity"]] += 1
            pc["by_language"][r["lang"]] += 1
            if f["category"] not in seen:
                pc["items"] += 1
                seen.add(f["category"])
    langs = {}
    for lang, v in sorted(per_lang.items()):
        mqm = max(0.0, 100.0 - 100.0 * v["penalty"] / max(v["words"], 1)) if v["checked"] else None
        langs[lang] = {"items": v["items"], "checked": v["checked"], "flagged": v["flagged"], "skipped": v["skipped"], "errors": v["errors"],
                       "flag_rate": round(v["flagged"] / v["checked"], 4) if v["checked"] else None,
                       "by_category": dict(sorted(v["by_category"].items())), "by_severity": dict(sorted(v["by_severity"].items())),
                       "mqm_score": None if mqm is None else round(mqm, 2), "source_words": v["words"]}
    cats = {c: {"flags": v["flags"], "items": v["items"], "by_severity": dict(sorted(v["by_severity"].items())),
                "by_language": dict(sorted(v["by_language"].items()))} for c, v in per_cat.items() if v["flags"]}
    status = Counter(r["status"] for r in rows)
    n_checked = sum(1 for r in rows if r["status"] not in ("skipped", "error"))
    cost = result["cost"]
    per100 = round(cost["attributed_total_inr"] * 100 / n_checked, 4) if n_checked else None
    speech_n = sum(1 for r in rows if "asr_alignment" in r["checks_run"])
    calib = result["calibration"]
    return {
        "items_total": len(rows) + len(result["problems"]),
        "items_scored": n_checked,
        "status_counts": dict(sorted(status.items())),
        "malformed_lines": len(result["problems"]),
        "speech_checked_items": speech_n,
        "per_language": langs,
        "per_category": cats,
        "cost": {**cost, "per_100_items_inr": per100},
        "llm_model": result["llm_model"],
        "thresholds": result["cfg"]["thresholds"],
        "quality_vs_labels": calib.get("after_tuning_in_sample"),
        "quality_vs_labels_cross_validated": calib.get("cross_validated"),
        "judge_consistency": calib.get("judge_consistency"),
    }


def _fmt(x, pct=True):
    if x is None:
        return "n/a"
    return f"{x:.0%}" if pct else f"{x}"


def _top_failures(rows, n=12):
    flat = []
    for r in rows:
        for f in r["flags"]:
            flat.append((SEVERITY_RANK[f["severity"]], f["confidence"], r, f))
    flat.sort(key=lambda t: (-t[0], -t[1], t[2]["id"], t[3]["category"]))
    return flat[:n]


def render_summary(summary: dict, rows: list[dict], result: dict, diff: dict | None) -> str:
    L = []
    s = summary
    L.append("# Translation & speech quality report\n")
    langs = ", ".join(s["per_language"])
    L.append(f"**{s['items_scored']} items scored** across {len(s['per_language'])} languages ({langs}); "
             f"{s['status_counts'].get('flagged', 0)} flagged, {s['status_counts'].get('clean', 0)} clean, "
             f"{s['status_counts'].get('skipped', 0)} skipped (no translation), {s['status_counts'].get('error', 0)} errored, "
             f"{s['malformed_lines']} malformed lines. Speech checked on {s['speech_checked_items']} items. "
             f"Judge: `{s['llm_model'] or 'disabled'}`.\n")
    L.append(f"Model cost of the outputs used: **₹{s['cost']['attributed_total_inr']:.2f}** "
             f"(₹{s['cost']['per_100_items_inr'] or 0:.2f} per 100 items; actual spend this run is in run_meta.json).\n")
    # targets
    q, cv = s.get("quality_vs_labels"), s.get("quality_vs_labels_cross_validated")
    if q:
        L.append("## Quality vs labels\n")
        L.append(f"Measured on {q['n_labelled_items']} labelled items ({q['n_errors']} seeded/labelled errors, {q['n_clean']} verified-clean items).\n")
        L.append("| Target | Goal | Tuned (in-sample) | Cross-validated | Met? |")
        L.append("|---|---|---|---|---|")
        misses = []
        for key, op, goal, name in TARGETS:
            v = q.get(key)
            c = cv.get(key) if cv else None
            ref = c if c is not None else v
            ok = None if ref is None else (ref >= goal if op == ">=" else ref <= goal)
            L.append(f"| {name} | {op} {goal:.0%} | {_fmt(v)} | {_fmt(c)} | {'✅' if ok else ('❌' if ok is False else 'n/a')} |")
            if ok is False:
                misses.append(name)
        jc = s.get("judge_consistency")
        if jc and "same_verdict" in jc:
            ok = jc["same_verdict"] >= 0.9
            L.append(f"| Judge consistency (same verdict on re-judge) | >= 90% | {_fmt(jc['same_verdict'])} | ({jc['items']} items) | {'✅' if ok else '❌'} |")
        L.append("")
        pc = q.get("per_category", {})
        if pc:
            L.append("Per category (tuned thresholds):\n")
            L.append("| Category | Precision | Recall | Detected (any flag) | Threshold |")
            L.append("|---|---|---|---|---|")
            for c, d in pc.items():
                L.append(f"| {c} | {_fmt(d['precision'])} | {_fmt(d['recall'])} | {_fmt(d.get('detection_recall'))} | {s['thresholds'].get(c)} |")
            L.append("")
        if misses:
            L.append(f"**Missed targets:** {', '.join(misses)}. See calibration.json (`per_category`, `per_check`) and the README's limitations.\n")
    # per language
    L.append("## Per language\n")
    L.append("| Lang | Scored | Flagged | Critical | Major | Minor | MQM-style score | Top categories |")
    L.append("|---|---|---|---|---|---|---|---|")
    for lang, v in s["per_language"].items():
        top = ", ".join(f"{k} {n}" for k, n in sorted(v["by_category"].items(), key=lambda t: -t[1])[:3]) or "—"
        sev = v["by_severity"]
        L.append(f"| {lang} | {v['checked']} | {v['flagged']} ({_fmt(v['flag_rate'])}) | {sev.get('critical', 0)} | {sev.get('major', 0)} | "
                 f"{sev.get('minor', 0)} | {v['mqm_score'] if v['mqm_score'] is not None else 'n/a'} | {top} |")
    L.append("\nMQM-style score = 100 − 100 × (1·minor + 5·major + 10·critical) / source words.\n")
    # per category
    if s["per_category"]:
        L.append("## Per category\n")
        L.append("| Category | Flags | Items | By language | By severity |")
        L.append("|---|---|---|---|---|")
        for c, v in sorted(s["per_category"].items(), key=lambda t: -t[1]["flags"]):
            L.append(f"| {c} | {v['flags']} | {v['items']} | {', '.join(f'{k} {n}' for k, n in v['by_language'].items())} | "
                     f"{', '.join(f'{k} {n}' for k, n in v['by_severity'].items())} |")
        L.append("")
    # top failures
    tops = _top_failures(rows)
    if tops:
        L.append("## Top failures\n")
        for i, (_, _, r, f) in enumerate(tops, 1):
            where = f"chars {f['span'][0]}–{f['span'][1]}" if f["span"] else "location: see source span"
            L.append(f"{i}. **{r['lang']} · {f['category']} ({f['severity']})** · `{r['id']}` · {where} · confidence {f['confidence']:.2f}  ")
            L.append(f"   {f['message']}  ")
            if f.get("text"):
                L.append(f"   Flagged text: “{f['text']}”  ")
            if f.get("source_text"):
                L.append(f"   Source: “{f['source_text']}”  ")
            L.append(f"   Evidence from: {', '.join(f['sources'])}")
        L.append("")
    if diff:
        L.append("## Regressions vs baseline\n")
        L.append(f"{diff['counts']['new_flags']} new flags ({diff['counts']['new_major_or_worse']} major or worse), "
                 f"{diff['counts']['resolved_flags']} resolved; {diff['counts']['newly_flagged_items']} items newly flagged, "
                 f"{diff['counts']['newly_clean_items']} newly clean. Details in diff.md.\n")
    probs = result["problems"]
    skipped = [r for r in rows if r["status"] in ("skipped", "error")]
    if probs or skipped:
        L.append("## Skipped and failed items\n")
        for p in probs[:20]:
            L.append(f"- line {p['line']} (`{p['id']}`): {p['reason']}")
        for r in skipped[:20]:
            L.append(f"- `{r['id']}`: {r['status']} — {'; '.join(r['notes'])[:200]}")
        L.append("")
    L.append("## How to read this\n")
    L.append("Every flag in items.jsonl has a category, severity, span (character offsets into the translation; speech errors point at the "
             "words that were not spoken correctly), the flagged text, and the evidence from each check that contributed. "
             "`sources` lists the checks that agreed. Back-translation and ASR are noisy, so neither can raise a flag on its own.\n")
    return "\n".join(L)


def write_review_queue(rows: list[dict], path: Path, limit: int) -> int:
    """Uncertain items first (near a threshold), then flags supported only by the LLM."""
    cand = []
    for r in rows:
        for c in r["uncertain_categories"]:
            cand.append((0, r, c, next((f for f in r["flags"] if f["category"] == c), None)))
        for f in r["flags"]:
            if set(f["sources"]) <= {"judge", "verifier"} and f["category"] not in r["uncertain_categories"]:
                cand.append((1, r, f["category"], f))
    cand.sort(key=lambda t: (t[0], t[1]["id"], t[2]))
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "lang", "category", "flagged_by_harness", "severity", "confidence", "flagged_text", "source_text", "message",
                    "is_real_error (yes/no)", "correct_category", "annotator"])
        for _, r, cat, f in cand[:limit]:
            w.writerow([r["id"], r["lang"], cat, "yes" if f else "no", f["severity"] if f else "", f["confidence"] if f else "",
                        (f or {}).get("text") or "", (f or {}).get("source_text") or "", (f or {}).get("message") or "", "", "", ""])
    return min(len(cand), limit)


def write_report(out: Path, result: dict, dataset_items: dict, baseline: Path | None = None, review_limit: int = 40) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    rows = serialize_items(result, dataset_items)
    with open(out / "items.jsonl", "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(_dump(r) + "\n")
    diff = None
    if baseline:
        base_items = Path(baseline) / "items.jsonl" if Path(baseline).is_dir() else Path(baseline)
        if base_items.exists():
            diff = diff_reports(base_items, rows)
            (out / "diff.md").write_text(render_diff(diff), encoding="utf-8", newline="\n")
            (out / "diff.json").write_text(json.dumps(diff, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8", newline="\n")
        else:
            (out / "diff.md").write_text(f"# Diff\n\nBaseline not found at `{baseline}`.\n", encoding="utf-8", newline="\n")
    summary = summarize(rows, result)
    if diff:
        summary["regressions"] = diff["counts"]
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8", newline="\n")
    (out / "summary.md").write_text(render_summary(summary, rows, result, diff), encoding="utf-8", newline="\n")
    (out / "calibration.json").write_text(json.dumps(result["calibration"], ensure_ascii=False, indent=2, sort_keys=True, default=str),
                                          encoding="utf-8", newline="\n")
    (out / "thresholds_tuned.json").write_text(json.dumps(result["cfg"], ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8", newline="\n")
    write_review_queue(rows, out / "review_queue.csv", review_limit)
    write_dashboard(rows, summary, out / "dashboard.html")
    (out / "run_meta.json").write_text(json.dumps(result["run"], ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8", newline="\n")
    return {"summary": summary, "diff": diff}


__all__ = ["write_report", "SEVERITIES"]
