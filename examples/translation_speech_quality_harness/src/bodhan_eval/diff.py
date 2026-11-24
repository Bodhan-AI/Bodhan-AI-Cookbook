"""``diff.md``: what changed against a baseline report (``--baseline ./previous_report``).

Items are matched by ``(id, lang)``. Two flags are "the same problem" when they are in the same
category group and their spans overlap (translation or source side), or when neither has a location.
A flag only in the current run is a regression; one only in the baseline is an improvement.

Phase 1 version: headline deltas per language, regressions, improvements, items added/removed.
Phase 6 adds MQM/chrF++/CER deltas and the config-hash warning (docs/dev/BUILD_PLAN.md).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import category_group
from .spans import overlaps

SEV_RANK = {"critical": 0, "major": 1, "minor": 2}
MAX_EXAMPLES = 20


def _span(v: Any) -> tuple[int, int] | None:
    return (int(v[0]), int(v[1])) if isinstance(v, list) and len(v) == 2 else None


def same_problem(a: dict, b: dict) -> bool:
    if category_group(a["category"]) != category_group(b["category"]):
        return False
    sa, sb = _span(a.get("span")), _span(b.get("span"))
    ssa, ssb = _span(a.get("source_span")), _span(b.get("source_span"))
    if (sa is None and ssa is None) or (sb is None and ssb is None):
        return True  # unlocated: the same kind of problem on the same item
    return overlaps(sa, sb) or overlaps(ssa, ssb)


def _unmatched(flags: list[dict], others: list[dict]) -> list[dict]:
    """Flags in ``flags`` with no counterpart in ``others`` (each counterpart used once)."""
    used = [False] * len(others)
    out = []
    for f in flags:
        k = next((k for k, o in enumerate(others) if not used[k] and same_problem(f, o)), None)
        if k is None:
            out.append(f)
        else:
            used[k] = True
    return out


def _load_items(path: Path) -> dict[str, dict]:
    items = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            if not rec.get("calibration_only"):
                items[f"{rec['id']}::{rec['lang']}"] = rec
    return items


def _example(key: str, f: dict) -> str:
    text = " ".join((f.get("text") or f.get("source_text") or "").split())[:60].replace("|", "\\|")
    detail = " ".join(((f.get("evidence") or [{}])[0].get("detail") or "").split())[:120]
    quoted = f" “{text}”" if text else ""
    return f"- **{key.replace('::', ' / ')}** — {f['category']} ({f['severity']}){quoted} — {detail}"


def _severity_order(pair: tuple[str, dict]) -> tuple[int, str, str]:
    key, flag = pair
    return SEV_RANK.get(flag["severity"], 3), key, flag["category"]


def changes(base: dict[str, dict], cur: dict[str, dict]) -> tuple[list[tuple[str, dict]], list[tuple[str, dict]]]:
    """(new problems, problems gone) on the items in both runs, most severe first."""
    common = sorted(set(base) & set(cur))
    regressions = [(k, f) for k in common for f in _unmatched(cur[k]["flags"], base[k]["flags"])]
    improvements = [(k, f) for k in common for f in _unmatched(base[k]["flags"], cur[k]["flags"])]
    return sorted(regressions, key=_severity_order), sorted(improvements, key=_severity_order)


def gate_exit_code(counts: dict[str, int], max_new_major: int) -> int:
    """CI mode (``--fail-on-regression``): exit code 3 when there are more new major-or-critical problems than allowed."""
    return 3 if counts["new_major_or_worse"] > max_new_major else 0


def render(base: dict[str, dict], cur: dict[str, dict], baseline_name: str) -> str:
    common = sorted(set(base) & set(cur))
    regressions, improvements = changes(base, cur)
    lines = [
        "# Changes against the baseline",
        "",
        f"Baseline `{baseline_name}` · {len(common)} items in both runs · "
        f"{len(set(cur) - set(base))} added · {len(set(base) - set(cur))} removed",
        "",
        "## Headline per language (items in both runs)",
        "",
        "| Language | Items | Flagged before | Flagged now | Flags before | Flags now | New problems | Fixed problems |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for lang in sorted({cur[k]["lang"] for k in common}):
        ks = [k for k in common if cur[k]["lang"] == lang]
        lines.append(
            f"| {lang} | {len(ks)} | {sum(bool(base[k]['flags']) for k in ks)} | {sum(bool(cur[k]['flags']) for k in ks)} "
            f"| {sum(len(base[k]['flags']) for k in ks)} | {sum(len(cur[k]['flags']) for k in ks)} "
            f"| {sum(k2 in ks for k2, _ in regressions)} | {sum(k2 in ks for k2, _ in improvements)} |"
        )
    for title, rows in (("Regressions (new problems)", regressions), ("Improvements (problems gone)", improvements)):
        lines += ["", f"## {title}: {len(rows)}", ""]
        lines += [_example(k, f) for k, f in rows[:MAX_EXAMPLES]] or ["None."]
        if len(rows) > MAX_EXAMPLES:
            lines.append(f"- … and {len(rows) - MAX_EXAMPLES} more (compare the two `items.jsonl` files)")
    return "\n".join(lines) + "\n"


def write_diff(out: Path, baseline_dir: Path, current_records: list[dict[str, Any]]) -> dict[str, int]:
    """Write diff.md; return the counts the CI gate uses."""
    counts = {"new_problems": 0, "new_major_or_worse": 0, "fixed_problems": 0}
    base_items = baseline_dir / "items.jsonl"
    if not base_items.is_file():
        text = f"# Changes against the baseline\n\nBaseline `{baseline_dir.name}` has no `items.jsonl`; nothing to compare.\n"
    else:
        cur = {f"{r['id']}::{r['lang']}": r for r in current_records if not r.get("calibration_only")}
        cur = {k: json.loads(json.dumps(v, default=str)) for k, v in cur.items()}  # tuples -> lists, as on disk
        base = _load_items(base_items)
        text = render(base, cur, baseline_dir.name)
        new, gone = changes(base, cur)
        counts = {
            "new_problems": len(new),
            "new_major_or_worse": sum(f["severity"] in ("major", "critical") for _, f in new),
            "fixed_problems": len(gone),
        }
    (out / "diff.md").write_text(text, encoding="utf-8", newline="\n")
    return counts
