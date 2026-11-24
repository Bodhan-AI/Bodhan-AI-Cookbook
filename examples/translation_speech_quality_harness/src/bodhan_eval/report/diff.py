"""Compare two runs of the same items: what regressed, what improved, with examples."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from ..taxonomy import SEVERITY_RANK


def _load(path: Path) -> dict[str, dict]:
    rows = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                r = json.loads(line)
                rows[r["id"]] = r
    return rows


def _same(a: dict, b: dict) -> bool:
    if a["category"] != b["category"]:
        return False
    sa, sb = a.get("span"), b.get("span")
    if sa and sb:
        return sa[0] < sb[1] and sb[0] < sa[1]
    ta, tb = a.get("source_span"), b.get("source_span")
    if ta and tb:
        return ta[0] < tb[1] and tb[0] < ta[1]
    return True


def diff_reports(baseline_items: Path, current_rows: list[dict]) -> dict:
    base = _load(baseline_items)
    cur = {r["id"]: r for r in current_rows}
    common = sorted(set(base) & set(cur))
    new_flags, resolved, newly_flagged, newly_clean, changed_output = [], [], [], [], 0
    by_cat_base, by_cat_cur = Counter(), Counter()
    by_lang_base, by_lang_cur = Counter(), Counter()
    for i in common:
        b, c = base[i], cur[i]
        if b.get("translation") != c.get("translation"):
            changed_output += 1
        for f in b["flags"]:
            by_cat_base[f["category"]] += 1
            by_lang_base[b["lang"]] += 1
        for f in c["flags"]:
            by_cat_cur[f["category"]] += 1
            by_lang_cur[c["lang"]] += 1
        for f in c["flags"]:
            if not any(_same(f, g) for g in b["flags"]):
                new_flags.append((c, f))
        for g in b["flags"]:
            if not any(_same(g, f) for f in c["flags"]):
                resolved.append((b, g))
        if c["flags"] and not b["flags"]:
            newly_flagged.append(i)
        if b["flags"] and not c["flags"]:
            newly_clean.append(i)

    def ex(pairs, n):
        pairs = sorted(pairs, key=lambda p: (-SEVERITY_RANK[p[1]["severity"]], -p[1].get("confidence", 0), p[0]["id"]))
        return [{"id": r["id"], "lang": r["lang"], "category": f["category"], "severity": f["severity"], "message": f["message"],
                 "text": f.get("text"), "source_text": f.get("source_text")} for r, f in pairs[:n]]

    cats = sorted(set(by_cat_base) | set(by_cat_cur))
    langs = sorted(set(by_lang_base) | set(by_lang_cur))
    return {
        "baseline": Path(baseline_items).as_posix(),
        "counts": {"common_items": len(common), "only_in_baseline": len(set(base) - set(cur)), "only_in_current": len(set(cur) - set(base)),
                   "changed_outputs": changed_output, "new_flags": len(new_flags),
                   "new_major_or_worse": sum(1 for _, f in new_flags if SEVERITY_RANK[f["severity"]] >= 1),
                   "resolved_flags": len(resolved), "newly_flagged_items": len(newly_flagged), "newly_clean_items": len(newly_clean)},
        "by_category": {c: {"baseline": by_cat_base[c], "current": by_cat_cur[c], "delta": by_cat_cur[c] - by_cat_base[c]} for c in cats},
        "by_language": {lang: {"baseline": by_lang_base[lang], "current": by_lang_cur[lang], "delta": by_lang_cur[lang] - by_lang_base[lang]} for lang in langs},
        "regressions": ex(new_flags, 15),
        "improvements": ex(resolved, 10),
        "newly_flagged_items": newly_flagged[:50],
        "newly_clean_items": newly_clean[:50],
    }


def render_diff(d: dict) -> str:
    c = d["counts"]
    L = ["# Diff against baseline\n", f"Baseline: `{d['baseline']}`\n",
         f"{c['common_items']} items in both runs ({c['changed_outputs']} with a different translation); "
         f"{c['only_in_baseline']} only in baseline, {c['only_in_current']} only in this run.\n",
         f"- **Regressions:** {c['new_flags']} new flags ({c['new_major_or_worse']} major or critical); {c['newly_flagged_items']} items went from clean to flagged.",
         f"- **Improvements:** {c['resolved_flags']} flags resolved; {c['newly_clean_items']} items went from flagged to clean.\n"]
    moved = {k: v for k, v in d["by_category"].items() if v["delta"]}
    if moved:
        L.append("## Where it moved\n")
        L.append("| Category | Baseline flags | Current flags | Δ |")
        L.append("|---|---|---|---|")
        for k, v in sorted(moved.items(), key=lambda t: -abs(t[1]["delta"])):
            L.append(f"| {k} | {v['baseline']} | {v['current']} | {v['delta']:+d} |")
        L.append("")
        L.append("| Language | Baseline flags | Current flags | Δ |")
        L.append("|---|---|---|---|")
        for k, v in d["by_language"].items():
            L.append(f"| {k} | {v['baseline']} | {v['current']} | {v['delta']:+d} |")
        L.append("")
    for title, key in (("Regressions (new in this run)", "regressions"), ("Improvements (gone in this run)", "improvements")):
        if d[key]:
            L.append(f"## {title}\n")
            for e in d[key]:
                L.append(f"- **{e['lang']} · {e['category']} ({e['severity']})** `{e['id']}`: {e['message']}"
                         + (f" — flagged text “{e['text']}”" if e.get("text") else ""))
            L.append("")
    return "\n".join(L)
