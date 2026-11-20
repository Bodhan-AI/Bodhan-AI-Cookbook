"""Human-readable reports: summary.md, and diff.md against a baseline run.

Quality score (MQM-style): each final flag costs its severity weight (minor 1, major 5,
critical 10); score = 100 * (1 - total penalty / English source words), floored at 0.
A run with no flags scores 100. Scores are comparable across runs of the same dataset.

Nothing here depends on wall-clock time, so the same inputs give byte-identical reports.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from bodhan_eval.categories import CATEGORIES, SEVERITIES, SEVERITY_WEIGHT

MAX_FAILURES = 10
MAX_DIFF_EXAMPLES = 15


# ---------------------------------------------------------------------------
# Quality score
# ---------------------------------------------------------------------------


def _words(text: str | None) -> int:
    return max(1, len((text or "").split()))


def item_penalty(row: dict[str, Any]) -> float:
    return sum(SEVERITY_WEIGHT[f["severity"]] for f in row["flags"])


def quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def score(rs: list[dict[str, Any]]) -> float | None:
        if not rs:
            return None
        pen = sum(item_penalty(r) for r in rs)
        words = sum(_words(r["source"]) for r in rs)
        return round(max(0.0, 100.0 * (1.0 - pen / words)), 2)

    by_lang: dict[str, list] = defaultdict(list)
    for r in rows:
        by_lang[r["lang"]].append(r)
    return {
        "overall": score(rows),
        "by_lang": {k: score(v) for k, v in sorted(by_lang.items())},
        "definition": "100 * (1 - sum(severity weights of final flags) / source words); "
        "weights minor=1 major=5 critical=10",
    }


# ---------------------------------------------------------------------------
# summary.md
# ---------------------------------------------------------------------------


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.0f}%"


def _num(x: float | None, nd: int = 2) -> str:
    return "n/a" if x is None else f"{x:.{nd}f}"


def _excerpt(text: str | None, span: list[int] | None, pad: int = 25) -> str:
    if not text or not span:
        return ""
    s, e = span
    pre = "…" if s - pad > 0 else ""
    post = "…" if e + pad < len(text) else ""
    mid = text[s:e] if e > s else "‸"  # zero-width: show the insertion point
    out = f"{pre}{text[max(0, s - pad) : s]}**{mid}**{text[e : e + pad]}{post}"
    return out.replace("\n", " ⏎ ").replace("|", "\\|")


def _md_table(header: list[str], rows: list[list[Any]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out


def render_summary(
    summary: dict[str, Any], rows: list[dict[str, Any]], dataset: str | None = None
) -> str:
    q = summary.get("quality") or quality(rows)
    L: list[str] = ["# Quality report", ""]
    if dataset:
        L += [f"Dataset: `{dataset}`  ", ""]

    # --- headline ---------------------------------------------------------------------------
    v = summary.get("verdicts", {})
    n = summary.get("items", len(rows))
    flagged = n - v.get("clean", 0)
    cost = summary.get("cost", {})
    jc = summary.get("judge_consistency", {})
    L += ["## Headline", ""]
    L += _md_table(
        ["Measure", "Value"],
        [
            ["Quality score (0–100)", _num(q["overall"], 1)],
            ["Items evaluated", n],
            ["Items with at least one flag", f"{flagged} ({_pct(flagged / n if n else None)})"],
            ["Verdicts", ", ".join(f"{k} {v[k]}" for k in ("clean", *SEVERITIES) if v.get(k))],
            ["Items skipped at load", summary.get("skipped_items", 0)],
            ["Bodhan cost", f"₹{cost.get('total_inr', 0):.2f}"
             + (f" of ₹{cost['max_inr']:.2f} budget" if cost.get("max_inr") else "")],
            ["LLM tokens (in / out)", "{input} / {output}".format(**cost.get("llm_tokens",
                                                                {"input": 0, "output": 0}))],
            ["Judge consistency (verdict / flags)",
             f"{_pct(jc.get('verdict_agreement'))} / {_pct(jc.get('flag_agreement'))}"
             if jc.get("ran") else "not measured"],
        ],
    )  # fmt: skip
    L.append("")

    # --- per language -----------------------------------------------------------------------
    cats_by_lang: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        for f in r["flags"]:
            cats_by_lang[r["lang"]][f["category"]] += 1
    used = [c for c in CATEGORIES if any(cats_by_lang[lg][c] for lg in cats_by_lang)]
    L += ["## By language", ""]
    lang_rows = []
    for lang, counts in summary.get("by_lang", {}).items():
        lang_rows.append(
            [lang, counts.get("items", 0), _num(q["by_lang"].get(lang), 1)]
            + [counts.get(k, 0) for k in ("clean", *SEVERITIES)]
            + [cats_by_lang[lang][c] for c in used]
        )
    L += _md_table(["Lang", "Items", "Score", "Clean", *SEVERITIES, *used], lang_rows)
    L.append("")

    # --- calibration -------------------------------------------------------------------------
    cal = summary.get("calibration")
    if cal:
        m = cal["metrics"]
        L += ["## Detection accuracy on labelled items", ""]
        L += [
            f"Mode: {cal['mode']}. Clean items flagged: {_pct(m.get('clean_flag_rate'))} "
            f"(budget {_pct(cal.get('max_clean_flag_rate'))}) over {m.get('clean_items', 0)} "
            f"clean items. Category accuracy: {_pct(cal.get('category_accuracy'))}. "
            f"Mean span overlap (IoU): {_num(m.get('span_iou_mean'))}.",
            "",
        ]
        cal_rows = [
            [c, x["labels"], x["tp"], x["fp"], x["fn"], _pct(x["precision"]), _pct(x["recall"]),
             _num(x["f1"]), _num(cal["thresholds"].get(c))]
            for c, x in m["per_category"].items()
            if x["labels"] or x["fp"]
        ]  # fmt: skip
        L += _md_table(
            ["Category", "Labels", "TP", "FP", "FN", "Precision", "Recall", "F1", "Threshold"],
            cal_rows,
        )
        if cal["mode"] == "calibrate":
            L += [
                "",
                "_Thresholds were tuned on these same items, so these numbers are "
                "optimistic. Re-run held-out data with `--thresholds calibration.json`._",
            ]
        L.append("")

    # --- top failures ----------------------------------------------------------------------
    worst = sorted((r for r in rows if r["flags"]), key=lambda r: (-item_penalty(r), r["id"]))
    L += [f"## Top failures ({min(len(worst), MAX_FAILURES)} of {len(worst)} flagged items)", ""]
    if not worst:
        L += ["No item has a final flag.", ""]
    for r in worst[:MAX_FAILURES]:
        L += [f"### `{r['id']}` ({r['lang']}): {r['verdict']}, penalty {item_penalty(r):g}", ""]
        src = r["source"].replace("\n", " ⏎ ")
        L.append(f"- Source: {src[:200]}{'…' if len(src) > 200 else ''}")
        for f in sorted(r["flags"], key=lambda f: -SEVERITY_WEIGHT[f["severity"]]):
            area_text = r.get("spoken_text") if f["category"].startswith("speech_") else None
            text = area_text or r.get("translation")
            checks = ", ".join(f.get("details", {}).get("checks", [f.get("check", "")]))
            where = _excerpt(text, f.get("span"))
            ts = f.get("time_span")
            L.append(
                f"- **{f['category']}** ({f['severity']}, confidence {f['confidence']:.2f}; "
                f"{checks}): {f['evidence']}"
            )
            if where:
                L.append(f"  - at: {where}")
            if ts:
                L.append(f"  - audio: {ts[0]:.1f}–{ts[1]:.1f} s")
        L.append("")

    # --- skipped / problems -----------------------------------------------------------------
    skipped = summary.get("skipped_steps") or {}
    problems = summary.get("problems") or []
    if skipped or problems:
        L += ["## Skipped steps and input problems", ""]
        for k, cnt in skipped.items():
            L.append(f"- {k}: {cnt} item(s)")
        for p in problems[:20]:
            what = "skipped" if p.get("fatal") else "kept"
            L.append(f"- line {p['line_no']} ({p.get('item_id') or 'no id'}): "
                     f"{p['reason']} [{what}]")  # fmt: skip
        L.append("")

    # --- cost -------------------------------------------------------------------------------
    services = cost.get("services") or {}
    if services:
        L += ["## Cost", ""]
        L += _md_table(
            ["Service", "Paid calls", "Cache hits", "Units", "₹"],
            [[k, s["calls"], s["cached"], _num(s["units"], 1), _num(s["inr"], 4)]
             for k, s in services.items()],
        )  # fmt: skip
        L += ["", "Units: translate = output tokens, speak = characters, transcribe = seconds, "
                  "llm = tokens (priced only if LLM_PRICE_INR_PER_M_* is set).", ""]  # fmt: skip

    L += [
        "## Files",
        "",
        "- `items.jsonl`: per item, final flags, all candidates and every check's evidence.",
        "- `summary.json`: the numbers above, machine-readable.",
        "- `calibration.json`, `judge_consistency.json`, `diff.md`: when applicable.",
        "- `logs/run.jsonl`: one JSON event per step, including skipped steps and API timings.",
        "",
    ]
    return "\n".join(L)


# ---------------------------------------------------------------------------
# diff.md
# ---------------------------------------------------------------------------


def _overlap(a: list[int] | None, b: list[int] | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return a[0] < b[1] and b[0] < a[1] or a == b


def load_run(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = [json.loads(x) for x in (path / "items.jsonl").read_text("utf-8").splitlines() if x]
    summary = json.loads((path / "summary.json").read_text("utf-8"))
    return rows, summary


def compute_diff(rows: list[dict[str, Any]], base_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Flag-level comparison by item id. A current flag is 'same' if a baseline flag on the
    same item has the same category and an overlapping span, 'changed' if it overlaps a
    baseline flag of another category or severity, else 'new'. Unmatched baseline flags are
    'fixed'. Items only in one run are listed, not diffed."""
    base = {r["id"]: r for r in base_rows}
    cur = {r["id"]: r for r in rows}
    out: dict[str, list] = {"new": [], "fixed": [], "changed": []}
    same = 0
    for iid in sorted(set(cur) & set(base)):
        a, b = cur[iid]["flags"], list(base[iid]["flags"])
        for f in a:
            m = next((g for g in b if g["category"] == f["category"]
                      and _overlap(f.get("span"), g.get("span"))), None)  # fmt: skip
            if m is not None:
                b.remove(m)
                if m["severity"] != f["severity"]:
                    out["changed"].append((iid, m, f))
                else:
                    same += 1
                continue
            m = next((g for g in b if _overlap(f.get("span"), g.get("span"))), None)
            if m is not None:
                b.remove(m)
                out["changed"].append((iid, m, f))
            else:
                out["new"].append((iid, f))
        out["fixed"] += [(iid, g) for g in b]
    qa, qb = quality(rows), quality(base_rows)
    delta = None
    if qa["overall"] is not None and qb["overall"] is not None:
        delta = round(qa["overall"] - qb["overall"], 2)
    return {
        "counts": {
            "new": len(out["new"]),
            "fixed": len(out["fixed"]),
            "changed": len(out["changed"]),
            "unchanged": same,
        },  # fmt: skip
        "quality": {
            "current": qa["overall"],
            "baseline": qb["overall"],
            "delta": delta,
            "by_lang": {
                k: {"current": qa["by_lang"].get(k), "baseline": qb["by_lang"].get(k)}
                for k in sorted(set(qa["by_lang"]) | set(qb["by_lang"]))
            },
        },  # fmt: skip
        "only_in_current": sorted(set(cur) - set(base)),
        "only_in_baseline": sorted(set(base) - set(cur)),
        "_examples": out,
        "_text": {iid: r.get("translation") for iid, r in cur.items()}
        | {f"base:{iid}": r.get("translation") for iid, r in base.items()},
    }


def _signed(cur: float | None, base: float | None) -> str:
    return "n/a" if cur is None or base is None else f"{cur - base:+.2f}"


def render_diff(d: dict[str, Any], baseline: str) -> str:
    q, c = d["quality"], d["counts"]
    L = ["# Diff against baseline", "", f"Baseline: `{baseline}`", ""]
    delta = q["delta"]
    trend = "n/a" if delta is None else (f"{delta:+.2f}" + (" (worse)" if delta < 0 else ""))
    L += _md_table(
        ["Measure", "Baseline", "Current", "Change"],
        [["Quality score", _num(q["baseline"], 1), _num(q["current"], 1), trend]]
        + [[f"Score: {k}", _num(v["baseline"], 1), _num(v["current"], 1),
            _signed(v["current"], v["baseline"])]
           for k, v in q["by_lang"].items()],
    )  # fmt: skip
    L += ["", f"Flags: **{c['new']} new**, **{c['fixed']} fixed**, {c['changed']} changed, "
              f"{c['unchanged']} unchanged.", ""]  # fmt: skip
    if d["only_in_current"] or d["only_in_baseline"]:
        L += [f"Items only in current run: {len(d['only_in_current'])}; only in baseline: "
              f"{len(d['only_in_baseline'])} (not compared).", ""]  # fmt: skip
    ex, text = d["_examples"], d["_text"]
    for kind, title in (("new", "New flags"), ("fixed", "Fixed (no longer flagged)")):
        if not ex[kind]:
            continue
        L += [f"## {title}", ""]
        for iid, f in ex[kind][:MAX_DIFF_EXAMPLES]:
            t = text.get(iid if kind == "new" else f"base:{iid}")
            L.append(f"- `{iid}`: **{f['category']}** ({f['severity']}, "
                     f"{f['confidence']:.2f}): {f['evidence']}")  # fmt: skip
            if where := _excerpt(t, f.get("span")):
                L.append(f"  - at: {where}")
        if len(ex[kind]) > MAX_DIFF_EXAMPLES:
            L.append(f"- … and {len(ex[kind]) - MAX_DIFF_EXAMPLES} more")
        L.append("")
    if ex["changed"]:
        L += ["## Changed", ""]
        for iid, old, new in ex["changed"][:MAX_DIFF_EXAMPLES]:
            L.append(f"- `{iid}`: {old['category']}/{old['severity']} → "
                     f"**{new['category']}/{new['severity']}**: {new['evidence']}")  # fmt: skip
        L.append("")
    return "\n".join(L)


def public_diff(d: dict[str, Any]) -> dict[str, Any]:
    """The diff without the bulky example payload, for summary.json."""
    return {k: v for k, v in d.items() if not k.startswith("_")}
