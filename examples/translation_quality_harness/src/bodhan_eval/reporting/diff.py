"""Baseline comparison: what improved, what regressed, with examples."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from ..models import ItemResult


def load_items(report_dir: str | Path) -> list[dict]:
    path = Path(report_dir) / "items.jsonl"
    if not path.is_file():
        raise FileNotFoundError(f"baseline has no items.jsonl: {path}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _key(row: dict) -> tuple[str, str]:
    return (str(row["id"]), str(row["lang"]))


def _cats(row: dict) -> Counter:
    return Counter(f["category"] for f in row.get("flags") or [])


def compare(current: list[ItemResult], baseline_rows: list[dict]) -> dict:
    cur = {_key(r.model_dump(mode="json")): r.model_dump(mode="json") for r in current}
    base = {_key(r): r for r in baseline_rows}
    regressions, improvements, changed_outputs = [], [], 0
    unchanged = 0
    for key in sorted(set(cur) & set(base)):
        now, then = cur[key], base[key]
        if now.get("status") != "scored" or then.get("status") != "scored":
            continue
        if (now.get("translation") or "") != (then.get("translation") or "") or (now.get("transcript") or "") != (then.get("transcript") or ""):
            changed_outputs += 1
        c_now, c_then = _cats(now), _cats(then)
        new_cats = sorted(set(c_now) - set(c_then))
        gone_cats = sorted(set(c_then) - set(c_now))
        p_now, p_then = float(now.get("mqm_penalty") or 0), float(then.get("mqm_penalty") or 0)
        row = {
            "id": key[0],
            "lang": key[1],
            "penalty": p_now,
            "baseline_penalty": p_then,
            "new_categories": new_cats,
            "resolved_categories": gone_cats,
            "flags": [_brief(f) for f in now.get("flags") or []][:3],
            "baseline_flags": [_brief(f) for f in then.get("flags") or []][:3],
            "translation_changed": (now.get("translation") or "") != (then.get("translation") or ""),
        }
        if p_now > p_then or (new_cats and p_now >= p_then):
            regressions.append(row)
        elif p_now < p_then or (gone_cats and p_now <= p_then):
            improvements.append(row)
        else:
            unchanged += 1
    regressions.sort(key=lambda r: (-(r["penalty"] - r["baseline_penalty"]), r["id"], r["lang"]))
    improvements.sort(key=lambda r: (-(r["baseline_penalty"] - r["penalty"]), r["id"], r["lang"]))

    def counts(rows: dict) -> dict:
        out: dict = {}
        for (_, lang), r in rows.items():
            if r.get("status") != "scored":
                continue
            d = out.setdefault(lang, {"items": 0, "flagged": 0, "penalty": 0.0, "categories": Counter()})
            d["items"] += 1
            d["flagged"] += int(bool(r.get("flags")))
            d["penalty"] += float(r.get("mqm_penalty") or 0)
            d["categories"].update(_cats(r))
        return out

    now_c, then_c = counts(cur), counts(base)
    per_lang = {}
    for lang in sorted(set(now_c) | set(then_c)):
        a, b = now_c.get(lang), then_c.get(lang)
        per_lang[lang] = {
            "flag_rate": _rate(a),
            "baseline_flag_rate": _rate(b),
            "penalty_per_item": round(a["penalty"] / a["items"], 3) if a and a["items"] else None,
            "baseline_penalty_per_item": round(b["penalty"] / b["items"], 3) if b and b["items"] else None,
        }
    cat_now = sum((d["categories"] for d in now_c.values()), Counter())
    cat_then = sum((d["categories"] for d in then_c.values()), Counter())
    per_category = {
        c: {"flags": cat_now.get(c, 0), "baseline_flags": cat_then.get(c, 0), "delta": cat_now.get(c, 0) - cat_then.get(c, 0)}
        for c in sorted(set(cat_now) | set(cat_then))
    }
    return {
        "headline": {
            "compared": len(set(cur) & set(base)),
            "regressed": len(regressions),
            "improved": len(improvements),
            "unchanged": unchanged,
            "outputs_changed": changed_outputs,
            "only_in_current": len(set(cur) - set(base)),
            "only_in_baseline": len(set(base) - set(cur)),
        },
        "per_language": per_lang,
        "per_category": per_category,
        "regressions": regressions,
        "improvements": improvements,
    }


def _rate(d: dict | None) -> float | None:
    return round(d["flagged"] / d["items"], 4) if d and d["items"] else None


def _brief(flag: dict) -> dict:
    return {
        "category": flag["category"],
        "severity": flag["severity"],
        "confidence": flag["confidence"],
        "excerpt": flag.get("excerpt"),
        "source_excerpt": flag.get("source_excerpt"),
        "evidence": (flag.get("evidence") or [])[:2],
    }


HEADLINE_KEYS = ("compared", "regressed", "improved", "unchanged", "outputs_changed", "only_in_current", "only_in_baseline")


def render(diff: dict, baseline_dir: str) -> str:
    h = diff["headline"]
    lines = [
        "# Diff against baseline",
        "",
        f"Baseline: `{baseline_dir}`",
        "",
        "| Compared | Regressed | Improved | Unchanged | Outputs changed | New items | Dropped items |",
        "|---|---|---|---|---|---|---|",
        "| " + " | ".join(str(h[k]) for k in HEADLINE_KEYS) + " |",
        "",
        "## Per language",
        "",
        "| Lang | Flag rate (baseline -> now) | MQM penalty per item (baseline -> now) |",
        "|---|---|---|",
    ]
    for lang, v in diff["per_language"].items():
        lines.append(f"| {lang} | {_p(v['baseline_flag_rate'])} -> {_p(v['flag_rate'])} | {v['baseline_penalty_per_item']} -> {v['penalty_per_item']} |")
    lines += ["", "## Per category", "", "| Category | Baseline flags | Now | Delta |", "|---|---|---|---|"]
    for cat, v in diff["per_category"].items():
        lines.append(f"| {cat} | {v['baseline_flags']} | {v['flags']} | {v['delta']:+d} |")
    for title, rows, which in (("Regressions", diff["regressions"], "flags"), ("Improvements", diff["improvements"], "baseline_flags")):
        lines += ["", f"## {title}", ""]
        if not rows:
            lines.append("None.")
        for row in rows[:25]:
            change = f"penalty {row['baseline_penalty']} -> {row['penalty']}"
            cats = row["new_categories"] if title == "Regressions" else row["resolved_categories"]
            lines.append(
                f"- **{row['id']}** ({row['lang']}): {change}; {'new' if title == 'Regressions' else 'resolved'}: {', '.join(cats) or '-'}"
                + ("; translation changed" if row["translation_changed"] else "")
            )
            for f in row[which][:2]:
                text = f.get("excerpt") or f.get("source_excerpt") or ""
                ev = (f.get("evidence") or [""])[0]
                lines.append(f'  - `{f["category"]}` {f["severity"]} ({f["confidence"]:.2f}): "{text.replace(chr(10), " ")}" {ev}')
        if len(rows) > 25:
            lines.append(f"- ... {len(rows) - 25} more in `diff.json`")
    return "\n".join(lines) + "\n"


def _p(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.0%}"
