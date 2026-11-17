"""Write the report files. Everything here is deterministic: no timestamps, sorted output, rounded numbers."""

import csv
import io
import json
from collections import Counter
from pathlib import Path

SCHEMA_VERSION = 2

# MQM-style weights (WMT uses 5 for critical, original MQM 25; ours is stated in the report).
SEVERITY_WEIGHT = {"minor": 1, "major": 5, "critical": 10}
_SEV_RANK = {"critical": 0, "major": 1, "minor": 2}

# (calibration key, label, direction, target) from the brief's quality targets.
TARGETS = [("recall", "Detection recall", ">=", 0.80), ("false_alarm_rate_clean", "False alarms on clean items", "<=", 0.10),
           ("localization", "Localization", ">=", 0.70), ("category_accuracy", "Category accuracy", ">=", 0.70)]
JUDGE_TARGET = 0.90

REVIEW_COLUMNS = ["id", "lang", "category", "severity", "confidence", "source_span", "translation_span", "source_quote",
                  "translation_quote", "checks", "human_label", "human_severity"]
CLEAN_ANSWERS = {"clean", "none", "no", "no error", "ok", "-"}


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def report_cost(log) -> dict:
    """Cost of the model calls this report depends on, from cache records, so a cached rerun reports the same number."""
    by_key = {}
    for e in log.calls():
        by_key[e["key"]] = (e["service"], e.get("cost_inr", 0.0))
    per_service = Counter()
    for service, cost in by_key.values():
        per_service[service] += cost
    return {"total_inr": round(sum(per_service.values()), 4), "per_service_inr": {k: round(v, 4) for k, v in sorted(per_service.items())},
            "unique_model_calls": len(by_key)}


def quote(text: str | None, span, limit: int = 120) -> str:
    """The spanned text on one line. A zero-width span (something missing) shows the text around the gap, marked ⟦⟧."""
    if not text or not span:
        return ""
    s, e = max(0, span[0]), min(len(text), span[1])
    q = text[max(0, s - 40):s] + "⟦⟧" + text[s:s + 40] if s == e else text[s:e]
    q = " ".join(q.split())
    return q if len(q) <= limit else q[:limit - 1] + "…"


def _add_quotes(entry: dict, text: tuple[str, str]):
    entry["quote"] = {"source": quote(text[0], entry.get("source_span")), "translation": quote(text[1], entry.get("translation_span"))}


def _weight(flags) -> int:
    return sum(SEVERITY_WEIGHT.get(f["severity"], 5) for f in flags)


def write(out: Path, results: list[dict], skipped: list[dict], meta: dict, log, texts: dict | None = None,
          baseline: str | None = None, narrate=None) -> dict:
    """Write every report file. `texts` maps (id, lang) to (source, translation) for quotes. `narrate(facts)` may
    return an English paragraph about the numbers (the LLM); a fixed sentence is used otherwise. Returns the diff
    counts (empty without a baseline)."""
    base = load_baseline(Path(baseline)) if baseline else None  # before writing: --baseline may equal --out
    out.mkdir(parents=True, exist_ok=True)
    results = sorted(results, key=lambda r: (r["id"], r["lang"]))
    texts = texts or {}
    for r in results:
        t = texts.get((r["id"], r["lang"]), ("", ""))
        for entry in r["flags"] + r["review"]:
            _add_quotes(entry, t)

    with open(out / "items.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for r in results:
            f.write(_dump(r) + "\n")

    langs = sorted({r["lang"] for r in results})
    per_lang = {}
    for lang in langs:
        rows = [r for r in results if r["lang"] == lang]
        flags = [f for r in rows for f in r["flags"]]
        words = sum(len(texts.get((r["id"], lang), ("", ""))[0].split()) for r in rows if r["status"] != "unscored")
        per_lang[lang] = {"items": len(rows), "status": dict(sorted(Counter(r["status"] for r in rows).items())),
                          "flags": len(flags), "severity": dict(sorted(Counter(f["severity"] for f in flags).items())),
                          "source_words": words,
                          "weighted_errors_per_1000_words": round(1000 * _weight(flags) / words, 1) if words else None}
    per_cat = {}
    for r in results:
        for f in r["flags"]:
            per_cat.setdefault(f["category"], Counter())[r["lang"]] += 1
    not_run = Counter()
    for r in results:
        for check, status in r["checks_run"].items():
            if status.startswith("not_run"):
                not_run[f"{check}: {status}"] += 1

    diff = diff_md = None
    if base is not None:
        diff, diff_md = compare(base, results, per_lang)
    cal = meta.get("calibration", {"labelled_items": 0})
    summary = {
        "schema_version": SCHEMA_VERSION,
        "items": len(results),
        "status": dict(sorted(Counter(r["status"] for r in results).items())),
        "per_language": per_lang,
        "per_category": {c: dict(sorted(v.items())) for c, v in sorted(per_cat.items())},
        "severity_weights": SEVERITY_WEIGHT,
        "targets": targets(cal, meta.get("judge_consistency", {})),
        "skipped": {"count": len(skipped), "reasons": dict(sorted(Counter(s["reason"] for s in skipped).items()))},
        "checks_not_run": dict(sorted(not_run.items())),
        "cost": report_cost(log),
        "warnings": sorted(meta.get("warnings", [])),
        "regen_agreement": meta.get("regen_agreement", {}),
        "judge_consistency": meta.get("judge_consistency", {}),
        "langs_requested": meta.get("langs", []),
    }
    facts = {"items": summary["items"], "status": summary["status"], "per_category": summary["per_category"],
             "per_language": {lang: {k: v[k] for k in ("status", "flags", "weighted_errors_per_1000_words")} for lang, v in per_lang.items()},
             "targets": [{k: t[k] for k in ("metric", "value", "target")} for t in summary["targets"]]}
    text = narrate(facts) if narrate else None
    summary["narrative"] = {"text": text, "by": "llm"} if text else {"text": _fixed_narrative(summary), "by": "fixed"}
    if diff is not None:
        summary["diff"] = diff
    (out / "summary.json").write_text(_dump(summary) + "\n", encoding="utf-8")
    (out / "summary.md").write_text(_summary_md(summary, results, cal), encoding="utf-8")
    (out / "calibration.json").write_text(_dump(cal) + "\n", encoding="utf-8")
    (out / "review_queue.csv").write_text(_review_csv(results), encoding="utf-8")
    (out / "dashboard.html").write_text(_dashboard_html(results), encoding="utf-8")

    path = out / "diff.md"
    if diff_md is not None:
        path.write_text(diff_md, encoding="utf-8")
    elif path.exists():
        path.unlink()  # stale diff from an earlier run with --baseline
    return diff or {}


def targets(cal: dict, consistency: dict) -> list[dict]:
    overall = cal.get("overall") or {}
    rows = []
    for key, name, op, goal in TARGETS:
        v = overall.get(key)
        rows.append({"metric": name, "target": f"{op} {goal:.0%}", "value": v,
                     "met": None if v is None else (v >= goal if op == ">=" else v <= goal)})
    judged = [(c["items"], c["same_verdict"]) for c in consistency.values()]
    n = sum(k for k, _ in judged)
    v = round(sum(k * s for k, s in judged) / n, 3) if n else None
    rows.append({"metric": "Judge consistency", "target": f">= {JUDGE_TARGET:.0%}", "value": v, "met": None if v is None else v >= JUDGE_TARGET})
    return rows


def _pct(v) -> str:
    return "n/a" if v is None else f"{v:.0%}"


def _top_failures(results, n=5) -> list[tuple[dict, dict]]:
    """The most severe, most confident flags, one per category first."""
    all_flags = sorted(((r, f) for r in results for f in r["flags"]),
                       key=lambda x: (_SEV_RANK.get(x[1]["severity"], 9), -x[1]["confidence"], x[0]["id"], x[0]["lang"]))
    picked, seen = [], set()
    for r, f in all_flags:
        if f["category"] not in seen:
            picked.append((r, f))
            seen.add(f["category"])
    picked += [x for x in all_flags if x not in picked]
    return picked[:n]


def _example(r, f) -> list[str]:
    q = f.get("quote") or {}
    lines = [f"- **{r['id']} / {r['lang']}**: {f['category']}, {f['severity']}, confidence {f['confidence']:.2f}, found by {', '.join(f['checks'])}"]
    if q.get("source"):
        lines.append(f"  - source: “{q['source']}”")
    if q.get("translation"):
        lines.append(f"  - translation: “{q['translation']}”")
    return lines


def _fixed_narrative(s: dict) -> str:
    flagged = s["status"].get("flagged", 0)
    parts = [f"{flagged} of {s['items']} items have at least one flagged error"]
    scored = {lang: v["weighted_errors_per_1000_words"] for lang, v in s["per_language"].items() if v["weighted_errors_per_1000_words"] is not None}
    if scored and max(scored.values()) > 0:
        worst = max(scored, key=lambda lang: (scored[lang], lang))
        parts.append(f"{worst} is the weakest language")
    if s["per_category"]:
        cat = max(s["per_category"], key=lambda c: (sum(s["per_category"][c].values()), c))
        parts.append(f"{cat} is the most frequent error")
    return "; ".join(parts) + "."


def _summary_md(s: dict, results: list[dict], cal: dict) -> str:
    lines = ["# Quality report", ""]
    for w in s["warnings"]:
        lines.append(f"> **Warning:** {w}")
    if s["warnings"]:
        lines.append("")
    lines += [f"Items scored: **{s['items']}** · skipped: **{s['skipped']['count']}** · model cost: **₹{s['cost']['total_inr']:.2f}**", ""]
    lines += [s["narrative"]["text"] + (" *(Written by the LLM from the numbers below.)*" if s["narrative"]["by"] == "llm" else ""), ""]

    scored = {lang: v for lang, v in s["per_language"].items() if v["weighted_errors_per_1000_words"] is not None}
    if s["per_category"] or scored:
        lines.append("## Headline")
        lines.append("")
        if scored:
            worst = max(scored, key=lambda lang: (scored[lang]["weighted_errors_per_1000_words"], lang))
            lines.append(f"- Worst language: **{worst}**, {scored[worst]['weighted_errors_per_1000_words']} weighted errors per 1,000 source words.")
        if s["per_category"]:
            cat = max(s["per_category"], key=lambda c: (sum(s["per_category"][c].values()), c))
            lines.append(f"- Most frequent error: **{cat}**, {sum(s['per_category'][cat].values())} flags.")
        if "diff" in s:
            d = s["diff"]
            lines.append(f"- Against the baseline: **{d['regressed_items']}** items regressed, **{d['improved_items']}** improved (see `diff.md`).")
        lines.append("")

    cols = ("clean", "flagged", "review", "unscored")
    lines += ["## Per language", "",
              "| Language | " + " | ".join(cols) + " | flags | weighted / 1k words |", "| --- " * (len(cols) + 3) + "|"]
    for lang, c in s["per_language"].items():
        w = c["weighted_errors_per_1000_words"]
        lines.append(f"| {lang} | " + " | ".join(str(c["status"].get(k, 0)) for k in cols) + f" | {c['flags']} | {'n/a' if w is None else w} |")
    lines += ["", "Weights: " + ", ".join(f"{k} {v}" for k, v in s["severity_weights"].items()) + ". Lower is better.", ""]

    if s["per_category"]:
        langs = list(s["per_language"])
        lines += ["## Flags per category", "", "| Category | " + " | ".join(langs) + " | total |", "| --- " * (len(langs) + 2) + "|"]
        for cat, v in sorted(s["per_category"].items(), key=lambda x: (-sum(x[1].values()), x[0])):
            lines.append(f"| {cat} | " + " | ".join(str(v.get(lang, 0)) for lang in langs) + f" | {sum(v.values())} |")
        lines += ["", "## Top failures", ""]
        for r, f in _top_failures(results):
            lines += _example(r, f)
        lines.append("")

    if cal.get("labelled_items"):
        lines += ["## Against labels", "",
                  f"{cal['labelled_items']} labelled items, {cal['labelled_clean']} of them clean. Matching rule: {cal['matching_rule']}.", "",
                  "| Metric | Value | Target | Met |", "| --- | --- | --- | --- |"]
        for t in s["targets"]:
            met = "n/a" if t["met"] is None else ("yes" if t["met"] else "**no**")
            lines.append(f"| {t['metric']} | {_pct(t['value'])} | {t['target']} | {met} |")
        if cal.get("per_category"):
            lines += ["", "| Category | Recall | Precision | Found / labelled |", "| --- | --- | --- | --- |"]
            for cat, v in cal["per_category"].items():
                lines.append(f"| {cat} | {_pct(v['recall'])} | {_pct(v['precision'])} | {v['tp']} / {v['tp'] + v['fn']} |")
        lines.append("")
        fa = set(cal.get("false_alarm_items") or [])
        if fa:
            lines += ["## Flags on items labelled clean", "",
                      "Counted as false alarms. Labels that only record injected errors leave real model errors marked clean, "
                      "so read these before trusting the rate.", ""]
            for r in results:
                if f"{r['id']}/{r['lang']}" in fa:
                    for f in r["flags"]:
                        lines += _example(r, f)
            lines.append("")

    review = sum(len(r["review"]) for r in results)
    if review:
        lines += [f"{review} uncertain findings are in `review_queue.csv`. Fill `human_label` (a category, or `clean`) and pass the file "
                  "back with `--calibration` to re-calibrate.", ""]
    if s["skipped"]["reasons"]:
        lines += ["## Skipped rows", ""] + [f"- {r}: {n}" for r, n in s["skipped"]["reasons"].items()] + [""]
    if s["checks_not_run"]:
        lines += ["## Checks not run", ""] + [f"- {r}: {n}" for r, n in s["checks_not_run"].items()] + [""]
    return "\n".join(lines).rstrip("\n") + "\n"


def _review_csv(results) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(REVIEW_COLUMNS)
    for r in results:
        for f in r["review"]:
            q = f.get("quote") or {}
            w.writerow([r["id"], r["lang"], f["category"], f["severity"], f["confidence"], json.dumps(f.get("source_span")),
                        json.dumps(f.get("translation_span")), q.get("source", ""), q.get("translation", ""), ";".join(f["checks"]), "", ""])
    return buf.getvalue()


def read_review_csv(text: str) -> tuple[dict, int]:
    """{(id, lang): labels} from a filled review queue. An item counts only when every one of its rows has an answer;
    `clean` rows add no label. Returns (labels, rows skipped for a missing answer)."""
    rows = {}
    for row in csv.DictReader(io.StringIO(text)):
        rows.setdefault((row.get("id", ""), row.get("lang", "")), []).append(row)
    out, unanswered = {}, 0
    for key, group in rows.items():
        if not all((g.get("human_label") or "").strip() for g in group):
            unanswered += len(group)
            continue
        labels = []
        for g in group:
            answer = g["human_label"].strip()
            if answer.lower() in CLEAN_ANSWERS:
                continue
            spans = {}
            for side in ("source_span", "translation_span"):
                try:
                    v = json.loads(g.get(side) or "null")
                except ValueError:
                    v = None
                spans[side] = v if isinstance(v, list) and len(v) == 2 and all(isinstance(x, int) for x in v) and 0 <= v[0] <= v[1] else None
            labels.append({"type": answer, "severity": (g.get("human_severity") or g.get("severity") or "major").strip(),
                           "span": spans["source_span"] or spans["translation_span"], **spans, "origin": "human"})
        out[key] = labels
    return out, unanswered


# ---------------------------------------------------------------- baseline diff

def _same(a: dict, b: dict) -> bool:
    """Two flags are the same finding: same category, and overlapping source spans when both have one."""
    if a["category"] != b["category"]:
        return False
    sa, sb = a.get("source_span"), b.get("source_span")
    return sa is None or sb is None or (sa[0] < sb[1] and sb[0] < sa[1]) or sa == sb


def load_baseline(baseline: Path) -> dict:
    """Read a previous report fully, before anything is written (the baseline may be this run's own --out folder)."""
    b = {"path": baseline.as_posix(), "items": None, "per_language": {}, "schema_version": None, "error": None}
    try:
        b["items"] = {(r["id"], r["lang"]): r for r in map(json.loads, (baseline / "items.jsonl").read_text(encoding="utf-8").splitlines())}
    except (OSError, ValueError, KeyError) as e:
        b["error"] = type(e).__name__
    try:
        s = json.loads((baseline / "summary.json").read_text(encoding="utf-8"))
        b["per_language"], b["schema_version"] = s.get("per_language", {}), s.get("schema_version")
    except (OSError, ValueError):
        pass
    return b


def compare(baseline: dict, results: list[dict], per_lang: dict) -> tuple[dict, str]:
    lines = ["# Diff against baseline", "", f"Baseline: `{baseline['path']}`", ""]
    if baseline["items"] is None:
        lines.append(f"> **Warning:** cannot read the baseline's items.jsonl ({baseline['error']}); nothing compared.")
        return {"error": "baseline unreadable", "regressed_items": 0, "improved_items": 0, "new_flags": 0, "fixed_flags": 0}, "\n".join(lines) + "\n"
    old, old_lang = baseline["items"], baseline["per_language"]
    if baseline["schema_version"] != SCHEMA_VERSION:
        lines += [f"> **Note:** the baseline was written by report schema {baseline['schema_version']}, this run by {SCHEMA_VERSION}; "
                  "fields missing on either side are not compared.", ""]
        old_lang = {lang: v if isinstance(v, dict) else {} for lang, v in old_lang.items()}

    new = {(r["id"], r["lang"]): r for r in results}
    both = sorted(set(old) & set(new))
    regressions, fixes, transitions = [], [], Counter()
    for k in both:
        o, n = old[k], new[k]
        transitions[(o.get("status"), n["status"])] += o.get("status") != n["status"]
        regressions += [(n, f) for f in n["flags"] if not any(_same(f, g) for g in o.get("flags", []))]
        fixes += [(o, f) for f in o.get("flags", []) if not any(_same(f, g) for g in n["flags"])]
    serious = lambda pairs: sorted({(r["id"], r["lang"]) for r, f in pairs if f["severity"] in ("major", "critical")})  # noqa: E731
    counts = {"compared_items": len(both), "only_in_baseline": len(set(old) - set(new)), "only_in_this_run": len(set(new) - set(old)),
              "new_flags": len(regressions), "fixed_flags": len(fixes),
              "regressed_items": len({(r["id"], r["lang"]) for r, _ in regressions}),
              "improved_items": len({(r["id"], r["lang"]) for r, _ in fixes}),
              "items_with_new_major_or_critical": len(serious(regressions)),
              "new_flags_per_category": dict(sorted(Counter(f["category"] for _, f in regressions).items())),
              "fixed_flags_per_category": dict(sorted(Counter(f["category"] for _, f in fixes).items()))}

    lines += [f"Compared **{len(both)}** items (only in baseline: {counts['only_in_baseline']}, only in this run: {counts['only_in_this_run']}).",
              f"**{counts['regressed_items']}** items regressed ({counts['new_flags']} new flags; "
              f"{counts['items_with_new_major_or_critical']} items with a new major or critical one).",
              f"**{counts['improved_items']}** items improved ({counts['fixed_flags']} flags gone).", "",
              "| Language | weighted / 1k words, baseline | this run |", "| --- | --- | --- |"]
    for lang in sorted(set(per_lang) | set(old_lang)):
        a = (old_lang.get(lang) or {}).get("weighted_errors_per_1000_words")
        b = (per_lang.get(lang) or {}).get("weighted_errors_per_1000_words")
        lines.append(f"| {lang} | {'n/a' if a is None else a} | {'n/a' if b is None else b} |")
    cats = sorted(set(counts["new_flags_per_category"]) | set(counts["fixed_flags_per_category"]))
    if cats:
        lines += ["", "| Category | new | fixed |", "| --- | --- | --- |"]
        lines += [f"| {c} | {counts['new_flags_per_category'].get(c, 0)} | {counts['fixed_flags_per_category'].get(c, 0)} |" for c in cats]
    changed = sorted((a, b, n) for (a, b), n in transitions.items() if n)
    if changed:
        lines += ["", "Status changes: " + ", ".join(f"{a} → {b}: {n}" for a, b, n in changed) + "."]
    order = lambda pairs: sorted(pairs, key=lambda x: (_SEV_RANK.get(x[1]["severity"], 9), -x[1]["confidence"], x[0]["id"], x[0]["lang"]))  # noqa: E731
    for title, pairs in (("Regressions", regressions), ("Improvements", fixes)):
        if pairs:
            lines += ["", f"## {title}", ""]
            for r, f in order(pairs)[:8]:
                lines += _example(r, f)
    return counts, "\n".join(lines) + "\n"


# ---------------------------------------------------------------- static dashboard

_DASHBOARD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Quality dashboard</title>
<style>
:root { --bg: #fff; --fg: #1d1d1f; --muted: #6e6e73; --line: #e3e3e6; --crit: #b42318; --major: #b54708; --minor: #475467; }
@media (prefers-color-scheme: dark) { :root { --bg: #161618; --fg: #ececee; --muted: #a0a0a6; --line: #333338;
  --crit: #f97066; --major: #fdb022; --minor: #98a2b3; } }
body { margin: 0; padding: 16px; background: var(--bg); color: var(--fg); font: 15px/1.45 system-ui, sans-serif; }
h1 { font-size: 20px; margin: 0 0 12px; }
.filters { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
select, input { font: inherit; padding: 4px 6px; background: var(--bg); color: var(--fg); border: 1px solid var(--line); border-radius: 6px; }
.count { color: var(--muted); margin-bottom: 8px; }
.row { border-top: 1px solid var(--line); padding: 10px 0; overflow-wrap: anywhere; }
.head { display: flex; flex-wrap: wrap; gap: 8px; align-items: baseline; }
.sev { font-weight: 600; text-transform: uppercase; font-size: 12px; }
.critical { color: var(--crit); } .major { color: var(--major); } .minor { color: var(--minor); }
.q { margin: 4px 0 0; color: var(--muted); } .q b { color: var(--fg); font-weight: 500; }
</style></head><body>
<h1>Quality dashboard</h1>
<div class="filters">
<select id="lang" aria-label="Language"></select><select id="cat" aria-label="Category"></select>
<select id="sev" aria-label="Severity"></select><select id="kind" aria-label="Flags or review">
<option value="">flags and review</option><option value="flag">flags only</option><option value="review">review only</option></select>
<input id="q" type="search" placeholder="Search id or text" aria-label="Search">
</div>
<div class="count" id="count"></div><div id="list"></div>
<script id="data" type="application/json">__DATA__</script>
<script>
const rows = JSON.parse(document.getElementById("data").textContent);
const el = id => document.getElementById(id);
function fill(id, label, values) {
  el(id).innerHTML = `<option value="">all ${label}</option>` + [...new Set(values)].sort().map(v => `<option>${v}</option>`).join("");
}
fill("lang", "languages", rows.map(r => r.lang)); fill("cat", "categories", rows.map(r => r.category));
fill("sev", "severities", rows.map(r => r.severity));
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
function render() {
  const f = {lang: el("lang").value, category: el("cat").value, severity: el("sev").value, kind: el("kind").value};
  const q = el("q").value.toLowerCase();
  const shown = rows.filter(r => Object.entries(f).every(([k, v]) => !v || r[k] === v) &&
    (!q || (r.id + " " + r.source + " " + r.translation).toLowerCase().includes(q)));
  el("count").textContent = `${shown.length} of ${rows.length} findings`;
  el("list").innerHTML = shown.map(r => `<div class="row"><div class="head"><b>${esc(r.id)} / ${esc(r.lang)}</b>
    <span>${esc(r.category)}</span><span class="sev ${esc(r.severity)}">${esc(r.severity)}</span>
    <span class="q">${esc(r.kind)} · confidence ${r.confidence} · ${esc(r.checks)}</span></div>
    ${r.source ? `<p class="q">source: <b>${esc(r.source)}</b></p>` : ""}
    ${r.translation ? `<p class="q">translation: <b>${esc(r.translation)}</b></p>` : ""}</div>`).join("");
}
["lang", "cat", "sev", "kind", "q"].forEach(id => el(id).addEventListener("input", render));
render();
</script></body></html>
"""


def _dashboard_html(results) -> str:
    rows = []
    for r in results:
        for kind, entries in (("flag", r["flags"]), ("review", r["review"])):
            for f in entries:
                q = f.get("quote") or {}
                rows.append({"id": r["id"], "lang": r["lang"], "kind": kind, "category": f["category"], "severity": f["severity"],
                             "confidence": f["confidence"], "checks": ", ".join(f["checks"]),
                             "source": q.get("source", ""), "translation": q.get("translation", "")})
    data = json.dumps(rows, ensure_ascii=False, sort_keys=True).replace("</", "<\\/")  # a quote containing </script> cannot end the data block
    return _DASHBOARD.replace("__DATA__", data)
