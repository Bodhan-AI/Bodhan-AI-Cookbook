"""diff: Baseline diff (``--baseline DIR``): this run vs a previous report, matched by item id.
report: Report writer. Deterministic: sorted keys and items, fixed float formatting, no
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .calibration import PREEXISTING, evaluate, label_result
from .config import MQM_WEIGHTS
from .dataset import Item, Label
from .infra import mcnemar_exact

# -------------------- diff
ALPHA = 0.05


MAX_EXAMPLES = 5


MAX_IDS = 20


def load_items(report_dir: Path) -> dict[str, dict] | None:
    path = Path(report_dir) / "items.jsonl"
    if not path.exists():
        return None
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            out[rec["id"]] = rec
    return out


def counted(rec: dict) -> list[dict]:
    return [f for f in rec.get("flags", []) if not f.get("needs_corroboration")]


def _cats(rec: dict) -> list[str]:
    return sorted({f["category"] for f in counted(rec)})


def significance(regressed: int, improved: int) -> dict:
    p = mcnemar_exact(regressed, improved)
    return {"regressed": regressed, "improved": improved, "p_value": p, "label": "significant" if p < ALPHA else "within noise"}


def _example(rec_before: dict, rec_after: dict, labels: list[Label] | None) -> dict:
    ex = {"id": rec_after["id"], "lang": rec_after["lang"], "before": _cats(rec_before), "after": _cats(rec_after)}
    if labels is not None:
        ex["labels"] = sorted({lb.category for lb in labels})
    first = (counted(rec_after) or counted(rec_before) or [{}])[0]
    if first:
        ex["evidence"] = f"{first['check_id']} ({first['category']}): {first['message'][:160]}"
    return ex


def build_diff(base: dict[str, dict], cur_items: list[dict], labels: dict[str, list[Label]]) -> dict:
    cur = {r["id"]: r for r in cur_items}
    shared = sorted(set(cur) & set(base))
    groups = {k: [] for k in ("improved", "regressed", "categories_changed", "newly_flagged", "no_longer_flagged", "errored")}
    flag_before: dict[str, int] = {}
    flag_after: dict[str, int] = {}
    label_cat: dict[str, dict] = {}
    totals = {"flagged_before": 0, "flagged_after": 0, "labelled": 0, "right_before": 0, "right_after": 0}
    for i in shared:
        b, c = base[i], cur[i]
        if b.get("status") == "error" or c.get("status") == "error":
            groups["errored"].append({"id": i, "lang": c["lang"]})
            continue
        fb, fc = bool(counted(b)), bool(counted(c))
        totals["flagged_before"] += fb
        totals["flagged_after"] += fc
        for f in counted(b):
            flag_before[f["category"]] = flag_before.get(f["category"], 0) + 1
        for f in counted(c):
            flag_after[f["category"]] = flag_after.get(f["category"], 0) + 1
        lbs = labels.get(i)
        ex = _example(b, c, lbs)
        if lbs is not None:
            truth = bool(lbs)
            rb, rc = fb == truth, fc == truth
            totals["labelled"] += 1
            totals["right_before"] += rb
            totals["right_after"] += rc
            if rb and not rc:
                groups["regressed"].append(ex)
            elif rc and not rb:
                groups["improved"].append(ex)
            elif _cats(b) != _cats(c):
                groups["categories_changed"].append(ex)
            for lb in lbs:
                hb, hc = label_result(lb, counted(b))["hit"], label_result(lb, counted(c))["hit"]
                s = label_cat.setdefault(lb.category, {"labels": 0, "hits_before": 0, "hits_after": 0, "lost": 0, "gained": 0})
                s["labels"] += 1
                s["hits_before"] += hb
                s["hits_after"] += hc
                s["lost"] += hb and not hc
                s["gained"] += hc and not hb
        elif fc and not fb:
            groups["newly_flagged"].append(ex)
        elif fb and not fc:
            groups["no_longer_flagged"].append(ex)
        elif _cats(b) != _cats(c):
            groups["categories_changed"].append(ex)
    cats = sorted(set(flag_before) | set(flag_after))
    return {
        "items": {"current": len(cur), "baseline": len(base), "shared": len(shared), "new": len(set(cur) - set(base)), "removed": len(set(base) - set(cur))},
        "new_ids": sorted(set(cur) - set(base))[:MAX_IDS],
        "removed_ids": sorted(set(base) - set(cur))[:MAX_IDS],
        "totals": totals,
        "overall": significance(len(groups["regressed"]), len(groups["improved"])),
        "counts": {k: len(v) for k, v in groups.items()},
        "examples": {k: v[:MAX_EXAMPLES] for k, v in groups.items() if k != "errored"},
        "flag_counts_by_category": {
            c: {"before": flag_before.get(c, 0), "after": flag_after.get(c, 0), "delta": flag_after.get(c, 0) - flag_before.get(c, 0)} for c in cats
        },
        "label_hits_by_category": {c: s | significance(s["lost"], s["gained"]) for c, s in sorted(label_cat.items())},
        "significant_regression": len(groups["regressed"]) > len(groups["improved"])
        and mcnemar_exact(len(groups["regressed"]), len(groups["improved"])) < ALPHA,
    }


def _ex_line(ex: dict) -> str:
    lab = f"; labels: {', '.join(ex['labels']) or 'clean'}" if "labels" in ex else ""
    ev = f"; {ex['evidence']}" if ex.get("evidence") else ""
    return f"- `{ex['id']}` ({ex['lang']}): flags {', '.join(ex['before']) or 'none'} -> {', '.join(ex['after']) or 'none'}{lab}{ev}"


def render_diff_md(d: dict, baseline_name: str) -> str:
    it, t, o = d["items"], d["totals"], d["overall"]
    lines = [
        "# Baseline diff",
        "",
        f"Baseline: `{baseline_name}`. Items are matched by `id`; all comparisons use the {it['shared']} shared ids.",
        "",
        f"- Items: current {it['current']}, baseline {it['baseline']}, shared {it['shared']}, new {it['new']}, removed {it['removed']}",
        f"- Items with a counted flag (shared): {t['flagged_before']} -> {t['flagged_after']}",
    ]
    if t["labelled"]:
        lines.append(f"- Labelled shared items judged right (flagged == has error): {t['right_before']} -> {t['right_after']} of {t['labelled']}")
    lines += [
        f"- Improved {o['improved']}, regressed {o['regressed']}: exact McNemar p = {o['p_value']:.4f} -> **{o['label']}**",
        f"- Unlabelled items: newly flagged {d['counts']['newly_flagged']}, no longer flagged {d['counts']['no_longer_flagged']}; "
        f"category set changed {d['counts']['categories_changed']}; errored in either run {d['counts']['errored']}",
        "",
    ]
    if d["new_ids"] or d["removed_ids"]:
        lines += [
            f"New ids (first {MAX_IDS}): {', '.join(d['new_ids']) or 'none'}",
            "",
            f"Removed ids (first {MAX_IDS}): {', '.join(d['removed_ids']) or 'none'}",
            "",
        ]
    lines += ["## Counted flags per category (shared items)", "", "| category | before | after | delta |", "|---|---:|---:|---:|"]
    lines += [f"| {c} | {v['before']} | {v['after']} | {v['delta']:+d} |" for c, v in d["flag_counts_by_category"].items()]
    if d["label_hits_by_category"]:
        lines += [
            "",
            "## Label hits per category (shared labelled items)",
            "",
            "| category | labels | hits before | hits after | gained | lost | McNemar p | verdict |",
            "|---|---:|---:|---:|---:|---:|---:|---|",
        ]
        lines += [
            f"| {c} | {s['labels']} | {s['hits_before']} | {s['hits_after']} | {s['gained']} | {s['lost']} | {s['p_value']:.4f} | {s['label']} |"
            for c, s in d["label_hits_by_category"].items()
        ]
    for key, title in (
        ("improved", "Improved"),
        ("regressed", "Regressed"),
        ("newly_flagged", "Newly flagged (unlabelled)"),
        ("no_longer_flagged", "No longer flagged (unlabelled)"),
        ("categories_changed", "Category set changed"),
    ):
        exs = d["examples"][key]
        if exs:
            lines += ["", f"## {title} (first {MAX_EXAMPLES} of {d['counts'][key]})", ""] + [_ex_line(e) for e in exs]
    return "\n".join(lines) + "\n"


# -------------------- report
TARGETS = [
    ("recall_items", "Detection recall (items)", 0.80, "min"),
    ("false_alarm_strict", "False alarms, strict", 0.10, "max"),
    ("false_alarm_reviewed", "False alarms, reviewed", 0.10, "max"),
    ("localization", "Localization (span overlap)", 0.70, "min"),
    ("category_accuracy", "Category accuracy", 0.70, "min"),
    ("judge_consistency", "Judge consistency (same verdict)", 0.90, "min"),
]


TOP_FAILURES = 10


MISSED_EXAMPLES = 8


EXCERPT = 60


def _round(obj: Any, nd: int = 4) -> Any:
    if isinstance(obj, float):
        return round(obj, nd)
    if isinstance(obj, dict):
        return {k: _round(v, nd) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_round(v, nd) for v in obj]
    return obj


def dump_json(obj: Any) -> str:
    return json.dumps(_round(obj), ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def write_report(out_dir: Path, *, items: list[dict], summary: dict, calibration: dict, run_meta: dict, diff_md: str | None = None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    items_sorted = sorted(items, key=lambda r: (r.get("lang", ""), r.get("id", "")))
    with (out_dir / "items.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
        for row in items_sorted:
            fh.write(json.dumps(_round(row), ensure_ascii=False, sort_keys=True) + "\n")
    write_text(out_dir / "summary.json", dump_json(summary))
    write_text(out_dir / "calibration.json", dump_json(calibration))
    write_text(out_dir / "summary.md", render_summary_md(_round(summary)))
    write_text(out_dir / "run_meta.json", dump_json(run_meta))
    if diff_md is not None:
        write_text(out_dir / "diff.md", diff_md)


def _excerpt(text: str | None, span) -> str | None:
    if not text:
        return None
    if not span:
        return text[: 2 * EXCERPT] + ("..." if len(text) > 2 * EXCERPT else "")
    s, e = int(span[0]), int(span[1])
    pre = ("..." if s > EXCERPT else "") + text[max(0, s - EXCERPT):s]
    post = text[e:e + EXCERPT] + ("..." if e + EXCERPT < len(text) else "")
    return f"{pre}[[{text[s:e]}]]{post}"


def _flag_view(f: dict, translation: str | None) -> dict:
    v = {"check_id": f["check_id"], "category": f["category"], "severity": f["severity"], "span": f["span"], "unit": f["unit"],
         "message": f["message"], "confidence": f["confidence"]}
    if f["unit"] == "chars":
        v["excerpt"] = _excerpt(translation, f["span"])
    corr = f.get("evidence", {}).get("corroborated_by")
    if corr:
        v["corroborated_by"] = corr
    return v


def _label_view(lb: Label) -> dict:
    return {"category": lb.category, "source": str(lb.extra.get("source") or "unspecified"), "span": list(lb.span) if lb.span else None,
            "frame": lb.frame, "note": str(lb.extra.get("note") or "")[:200]}


def _seeded(lbs: list[Label] | None) -> bool:
    return bool(lbs) and any(str(lb.extra.get("source")) != PREEXISTING for lb in lbs)


def _top_failures(results: list[dict], items: dict[str, Item], labels: dict[str, list[Label]]) -> list[dict]:
    rows = []
    for r in results:
        flags = counted(r)
        if not flags:
            continue
        worst = max(MQM_WEIGHTS[f["severity"]] for f in flags)
        rows.append((-worst, -sum(MQM_WEIGHTS[f["severity"]] for f in flags), r["lang"], r["id"], r, flags))
    out = []
    for _, neg_pen, _, _, r, flags in sorted(rows, key=lambda x: x[:4])[:TOP_FAILURES]:
        it = items[r["id"]]
        lbs = labels.get(r["id"])
        out.append({"id": r["id"], "lang": r["lang"], "mqm_penalty": -neg_pen, "source": it.source[:240],
                    "labels": [_label_view(lb) for lb in lbs or []],
                    "flags": [_flag_view(f, it.translation) for f in flags[:3]]})
    return out


def _missed(results: list[dict], items: dict[str, Item], labels: dict[str, list[Label]]) -> list[dict]:
    out = []
    for r in results:
        lbs = labels.get(r["id"])
        if not lbs or counted(r) or r["status"] == "error":
            continue
        weak = sorted({f"{f['check_id']}:{f['category']}" for f in r.get("flags", []) if f.get("needs_corroboration")})
        out.append({"id": r["id"], "lang": r["lang"], "labels": [_label_view(lb) for lb in lbs], "weak_signals": weak,
                    "judge": (r.get("judge") or {}).get("verdict") or (r.get("judge") or {}).get("status")})
    return out[:MISSED_EXAMPLES]


def _findings(results: list[dict], items: dict[str, Item], labels: dict[str, list[Label]]) -> dict:
    """Counted flags on outputs WITHOUT a seeded error: candidate genuine model defects."""
    groups: dict[str, dict] = {}
    for r in results:
        if _seeded(labels.get(r["id"])):
            continue
        for f in counted(r):
            key = f"{f['category']} / {f['check_id']}"
            g = groups.setdefault(key, {"items": [], "example": None})
            if r["id"] not in g["items"]:
                g["items"].append(r["id"])
            if g["example"] is None:
                g["example"] = {"id": r["id"], "lang": r["lang"], **_flag_view(f, items[r["id"]].translation)}
    reviewed: dict[str, list[str]] = {}
    for i, lbs in labels.items():
        for lb in lbs:
            if str(lb.extra.get("source")) == PREEXISTING:
                reviewed.setdefault(f"{lb.category}: {lb.extra.get('note') or 'no note'}", []).append(i)
    return {"flag_groups": {k: {"count": len(v["items"]), "ids": sorted(v["items"])[:10], "example": v["example"]} for k, v in sorted(groups.items())},
            "reviewed_preexisting": {k: sorted(v) for k, v in sorted(reviewed.items())}}


def _target_rows(headline: dict | None, basis: str, consistency: dict | None) -> list[dict]:
    rows = []
    for key, label, target, kind in TARGETS:
        if key == "judge_consistency":
            r = (consistency or {}).get("overall", {}).get("same_verdict")
            src = "consistency re-run" if consistency else "not run (use --judge-rerun N)"
        else:
            r = (headline or {}).get(key)
            src = basis
        value = r["value"] if r else None
        met = None if value is None else (value >= target if kind == "min" else value <= target)
        lo, hi = r["ci95"] if r else (0.0, 1.0)
        rows.append({"metric": key, "label": label, "target": target, "kind": kind, "value": value, "k": r["k"] if r else 0,
                     "n": r["n"] if r else 0, "ci95": [lo, hi], "met": met, "ci_spans_target": lo <= target <= hi, "basis": src})
    return rows


def _explain_misses(rows: list[dict], all_eval: dict | None, findings: dict, consistency: dict | None) -> list[str]:
    """Plain-language reasons for each missed target, computed from the data."""
    out = []
    cats = (all_eval or {}).get("by_category", {})

    def weakest(key: str) -> str:
        vals = [(v[key]["value"], c, v[key]["k"], v[key]["n"]) for c, v in cats.items() if v[key]["n"] and v[key]["value"] is not None]
        return ", ".join(f"{c} {k}/{n}" for _, c, k, n in sorted(vals)[:4]) or "n/a"

    for r in rows:
        if r["met"] is not False:
            continue
        m = r["metric"]
        if m == "recall_items":
            out.append(f"Recall: lowest per-category label recall (all labelled items): {weakest('recall')}.")
        elif m.startswith("false_alarm"):
            out.append(f"{r['label']}: flags on outputs without seeded errors come from: {', '.join(findings['flag_groups']) or 'n/a'}.")
        elif m == "localization":
            out.append(f"Localization: lowest per-category localization: {weakest('localization')}.")
        elif m == "category_accuracy":
            out.append(f"Category accuracy: lowest per-category accuracy: {weakest('category_accuracy')}.")
        elif m == "judge_consistency" and consistency:
            per = ", ".join(f"{lg} {v['same_verdict']['k']}/{v['same_verdict']['n']}" for lg, v in consistency["by_lang"].items())
            out.append(f"Judge consistency: same verdict per language: {per}; "
                       "disagreements are judge-only flags that fusion already sends to the review queue.")
    return out


def build_sections(
    results: list[dict],
    items: dict[str, Item],
    labels: dict[str, list[Label]],
    label_info: dict | None,
    thresholds: dict,
    langs: list[str],
    calibration: dict,
    consistency: dict | None,
) -> dict:
    labelled = [r for r in results if r["id"] in labels]
    all_eval = evaluate(labelled, labels, thresholds, langs) if labelled else None
    heldout = calibration.get("heldout", {}).get("overall") if calibration.get("status") == "ok" else None
    basis = "held-out split" if heldout else ("all labelled items" if all_eval else "no labels")
    headline = heldout or (all_eval or {}).get("overall")
    findings = _findings(results, items, labels)
    rows = _target_rows(headline, basis, consistency)
    return {
        "quality": {
            "basis": basis,
            "label_sources": (label_info or {}).get("by_source", {}),
            "targets": rows,
            "miss_explanations": _explain_misses(rows, all_eval, findings, consistency),
            "all_labelled": all_eval,
        },
        "top_failures": _top_failures(results, items, labels),
        "missed_examples": _missed(results, items, labels),
        "findings": findings,
    }


def _pct(v) -> str:
    return "n/a" if v is None else f"{100 * v:.1f}%"


def _rate(r: dict | None) -> str:
    if not r or not r.get("n"):
        return "n/a (n=0)"
    return f"{_pct(r['value'])} [{_pct(r['ci95'][0])}-{_pct(r['ci95'][1])}] ({r['k']}/{r['n']})"


def _span(f: dict) -> str:
    if not f.get("span"):
        return "no span"
    a, b = f["span"]
    return f"{a:.2f}-{b:.2f} s" if f["unit"] == "seconds" else f"chars {int(a)}-{int(b)}"


def _md_cell(text) -> str:
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ")


JUDGE_UNAVAILABLE_REASONS = {
    "skipped:llm_not_configured": "LLM_MODEL / LLM_API_KEY not set",
    "skipped:judge_unavailable": "judging stopped after repeated consecutive LLM failures",
    "error:llm_error": "LLM endpoint failed after retries",
    "error:invalid_json_after_repair": "LLM answered invalid JSON after the repair retry",
}


def _label_caveat(src: dict) -> str:
    """Honest statement of where the labels came from (the dataset's own human labels are named as such)."""
    used = ", ".join(f"{k} {v}" for k, v in src.items()) or "none"
    human = {k: v for k, v in src.items() if k.startswith("human")}
    reviewed = src.get("reviewed_preexisting", 0)
    synthetic = src.get("injector", 0)
    if human:
        return f"- Labels used: {used}. Human labels are counted separately from programmatic ones."
    if reviewed:
        return (
            f"- **No human labels on seeded errors.** {reviewed} `reviewed_preexisting` labels were added by the recipe author "
            f"after reading the output (real defects in un-seeded items) and are tagged with that source. Labels used: {used}."
        )
    if synthetic:
        return f"- **No human annotations were added.** Labels used: {used}."
    if not src:
        return "- No evaluation labels were supplied for this run."
    return f"- Labels used: {used} (external dataset labels; no source tag provided)."


def _judge_unavailable_line(j: dict) -> str | None:
    """Plain-words statement when the LLM judge could not read some items (empty when it ran fine)."""
    un = (j or {}).get("unavailable") or {}
    n = un.get("items_not_judged", 0)
    if not n:
        return None
    why = "; ".join(f"{JUDGE_UNAVAILABLE_REASONS.get(k, k)}: {v}" for k, v in un.get("by_reason", {}).items())
    return (
        f"**LLM judge unavailable for {n} item(s)** ({why}). These items were scored by the deterministic and "
        "round-trip checks only, so mistranslation / omission / addition recall on them is lower."
    )


def render_summary_md(s: dict) -> str:
    ds, q, j = s.get("dataset", {}), s.get("quality") or {}, s.get("judge", {})
    L = ["# Quality harness report", ""]
    L += [f"Dataset `{ds.get('path', '')}`: **{ds.get('evaluated', 0)}** items evaluated ({', '.join(ds.get('langs', []))}).", ""]
    unavailable_line = _judge_unavailable_line(j)
    if unavailable_line:
        L += [unavailable_line, ""]

    L += ["## Headline vs targets", ""]
    if q.get("targets"):
        L += [
            f"Basis: **{q['basis']}** (Wilson 95% intervals; detection definitions at the end).",
            "",
            "| metric | target | value [95% CI] (k/n) | status |",
            "|---|---|---|---|",
        ]
        for r in q["targets"]:
            op = ">=" if r["kind"] == "min" else "<="
            status = "n/a" if r["met"] is None else ("met" if r["met"] else "**MISSED**")
            if r["met"] is not None and r["ci_spans_target"]:
                status += " (CI spans target)"
            val = _rate({"value": r["value"], "ci95": r["ci95"], "k": r["k"], "n": r["n"]})
            L.append(f"| {r['label']} | {op} {_pct(r['target'])} | {val} | {status} |")
        if q.get("miss_explanations"):
            L += ["", "Why targets were missed:"] + [f"- {m}" for m in q["miss_explanations"]]
        L += ["", "With samples this small, the intervals are wide: e.g. about 50 items cannot tell 80% from 70% recall."]
    L.append("")

    allq = q.get("all_labelled")
    if allq:
        L += [
            "## Per language (all labelled items)",
            "",
            "| lang | items | recall | precision | false alarms strict | false alarms reviewed | localization | category acc. |",
            "|---|---:|---|---|---|---|---|---|",
        ]
        for lg, m in allq["by_lang"].items():
            L.append(
                f"| {lg} | {m['items']} | {_rate(m['recall_items'])} | {_rate(m['precision_items'])} | {_rate(m['false_alarm_strict'])} | "
                f"{_rate(m['false_alarm_reviewed'])} | {_rate(m['localization'])} | {_rate(m['category_accuracy'])} |"
            )
        L += [
            "",
            "## Per category (all labelled items)",
            "",
            "| category | labels | recall | precision | localization | mean IoU | category acc. |",
            "|---|---:|---|---|---|---:|---|",
        ]
        for c, m in allq["by_category"].items():
            iou = "n/a" if m["mean_iou"] is None else f"{m['mean_iou']:.2f}"
            L.append(
                f"| {c} | {m['labels']} | {_rate(m['recall'])} | {_rate(m['precision'])} | "
                f"{_rate(m['localization'])} | {iou} | {_rate(m['category_accuracy'])} |"
            )
        L += [
            "",
            "These tables include the calibration train split (in-sample for the judge cutoff); the headline uses held-out items only.",
            "Recall counts only strong (corroborated) flags; weak or review-queue signals are never counted as recall.",
            "",
        ]
    flags = s.get("flags", {})
    L += ["## Counted flags", "", "| category | flags |", "|---|---:|"] + [f"| {c} | {n} |" for c, n in flags.get("by_category", {}).items()]
    L += ["", "| lang | items | items flagged |", "|---|---:|---:|"]
    L += [f"| {lg} | {v['items']} | {v['items_flagged']} |" for lg, v in flags.get("by_lang", {}).items()]
    L.append("")

    L += ["## Top failures", ""]
    for t in s.get("top_failures", []):
        lab = "; ".join(f"{lb['category']} ({lb['source']})" for lb in t["labels"]) or "no label"
        L += [f"### `{t['id']}` ({t['lang']}), MQM penalty {t['mqm_penalty']}, labels: {lab}", "", f"- Source: {_md_cell(t['source'])}"]
        for f in t["flags"]:
            corr = f"; corroborated by {', '.join(f['corroborated_by'])}" if f.get("corroborated_by") else ""
            L.append(
                f"- **{f['category']}** / {f['severity']} by `{f['check_id']}` at {_span(f)} (confidence {f['confidence']:.2f}{corr}): {_md_cell(f['message'])}"
            )
            if f.get("excerpt"):
                L.append(f"  - Translation: {_md_cell(f['excerpt'])}")
        L.append("")
    if s.get("missed_examples"):
        L += ["## Missed errors (labelled, no counted flag)", ""]
        for m in s["missed_examples"]:
            lab = "; ".join(f"{lb['category']}: {lb['note']}" for lb in m["labels"])
            L.append(f"- `{m['id']}` ({m['lang']}): {_md_cell(lab)}. Weak signals: {', '.join(m['weak_signals']) or 'none'}; judge: {m['judge']}")
        L.append("")

    fnd = s.get("findings") or {}
    L += ["## Findings for the Bodhan team", "", "Counted flags on outputs that carry no seeded error (candidate genuine model defects):", ""]
    for k, g in (fnd.get("flag_groups") or {}).items():
        ex = g["example"]
        L.append(f"- **{k}**: {g['count']} item(s) ({', '.join(g['ids'])}). Example `{ex['id']}` ({ex['lang']}) at {_span(ex)}: {_md_cell(ex['message'])}")
        if ex.get("excerpt"):
            L.append(f"  - Translation: {_md_cell(ex['excerpt'])}")
    if not fnd.get("flag_groups"):
        L.append("- none")
    if fnd.get("reviewed_preexisting"):
        L += ["", "Defects in un-seeded Bodhan outputs confirmed by reading the text (labels with source `reviewed_preexisting`):", ""]
        L += [f"- {_md_cell(k)} ({', '.join(v)})" for k, v in fnd["reviewed_preexisting"].items()]
    L.append("")

    L += ["## Judge (tier-3)", ""]
    st = j.get("status", {})
    L.append(f"- {unavailable_line}" if unavailable_line else "- The LLM judge was available for every item it was asked to read.")
    L += [
        f"- Status counts: {', '.join(f'{k} {v}' for k, v in st.items()) or 'none'}",
        f"- Valid JSON: {_rate(j.get('json_valid'))}; answers needing the repair retry: {j.get('repaired', 0)}",
        f"- Quotes found verbatim in the translation: {_rate(j.get('quote_found'))}",
        f"- Tokens (judge pass): {j.get('tokens', 0)}; review-queue flags (judge-only or quote not found): {j.get('review_queue_flags', 0)}",
    ]
    cons = s.get("judge_consistency")
    if cons:
        L.append(
            f"- Consistency re-run on {cons['items_requested']} items: same verdict {_rate(cons['overall']['same_verdict'])}, "
            f"same category set {_rate(cons['overall']['same_categories'])}, failed {cons['overall']['failed']}, tokens {cons.get('tokens', 0)}"
        )
        if cons["overall"].get("same_counted_flags"):
            L.append(f"- Same counted judge flags after fusion (what the report actually counts): {_rate(cons['overall']['same_counted_flags'])}")
    L.append("")

    cost = s.get("cost", {})
    L += ["## Cost and cache", "", "| service | calls | units | INR (cached-equivalent) |", "|---|---:|---:|---:|"]
    for svc, v in cost.get("per_service", {}).items():
        L.append(f"| {svc} | {v['calls']} | {v['units']:.1f} {v['unit']} | {v['inr']:.4f} |")
    L += [
        "",
        f"Total model cost of this evaluation if nothing were cached: **INR {cost.get('total_equivalent_inr', 0.0):.4f}** (the tier-3 judge is priced at 0).",
        "The new-vs-cached split, cache hit counts and runtime are in `run_meta.json`; they are kept out of the report so a cached rerun is byte-identical.",
        "",
    ]

    L += [
        "## Bad, skipped and failed items",
        "",
        f"- Bad rows skipped: {ds.get('bad_rows', 0)}; filtered out by `--langs`: {ds.get('filtered_out', 0)}",
        f"- Items whose processing failed: {ds.get('item_errors', 0)}",
        f"- Items whose audio file is missing (speech checks skipped): {ds.get('missing_audio', 0)}",
        f"- Labels dropped on their own (row kept): {ds.get('bad_labels', 0)}; calibration ids not in this run: {ds.get('calibration_ids_not_in_run', 0)}",
    ]
    for k, v in (s.get("check_status") or {}).items():
        L.append(f"- Check `{k}`: {v}")
    L.append("")

    L += ["## Caveats", ""]
    label_src = q.get("label_sources") or {}
    L.append(_label_caveat(label_src))
    if label_src.get("injector"):
        L.append(
            "- Synthetic labels are programmatic edits (deletions, splices, digit and name swaps, audio cuts, loops and noise). They are likely "
            "**easier** to detect than real model errors, so recall here is an optimistic estimate for real errors."
        )
    L += [
        "- Thresholds are tuned on the train split only; the headline is held-out. "
        "Run statistics (length, speaking rate, noise floor) are relative to this run.",
        "",
        "## Definitions",
        "",
        "- Detection recall: error items with at least one counted flag / error items. Counted = not `needs_corroboration` (review-queue flags never count).",
        "- False alarms, strict: flagged items / items without seeded or human labels (items with only `reviewed_preexisting` labels count as clean).",
        "- False alarms, reviewed: flagged items / items with no labels at all.",
        "- Localization: labels with a span on detected items where a counted flag overlaps the span (same unit: chars or seconds).",
        "- Category accuracy: labels on detected items where a counted flag has the label's category.",
        "- Judge consistency: same verdict when the identical judge request is sampled a second time.",
        "",
    ]
    return "\n".join(L)
