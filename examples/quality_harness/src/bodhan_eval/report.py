"""Reports (plan §16): summary.md, summary.json and, with a baseline, diff.md.

    python -m bodhan_eval.report --report report [--baseline old-report]

Built only from files in the report directory (items.jsonl, run.json, calibration.json if a
calibration ran, consistency.json if scripts/judge_consistency.py wrote one), so the CLI calls this
at the end of a run and an old report can be re-rendered. Sorted and rounded; the only run-specific
lines are cost and runtime (from run.json).

Diff: items matched by (id, lang); flags (decision "flag") matched by category and place — the same
span overlap when that side's text is unchanged, else the same quoted text. new / resolved /
persisting / severity changed.
"""

from __future__ import annotations

import argparse
import difflib
import json
from collections import Counter, defaultdict
from pathlib import Path

from .clients import expected_rpm
from .fuse import SEVERITY
from .review import why

TARGETS = {"recall": 0.8, "false_alarm_rate": 0.1, "judge_consistency": 0.9, "minutes_per_200": 30}


def _load(out: Path):
    records = [json.loads(line) for line in (out / "items.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    meta = json.loads((out / "run.json").read_text(encoding="utf-8")) if (out / "run.json").is_file() else {}
    opt = {name: json.loads((out / f"{name}.json").read_text(encoding="utf-8"))
           for name in ("calibration", "consistency") if (out / f"{name}.json").is_file()}
    return records, meta, opt


def _flags(r: dict) -> list[dict]:
    return [f for f in r.get("flags", []) if f["decision"] == "flag"]


def _cell(text: str, n: int = 70) -> str:
    text = " ".join(str(text).split()).replace("|", "\\|")
    return text if len(text) <= n else text[: n - 1] + "…"


def _pct(x) -> str:
    return "—" if x is None else f"{x:.0%}"


def _reason(r: dict, f: dict) -> str:
    if f.get("adjudicator"):
        return "adjudicator: " + f["adjudicator"]["reason"]
    cands = [r["candidates"][i] for i in f["candidates"]]
    best = max(cands, key=lambda c: (c["standalone"], c["check"] == "judge", c["score"]))
    return why(best) if best["check"] == "judge" else f"{best['check']}: {why(best)}"


# --- summary --------------------------------------------------------------------------------------------

def summarize(records: list[dict], meta: dict, opt: dict) -> dict:
    by_lang: dict = {}
    for lang in sorted({r["lang"] for r in records}):
        rs = [r for r in records if r["lang"] == lang]
        scored = [r for r in rs if r["verdict"] != "skipped"]
        words = sum(len(r["source"].split()) for r in scored)
        by_lang[lang] = {
            "items": len(rs), "scored": len(scored), "skipped": len(rs) - len(scored),
            **{v: sum(r["verdict"] == v for r in rs) for v in ("flagged", "uncertain", "clean")},
            "flagged_share": round(sum(r["verdict"] == "flagged" for r in scored) / len(scored), 3) if scored else None,
            "mqm": sum(r["mqm"] for r in rs),
            "mqm_per_100_words": round(100 * sum(r["mqm"] for r in rs) / words, 2) if words else None,
            "flags": {cat: dict(sorted(Counter(f["severity"] for r in rs for f in _flags(r) if f["category"] == cat).items()))
                      for cat in sorted({f["category"] for r in rs for f in _flags(r)})},
        }
    # Only numbers a cached rerun reproduces: requests = calls + cache hits, cost of the outputs paid now or
    # earlier. This run's own runtime and spend are in run.json.
    by_model = meta.get("cost", {}).get("by_model", {})
    requests = {m: v.get("calls", 0) + v.get("cache_hits", 0) - v.get("failures", 0) for m, v in by_model.items()}
    n = len(records)
    rpm = expected_rpm()
    minutes = {m: requests.get(m, 0) / rpm[m] for m in rpm}
    # Synthesis runs alongside the text checks (translate, LLM); transcription follows once the audio exists.
    fresh = max(minutes["speak"], minutes["translate"], minutes["llm"]) + minutes["transcribe"]
    run = {"items": n, "requests": dict(sorted(requests.items())),
           "bodhan_inr_outputs": round(sum(v.get("cost_inr", 0) + v.get("cached_cost_inr", 0)
                                           for m, v in by_model.items() if m != "llm"), 2),
           "requests_per_minute_assumed": rpm,
           "projected_fresh_minutes": round(fresh, 1), "projected_minutes_per_200": round(fresh / n * 200, 1) if n else None}

    targets = []
    cal = opt.get("calibration")
    if cal:
        m = cal["metrics"].get(f"held_out:{cal['chosen']}", cal["metrics"]["tuned_in_sample"])["all"]["all"]
        found = m["recall"]
        targets += [
            ("Detection recall (labelled errors flagged at their place, held-out)", found, TARGETS["recall"], ">="),
            ("False alarms on clean labelled items (held-out)", m["false_alarm_rate"], TARGETS["false_alarm_rate"], "<="),
            ("Flag precision (flags pointing at a labelled error)", m["precision"], None, None),
            ("Localization (found at the right place, of errors whose item is flagged)",
             round(found / m["item_recall"], 3) if found is not None and m["item_recall"] else None, None, None),
            ("Category accuracy (of found errors, flagged in the right category)",
             round(m["recall_right_category"] / found, 3) if found else None, None, None),
        ]
    cons = opt.get("consistency")
    targets.append(("Judge consistency (same verdict on re-judging)", cons["verdict_agreement"] if cons else None,
                    TARGETS["judge_consistency"], ">="))

    gaps = Counter()
    for r in records:
        for check, status in r["checks"].items():
            if status not in ("ok",):
                gaps[f"{check}: {status}"] += 1
    top = sorted((r for r in records if r["mqm"]), key=lambda r: (-r["mqm"], r["id"], r["lang"]))[:10]
    return {
        "run": run, "languages": by_lang,
        "targets": [{"name": t, "value": v, "target": g, "op": op} for t, v, g, op in targets],
        "calibration": {"chosen": cal["chosen"], "thresholds": cal["thresholds"]["tuned"],
                        "labelled_items": cal["labelled_items"],
                        "by_category": cal["metrics"].get(f"held_out:{cal['chosen']}", {}).get("category", {})} if cal else None,
        "top_failures": [{"id": r["id"], "lang": r["lang"], "mqm": r["mqm"],
                          "flags": [{"category": f["category"], "severity": f["severity"], "text": f["text"],
                                     "why": _reason(r, f)} for f in _flags(r)[:3]]} for r in top],
        "coverage_gaps": dict(sorted(gaps.items(), key=lambda kv: (-kv[1], kv[0]))),
        "warnings": meta.get("warnings", []),
    }


def _met(v, target, op):
    if v is None:
        return "not measured in this run"
    if target is None:
        return ""
    return "✅ met" if (v >= target if op == ">=" else v <= target) else "❌ missed"


def render_summary(s: dict, diff: dict | None) -> str:
    run = s["run"]
    out = ["# Translation quality report", ""]
    langs = s["languages"]
    out += ["| language | items | scored | flagged | uncertain | clean | MQM | MQM / 100 words |", "|---|---|---|---|---|---|---|---|"]
    for lang, v in langs.items():
        out.append(f"| {lang} | {v['items']} | {v['scored']} | {v['flagged']} ({_pct(v['flagged_share'])}) | {v['uncertain']} "
                   f"| {v['clean']} | {v['mqm']} | {v['mqm_per_100_words'] if v['mqm_per_100_words'] is not None else '—'} |")
    req = ", ".join(f"{m} {k}" for m, k in run["requests"].items() if k)
    out += ["", f"Model requests: {req or 'none'}. Bodhan cost of these outputs: ₹{run['bodhan_inr_outputs']} "
            f"(paid in this run or an earlier one; reruns use the cache). This run's own runtime and spend: run.json.", ""]

    out += ["## Quality targets", "", "| measure | value | target | |", "|---|---|---|---|"]
    for t in s["targets"]:
        target = "" if t["target"] is None else f"{'≥' if t['op'] == '>=' else '≤'} {_pct(t['target'])}"
        out.append(f"| {t['name']} | {_pct(t['value'])} | {target} | {_met(t['value'], t['target'], t['op'])} |")
    if run["projected_minutes_per_200"] is not None:
        ok = run["projected_minutes_per_200"] < TARGETS["minutes_per_200"]
        assumed = ", ".join(f"{m} {k}" for m, k in sorted(run["requests_per_minute_assumed"].items()))
        out.append(f"| Runtime per 200 items, fresh (projected: these requests at {assumed} requests/min) "
                   f"| {run['projected_minutes_per_200']} min | < 30 min | {'✅ met' if ok else '❌ missed'} |")
    cal = s["calibration"]
    if cal:
        li = cal["labelled_items"]
        out += ["", f"From calibration.json: {li['usable']} labelled items ({li['clean']} clean, {li['labels']} labelled errors), "
                f"strategy `{cal['chosen']}`, flag threshold {cal['thresholds']['flag']}. "
                + ("`keep`: this calibration changed nothing, as no tuning beat the thresholds in use on held-out folds. "
                   "If those thresholds were set from these same labels, the numbers above are in-sample; "
                   "calibration.json has every strategy's held-out result." if cal["chosen"] == "keep" else
                   "Held-out = tuned on 4/5 of the labelled items, measured on the rest."), "",
                "| category | labelled errors | found | right category | flags | precision |", "|---|---|---|---|---|---|"]
        for cat, m in cal["by_category"].items():
            out.append(f"| {cat} | {m['labels']} | {_pct(m['recall'])} | {_pct(m['recall_right_category'])} | {m['flags']} | {_pct(m['precision'])} |")
    else:
        out += ["", "No `--calibration` labels in this run: recall, false alarms and category accuracy are not measured."]

    cats = sorted({c for v in langs.values() for c in v["flags"]})
    if cats:
        out += ["", "## Flags by language and category", "", "Count (critical / major / minor).", "",
                "| language | " + " | ".join(cats) + " |", "|---|" + "---|" * len(cats)]
        for lang, v in langs.items():
            cells = []
            for c in cats:
                sev = v["flags"].get(c, {})
                total = sum(sev.values())
                cells.append(f"{total} ({'/'.join(str(sev.get(x, 0)) for x in reversed(SEVERITY))})" if total else "")
            out.append(f"| {lang} | " + " | ".join(cells) + " |")

    if s["top_failures"]:
        out += ["", "## Worst items", "", "| item | lang | MQM | category | severity | words | why |", "|---|---|---|---|---|---|---|"]
        for t in s["top_failures"]:
            for k, f in enumerate(t["flags"]):
                head = f"| {t['id']} | {t['lang']} | {t['mqm']} " if k == 0 else "| | | "
                out.append(f"{head}| {f['category']} | {f['severity']} | {_cell(f['text'], 40)} | {_cell(f['why'], 110)} |")

    if diff:
        out += ["", "## Changes since the baseline", "", diff["headline"], ""]
        reg = diff["new"][:5]
        if reg:
            out += ["| item | lang | category | severity | words | why |", "|---|---|---|---|---|---|"]
            out += [f"| {x['id']} | {x['lang']} | {x['category']} | {x['severity']} | {_cell(x['text'], 40)} | {_cell(x['why'], 100)} |" for x in reg]
        out += ["", "Details: [diff.md](diff.md)."]

    out += ["", "## Not checked", ""]
    out += [f"- {k}: {v} item(s)" for k, v in s["coverage_gaps"].items()] or ["- Every check ran on every item."]
    out += [f"- {w}" for w in s["warnings"]]
    out += ["", "Per-item details: items.jsonl (flags with evidence and the candidates behind them)."]
    return "\n".join(out) + "\n"


# --- diff -----------------------------------------------------------------------------------------------

def _same(a: dict, b: dict, old: dict, new: dict) -> bool:
    if a["category"] != b["category"]:
        return False
    if a["side"] == b["side"] and old[a["side"]] == new[a["side"]]:
        (s1, e1), (s2, e2) = a["span"], b["span"]
        return (s1 < e2 and s2 < e1) if s1 < e1 and s2 < e2 else (s1 <= e2 and s2 <= e1)
    return a["text"] == b["text"]


def _changed_words(old: str, new: str, n: int = 160) -> str:
    a, b = old.split(), new.split()
    parts = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag != "equal":
            parts.append(f"−“{' '.join(a[i1:i2])}” +“{' '.join(b[j1:j2])}”" if i2 > i1 and j2 > j1
                         else f"−“{' '.join(a[i1:i2])}”" if i2 > i1 else f"+“{' '.join(b[j1:j2])}”")
    return _cell("; ".join(parts), n)


def diff(records: list[dict], baseline: list[dict]) -> dict:
    old = {(r["id"], r["lang"]): r for r in baseline}
    new = {(r["id"], r["lang"]): r for r in records}
    rows = defaultdict(list)
    delta: dict = defaultdict(lambda: Counter())
    changed_text = {}
    for key in sorted(old.keys() & new.keys()):
        o, n = old[key], new[key]
        texts_o = {"source": o["source"], "translation": o["translation"] or ""}
        texts_n = {"source": n["source"], "translation": n["translation"] or ""}
        if texts_o["translation"] != texts_n["translation"]:
            changed_text[key] = _changed_words(texts_o["translation"], texts_n["translation"])
        fo, fn = _flags(o), _flags(n)
        matched = set()
        for f in fn:
            j = next((j for j, g in enumerate(fo) if j not in matched and _same(g, f, texts_o, texts_n)), None)
            row = {"id": key[0], "lang": key[1], "category": f["category"], "severity": f["severity"], "text": f["text"],
                   "why": _reason(n, f), "translation_changed": changed_text.get(key)}
            if j is None:
                rows["new"].append(row)
            else:
                matched.add(j)
                was = fo[j]["severity"]
                rows["persisting" if was == f["severity"] else "severity_changed"].append({**row, "was": was})
        for j, g in enumerate(fo):
            if j not in matched:
                rows["resolved"].append({"id": key[0], "lang": key[1], "category": g["category"], "severity": g["severity"],
                                         "text": g["text"], "why": _reason(o, g), "translation_changed": changed_text.get(key)})
        for side, r in (("before", o), ("after", n)):
            for f in _flags(r):
                delta[(key[1], f["category"])][side] += 1
            delta[(key[1], "MQM")][side] += r["mqm"]
    rank = {s: i for i, s in enumerate(SEVERITY)}
    for k in rows:
        rows[k].sort(key=lambda x: (-rank[x["severity"]], x["id"], x["lang"], x["category"]))
    only_old, only_new = sorted(old.keys() - new.keys()), sorted(new.keys() - old.keys())
    headline = (f"{len(rows['new'])} new flags (regressions), {len(rows['resolved'])} resolved, {len(rows['persisting'])} "
                f"persisting, {len(rows['severity_changed'])} with a changed severity; {len(changed_text)} items have a "
                f"different translation; {len(only_old)} items only in the baseline, {len(only_new)} only in this run.")
    return {"headline": headline, **{k: rows[k] for k in ("new", "resolved", "severity_changed", "persisting")},
            "delta": {f"{lang}|{cat}": dict(c) for (lang, cat), c in sorted(delta.items())},
            "only_in_baseline": [list(k) for k in only_old], "only_in_run": [list(k) for k in only_new]}


def render_diff(d: dict, baseline: Path) -> str:
    out = ["# Changes since the baseline", "", f"Baseline: `{baseline}`.", "", d["headline"], "",
           "## Flags per language and category", "", "| language | category | before | after | change |", "|---|---|---|---|---|"]
    for key, c in d["delta"].items():
        lang, cat = key.split("|")
        b, a = c.get("before", 0), c.get("after", 0)
        if a != b:
            out.append(f"| {lang} | {cat} | {b} | {a} | {a - b:+d} |")
    for name, title in (("new", "New flags (regressions)"), ("resolved", "Resolved"), ("severity_changed", "Severity changed")):
        rows = d[name]
        out += ["", f"## {title} ({len(rows)})", ""]
        if not rows:
            out.append("None.")
            continue
        out += ["| item | lang | category | severity | words | why | translation change |", "|---|---|---|---|---|---|---|"]
        for x in rows:
            sev = f"{x['was']} → {x['severity']}" if "was" in x else x["severity"]
            out.append(f"| {x['id']} | {x['lang']} | {x['category']} | {sev} | {_cell(x['text'], 40)} | {_cell(x['why'], 90)} "
                       f"| {x['translation_changed'] or ''} |")
    out += ["", f"Persisting flags: {len(d['persisting'])} (same category, place and severity)."]
    for name, title in (("only_in_baseline", "Only in the baseline"), ("only_in_run", "Only in this run")):
        if d[name]:
            out += ["", f"{title}: " + ", ".join(f"{i}/{lang}" for i, lang in d[name])]
    return "\n".join(out) + "\n"


def write_reports(out: Path, baseline: Path | None = None) -> dict:
    records, meta, opt = _load(out)
    d = None
    if baseline:
        d = diff(records, _load(baseline)[0])
        (out / "diff.md").write_text(render_diff(d, baseline), encoding="utf-8")
    s = summarize(records, meta, opt)
    if d:
        s["diff"] = {k: (len(v) if isinstance(v, list) else v) for k, v in d.items() if k != "delta"}
    (out / "summary.json").write_text(json.dumps(s, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "summary.md").write_text(render_summary(s, d), encoding="utf-8")
    return s


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m bodhan_eval.report")
    p.add_argument("--report", required=True, type=Path)
    p.add_argument("--baseline", type=Path)
    args = p.parse_args(argv)
    write_reports(args.report, args.baseline)
    print(f"wrote {args.report / 'summary.md'}" + (f" and {args.report / 'diff.md'}" if args.baseline else ""))


if __name__ == "__main__":
    main()
