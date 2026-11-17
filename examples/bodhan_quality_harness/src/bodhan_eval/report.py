"""Report writers.

Five outputs, exactly as the brief specifies. Determinism is made checkable
rather than asserted: a digest is computed over everything except timings, so a
cached rerun can prove it produced the same result at zero model cost.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path

from .checks.base import Flag, ItemContext, SEVERITY_WEIGHT


def deterministic_digest(items_payload: list[dict], metrics: dict) -> str:
    blob = json.dumps({"items": items_payload, "metrics": metrics},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def mqm_score(flags: list[Flag], words: int) -> float:
    """Severity-weighted errors per 100 words, inverted to a 0-100 score."""
    if words <= 0:
        return 100.0
    penalty = sum(SEVERITY_WEIGHT.get(f.severity, 1) for f in flags)
    return round(max(0.0, 100.0 - (penalty / words) * 100.0), 2)


def write_reports(
    out: Path,
    *,
    items: list[ItemContext],
    predictions: dict[str, list[Flag]],
    checks_run: dict[str, list[str]],
    metrics: dict,
    calibration: dict | None,
    cost: dict,
    runtime_s: float,
    cache_stats: dict,
    skipped: list[dict],
    judge_stats: dict | None = None,
) -> dict:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)

    # ---- items.jsonl ----
    items_payload = []
    for it in items:
        flags = predictions.get(it.id, [])
        items_payload.append({
            "id": it.id,
            "lang": it.lang,
            "checks_run": checks_run.get(it.id, []),
            "words": len((it.translation or "").split()),
            "flag_count": len(flags),
            "mqm": mqm_score(flags, len((it.translation or "").split())),
            "flags": [f.to_dict() for f in flags],
            "labels": it.labels,
        })
    (out / "items.jsonl").write_text(
        "\n".join(json.dumps(p, ensure_ascii=False) for p in items_payload) + "\n",
        encoding="utf-8",
    )

    # ---- per-language and per-category rollups ----
    per_lang: dict[str, dict] = defaultdict(lambda: {"items": 0, "flags": 0,
                                                     "by_category": defaultdict(int),
                                                     "by_severity": defaultdict(int)})
    for it in items:
        row = per_lang[it.lang]
        row["items"] += 1
        for f in predictions.get(it.id, []):
            row["flags"] += 1
            row["by_category"][f.category] += 1
            row["by_severity"][f.severity] += 1

    per_lang_out = {
        lang: {
            "items": r["items"],
            "flags": r["flags"],
            "flags_per_item": round(r["flags"] / r["items"], 2) if r["items"] else 0.0,
            "by_category": dict(sorted(r["by_category"].items())),
            "by_severity": dict(sorted(r["by_severity"].items())),
        }
        for lang, r in sorted(per_lang.items())
    }

    digest = deterministic_digest(items_payload, metrics)
    summary = {
        "digest": digest,
        "items_scored": len(items),
        "items_skipped": len(skipped),
        "languages": sorted({it.lang for it in items}),
        "totals": {
            "flags": sum(len(v) for v in predictions.values()),
            "items_with_flags": sum(1 for v in predictions.values() if v),
        },
        "per_language": per_lang_out,
        "accuracy": metrics,
        "calibration": calibration,
        "judge": judge_stats,
        "cost": cost,
        "cache": cache_stats,
        "runtime_seconds": round(runtime_s, 1),
    }
    (out / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    if calibration is not None:
        (out / "calibration.json").write_text(
            json.dumps(calibration, indent=2, ensure_ascii=False), encoding="utf-8")

    (out / "summary.md").write_text(
        _summary_md(summary, items, predictions, skipped), encoding="utf-8")
    return summary


def _summary_md(summary: dict, items, predictions, skipped) -> str:
    acc = summary.get("accuracy") or {}
    overall = acc.get("overall", {})
    per_cat = acc.get("per_category", {})
    L = []
    L.append("# Translation and speech quality report\n")
    L.append(f"**{summary['items_scored']} items** across "
             f"{', '.join(summary['languages']) or 'no languages'}"
             + (f" · {summary['items_skipped']} skipped" if summary["items_skipped"] else "")
             + f" · **{summary['totals']['flags']} flags** "
             f"on {summary['totals']['items_with_flags']} items\n")

    if overall:
        L.append("## Headline\n")
        L.append("| Metric | Value | Target |")
        L.append("| --- | --- | --- |")
        L.append(f"| Detection recall | {overall.get('recall', 0):.1%} | ≥ 80% |")
        L.append(f"| False alarms on clean items | {overall.get('false_alarm_rate', 0):.1%} | ≤ 10% |")
        L.append(f"| Precision | {overall.get('precision', 0):.1%} | — |")
        L.append(f"| F1 | {overall.get('f1', 0):.3f} | — |")
        L.append("")

    if per_cat:
        L.append("## Per category\n")
        L.append("| Category | TP | FP | FN | Precision | Recall | Localization | Category acc. |")
        L.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for cat, m in per_cat.items():
            loc = f"{m['localization']:.0%}" if m["localization"] is not None else "—"
            ca = f"{m['category_accuracy']:.0%}" if m["category_accuracy"] is not None else "—"
            L.append(f"| {cat} | {m['tp']} | {m['fp']} | {m['fn']} | "
                     f"{m['precision']:.0%} | {m['recall']:.0%} | {loc} | {ca} |")
        L.append("")

    L.append("## Per language\n")
    L.append("| Language | Items | Flags | Flags/item | Top categories |")
    L.append("| --- | --- | --- | --- | --- |")
    for lang, m in summary["per_language"].items():
        top = sorted(m["by_category"].items(), key=lambda kv: -kv[1])[:3]
        top_s = ", ".join(f"{k} ({v})" for k, v in top) or "—"
        L.append(f"| {lang} | {m['items']} | {m['flags']} | {m['flags_per_item']} | {top_s} |")
    L.append("")

    # Top failures: most severe flags, with their evidence.
    worst = []
    for it in items:
        for f in predictions.get(it.id, []):
            worst.append((SEVERITY_WEIGHT.get(f.severity, 1), it.id, f))
    worst.sort(key=lambda t: -t[0])
    if worst:
        L.append("## Top failures\n")
        for _, item_id, f in worst[:10]:
            span = f"{f.span[0]}–{f.span[1]}" if f.span else "no span"
            L.append(f"- **{item_id}** · `{f.category}`/{f.severity} · {span} — {f.evidence}")
        L.append("")

    cost = summary.get("cost", {})
    L.append("## Cost and runtime\n")
    L.append(f"- Total: **₹{cost.get('total_inr', 0):.2f}** over {summary['runtime_seconds']:.0f}s")
    for model, amt in (cost.get("by_model_inr") or {}).items():
        L.append(f"- {model}: ₹{amt:.2f} ({cost.get('calls', {}).get(model, 0)} calls)")
    cache = summary.get("cache", {})
    if cache:
        L.append(f"- Cache: {cache.get('hits', 0)} hits / {cache.get('misses', 0)} misses "
                 f"({cache.get('hit_rate', 0):.0%} hit rate)")
    L.append("")

    if skipped:
        L.append("## Skipped items\n")
        for s in skipped[:10]:
            L.append(f"- line {s.get('line', '?')}: {s.get('reason', '')}")
        L.append("")

    L.append("---\n")
    L.append(f"Deterministic digest: `{summary['digest']}` — a cached rerun must reproduce this "
             f"exactly at zero model cost.\n")
    return "\n".join(L)


def write_diff(out: Path, current: dict, baseline_dir: Path) -> str:
    """What improved and what regressed, with examples."""
    base_path = Path(baseline_dir) / "summary.json"
    if not base_path.exists():
        return ""
    base = json.loads(base_path.read_text(encoding="utf-8"))

    cur_items = _load_items(Path(out) / "items.jsonl")
    base_items = _load_items(Path(baseline_dir) / "items.jsonl")

    L = ["# Diff against baseline\n"]
    a, b = base.get("accuracy", {}).get("overall", {}), current.get("accuracy", {}).get("overall", {})
    if a and b:
        L.append("## Headline change\n")
        L.append("| Metric | Baseline | Current | Change |")
        L.append("| --- | --- | --- | --- |")
        for key, label, better in (("recall", "Recall", "up"),
                                   ("precision", "Precision", "up"),
                                   ("false_alarm_rate", "False alarms", "down")):
            av, bv = a.get(key, 0), b.get(key, 0)
            delta = bv - av
            arrow = "→"
            if delta > 0:
                arrow = "✅" if better == "up" else "⚠️"
            elif delta < 0:
                arrow = "⚠️" if better == "up" else "✅"
            L.append(f"| {label} | {av:.1%} | {bv:.1%} | {arrow} {delta:+.1%} |")
        L.append("")

    fixed = [i for i in cur_items.values() if i["flag_count"] == 0
             and base_items.get(i["id"], {}).get("flag_count", 0) > 0]
    regressed = [i for i in cur_items.values() if i["flag_count"] > 0
                 and base_items.get(i["id"], {}).get("flag_count", 0) == 0]

    L.append(f"## Fixed ({len(fixed)})\n")
    for i in fixed[:10]:
        L.append(f"- **{i['id']}** — was {base_items[i['id']]['flag_count']} flags, now clean")
    L.append(f"\n## Regressed ({len(regressed)})\n")
    for i in regressed[:10]:
        cats = ", ".join(f["category"] for f in i["flags"][:3])
        L.append(f"- **{i['id']}** — new flags: {cats}")
    L.append("")
    return "\n".join(L)


def _load_items(path: Path) -> dict[str, dict]:
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            obj = json.loads(line)
            out[obj["id"]] = obj
    return out
