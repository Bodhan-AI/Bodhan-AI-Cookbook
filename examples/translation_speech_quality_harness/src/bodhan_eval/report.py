"""Report files: ``items.jsonl``, ``summary.json``, ``calibration.json``, ``summary.md`` (+ ``run_meta.json``).

Deterministic by construction: JSON with sorted keys, floats rounded to 4 dp, LF line endings, items
in load order, and no timestamps, durations or absolute paths. What legitimately differs between two
runs of the same input (money spent now vs. served from cache, runtime) goes to ``run_meta.json``,
which is not one of the report files. The "cost of outputs used" in the report counts cached outputs
at their original price, so it is identical on a rerun.

Phase 1 writes a plain ``summary.md``; the polished report (MQM, targets table, review queue) is Phase 6.
"""

from __future__ import annotations

import json
import math
import os
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import __version__, dashboard, mqm
from .config import category_group, taxonomy
from .models import Flag, ItemResult

if TYPE_CHECKING:  # pragma: no cover
    from .pipeline import RunResult

ROUND_DP = 4
SEV_RANK = {"critical": 0, "major": 1, "minor": 2}
TOP_FAILURES = 10
MAX_LISTED_PROBLEMS = 20


# ---------------------------------------------------------------------------
# deterministic serialisation
# ---------------------------------------------------------------------------


def clean(obj: Any) -> Any:
    """JSON-ready copy: floats rounded to 4 dp (non-finite -> None), tuples -> lists, keys -> str."""
    if isinstance(obj, bool) or obj is None or isinstance(obj, (int, str)):
        return obj
    if isinstance(obj, float):
        return round(obj, ROUND_DP) if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        items = sorted(obj) if isinstance(obj, (set, frozenset)) else obj
        return [clean(v) for v in items]
    if hasattr(obj, "item"):  # numpy scalars
        return clean(obj.item())
    return str(obj)


def dumps_line(obj: Any) -> str:
    return json.dumps(clean(obj), ensure_ascii=False, sort_keys=True)


def write_json(path: Path, obj: Any) -> None:
    text = json.dumps(clean(obj), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def write_jsonl(path: Path, rows: list[Any]) -> None:
    path.write_text("".join(dumps_line(r) + "\n" for r in rows), encoding="utf-8", newline="\n")


def _rel(path: str | None, base: Path) -> str | None:
    """A path relative to ``base`` with forward slashes (never absolute, so reports are portable)."""
    if not path:
        return None
    try:
        return Path(os.path.relpath(path, base)).as_posix()
    except ValueError:  # different drive on Windows
        return Path(path).name


# ---------------------------------------------------------------------------
# items.jsonl
# ---------------------------------------------------------------------------


def item_record(r: ItemResult, dataset_dir: Path, out_dir: Path) -> dict[str, Any]:
    it = r.item
    audio_base = out_dir if "audio" in it.generated else dataset_dir
    return {
        "id": it.id,
        "lang": it.lang,
        "status": r.status,
        "reason": r.reason,
        "calibration_only": it.calibration_only,
        "generated": sorted(it.generated),
        "has_reference": it.reference is not None,
        "has_translation": it.translation is not None,
        "audio": _rel(it.audio, audio_base),
        "checks": r.checks,
        "metrics": r.metrics,
        "flags": [f.to_dict() for f in r.flags],
        "uncertain": [f.to_dict() for f in r.uncertain],
        "dismissed": r.dismissed,
        "labels": [asdict(lab) for lab in it.labels] if it.labels is not None else None,
    }


# ---------------------------------------------------------------------------
# summary.json
# ---------------------------------------------------------------------------


def _check_counts(rs: list[ItemResult]) -> dict[str, dict[str, int]]:
    counts: dict[str, Counter] = {}
    for r in rs:
        for name, c in r.checks.items():
            counts.setdefault(name, Counter())[c["status"]] += 1
    return {name: dict(sorted(c.items())) for name, c in sorted(counts.items())}


def _language_summary(rs: list[ItemResult]) -> dict[str, Any]:
    flags = [f for r in rs for f in r.flags]
    return {
        "items": len(rs),
        "scored": sum(r.status == "ok" for r in rs),
        "skipped": sum(r.status != "ok" for r in rs),
        "flagged_items": sum(bool(r.flags) for r in rs),
        "uncertain_items": sum(bool(r.uncertain) for r in rs),
        "flags": len(flags),
        "flags_by_category": dict(sorted(Counter(f.category for f in flags).items())),
        "mqm": mqm.language_score(rs),
        "checks": _check_counts(rs),
    }


def check_table(rs: list[ItemResult]) -> dict[str, dict[str, Any]]:
    """Per check: on how many items it ran, was skipped or not applicable, or failed, and why. A check that
    could not run leaves no flag, so without this table an unchecked item would look like a clean one."""
    out: dict[str, dict[str, Any]] = {}
    for r in rs:
        for name, c in r.checks.items():
            row = out.setdefault(name, {"ok": 0, "error": 0, "skipped": 0, "not_applicable": 0, "reasons": Counter()})
            status = c.get("status")
            row[status if status in ("ok", "skipped", "not_applicable") else "error"] += 1
            if status != "ok" and c.get("reason"):
                row["reasons"][str(c["reason"])[:100]] += 1
    return {name: {**row, "reasons": dict(row["reasons"].most_common())} for name, row in sorted(out.items())}


def markdown_checks(table: dict[str, dict[str, Any]]) -> list[str]:
    lines = ["", "## Checks", "", "An item a check could not run on has no flags from it: read zero flags there as not checked.", "",
             "| Check | Ran | Skipped / not applicable | Failed | Main reasons |", "|---|---:|---:|---:|---|"]  # fmt: skip
    for name, row in table.items():
        reasons = "; ".join(f"{_cell(k, 70)} ({v})" for k, v in list(row["reasons"].items())[:3])
        lines.append(f"| {name} | {row['ok']} | {row['skipped'] + row['not_applicable']} | {row['error']} | {reasons} |")
    return lines


def _category_summary(rs: list[ItemResult]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for cat in taxonomy()["categories"]:
        hits = [(r, f) for r in rs for f in r.flags if f.category == cat]
        if not hits:
            continue
        out[cat] = {
            "group": category_group(cat),
            "flags": len(hits),
            "items": len({r.item.key for r, _ in hits}),
            "by_severity": dict(sorted(Counter(f.severity for _, f in hits).items())),
            "by_language": dict(sorted(Counter(r.item.lang for r, _ in hits).items())),
        }
    return out


def summarize(result: RunResult) -> dict[str, Any]:
    main = [r for r in result.results if not r.item.calibration_only]
    rep, budget, ev = result.load_report, result.budget, result.calibration.get("evaluation", {})
    by_lang: dict[str, list[ItemResult]] = {}
    for r in main:
        by_lang.setdefault(r.item.lang, []).append(r)
    generated = Counter(g for r in main for g in r.item.generated)
    return {
        "version": __version__,
        "dataset": {
            "file": result.options.dataset.name,
            "languages_requested": sorted(result.options.langs) if result.options.langs else "all",
            "items": len(main),
            "scored": sum(r.status == "ok" for r in main),
            "skipped_items": sum(r.status != "ok" for r in main),
            "bad_lines": len(rep.bad),
            "filtered_out": rep.filtered_out,
            "notes": len(rep.notes),
            "calibration_only_items": len(result.results) - len(main),
            "unknown_label_types": dict(sorted(rep.unknown_label_types.items())),
            "language_codes_normalised": dict(sorted(rep.lang_aliases.items())),
        },
        "generated": {"translations": generated["translation"], "audio": generated["audio"]},
        "per_language": {lang: _language_summary(rs) for lang, rs in sorted(by_lang.items())},
        "per_category": _category_summary(main),
        "checks": check_table(main),
        "mqm": mqm.language_score(main),
        "cost_inr_of_outputs_used": {**dict(sorted(budget.used.items())), "total": sum(budget.used.values())},
        "llm_tokens": dict(budget.llm_tokens),
        "thresholds": dict(sorted(result.thresholds.items())),
        "labelled_metrics": {
            k: ev.get(k)
            for k in ("labelled_items", "labels", "recall", "false_alarm_rate", "precision", "localization")
        }
        | {"category_accuracy": ev.get("category_accuracy")},
    }


# ---------------------------------------------------------------------------
# summary.md
# ---------------------------------------------------------------------------


def _cell(text: str | None, limit: int = 80) -> str:
    t = " ".join((text or "").split())
    t = t if len(t) <= limit else t[: limit - 1] + "…"
    return t.replace("|", "\\|")


def _pct(x: float | None) -> str:
    return "–" if x is None else f"{100 * x:.0f}%"


def _top_failures(rs: list[ItemResult]) -> list[tuple[ItemResult, Flag]]:
    pairs = [(r, f) for r in rs for f in r.flags]
    pairs.sort(key=lambda p: (SEV_RANK.get(p[1].severity, 3), -round(p[1].confidence, 4), p[0].item.key))
    return pairs[:TOP_FAILURES]


def render_markdown(result: RunResult, summary: dict[str, Any]) -> str:
    ds, lines = summary["dataset"], []
    langs = ", ".join(summary["per_language"]) or "none"
    lines += [
        "# Translation and speech quality report",
        "",
        f"Dataset `{ds['file']}` · languages: {langs} · {ds['items']} items "
        f"({ds['scored']} scored, {ds['skipped_items']} skipped) · {ds['bad_lines']} bad lines · "
        f"{ds['filtered_out']} filtered out by `--langs`",
        "",
        "## By language",
        "",
        "| Language | Items | Scored | Flagged items | Flags | Uncertain items | MQM score |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for lang, s in summary["per_language"].items():
        score = "–" if s["mqm"] is None else f"{s['mqm']:.1f}"
        lines.append(
            f"| {lang} | {s['items']} | {s['scored']} | {s['flagged_items']} | {s['flags']} | {s['uncertain_items']} "
            f"| {score} |"
        )
    lines += ["", "MQM score: 100 minus penalties per source word (minor 1, major 5, critical 10), averaged per item."]
    lines += ["", "## By category", ""]
    if summary["per_category"]:
        lines += ["| Category | Flags | Items | Severity |", "|---|---:|---:|---|"]
        for cat, s in summary["per_category"].items():
            sev = ", ".join(f"{k} {v}" for k, v in s["by_severity"].items())
            lines.append(f"| {cat} | {s['flags']} | {s['items']} | {sev} |")
    else:
        lines.append("No flags.")
    lines += _markdown_top_failures(result)
    lines += _markdown_calibration(summary)
    lines += markdown_checks(summary["checks"])
    lines += _markdown_cost(summary)
    lines += _markdown_problems(result)
    return "\n".join(lines) + "\n"


def _markdown_top_failures(result: RunResult) -> list[str]:
    main = [r for r in result.results if not r.item.calibration_only]
    top = _top_failures(main)
    lines = ["", f"## Top {len(top)} failures", ""]
    if not top:
        return lines + ["None."]
    for k, (r, f) in enumerate(top, 1):
        where = (
            f"“{_cell(f.text, 60)}”" if f.text else (f"source: “{_cell(f.source_text, 60)}”" if f.source_text else "")
        )
        detail = _cell(f.evidence[0]["detail"], 140) if f.evidence else ""
        lines.append(
            f"{k}. **{r.item.lang} / {r.item.id}** — {f.category} ({f.severity}, {f.confidence:.2f}) {where} — {detail}"
        )
    return lines


def _markdown_calibration(summary: dict[str, Any]) -> list[str]:
    m = summary["labelled_metrics"]
    if not m.get("labelled_items"):
        return ["", "## Calibration", "", "No labelled items: default thresholds used (see `calibration.json`)."]
    return [
        "",
        "## Calibration (labelled items, in-sample; cross-validated numbers in `calibration.json`)",
        "",
        "| Labelled items | Labels | Recall | False alarms | Precision | Localisation | Category accuracy |",
        "|---:|---:|---:|---:|---:|---:|---:|",
        f"| {m['labelled_items']} | {m['labels']} | {_pct(m['recall'])} | {_pct(m['false_alarm_rate'])} | "
        f"{_pct(m['precision'])} | {_pct(m['localization'])} | {_pct(m['category_accuracy'])} |",
    ]


def _markdown_cost(summary: dict[str, Any]) -> list[str]:
    cost = summary["cost_inr_of_outputs_used"]
    parts = ", ".join(f"{k} ₹{v:.4f}" for k, v in cost.items() if k != "total")
    tok = summary["llm_tokens"]
    return [
        "",
        "## Cost",
        "",
        f"Model outputs used: ₹{cost['total']:.4f}"
        + (f" ({parts})" if parts else "")
        + f"; judge LLM tokens: {tok['prompt']} prompt + {tok['completion']} completion. "
        "Money actually spent in this run (cache misses only) is in `run_meta.json`.",
    ]


def _markdown_problems(result: RunResult) -> list[str]:
    rep = result.load_report
    problems = [f"line {b['line']}: {b['reason']}" for b in rep.bad] + [
        f"{n['item']}: {n['reason']}" for n in rep.notes
    ]
    skipped = [r for r in result.results if r.status != "ok" and not r.item.calibration_only]
    problems += [f"{r.item.key}: {r.reason}" for r in skipped]
    lines = ["", "## Data problems", ""]
    if not problems:
        return lines + ["None."]
    lines += [f"- {_cell(p, 160)}" for p in problems[:MAX_LISTED_PROBLEMS]]
    if len(problems) > MAX_LISTED_PROBLEMS:
        lines.append(f"- … and {len(problems) - MAX_LISTED_PROBLEMS} more (see `summary.json` and `logs/events.jsonl`)")
    return lines


# ---------------------------------------------------------------------------
# entry points
# ---------------------------------------------------------------------------


def write_all(out: Path, result: RunResult) -> list[dict[str, Any]]:
    """Write the four always-present report files and dashboard.html; returns the item records (used by the baseline diff)."""
    out.mkdir(parents=True, exist_ok=True)
    dataset_dir = result.options.dataset.resolve().parent
    records = [item_record(r, dataset_dir, out.resolve()) for r in result.results]
    write_jsonl(out / "items.jsonl", records)
    summary = summarize(result)
    write_json(out / "summary.json", summary)
    write_json(out / "calibration.json", {"thresholds": result.thresholds, **result.calibration})
    (out / "summary.md").write_text(render_markdown(result, summary), encoding="utf-8", newline="\n")
    page = dashboard.render(result.results, result.options.dataset.name)
    (out / "dashboard.html").write_text(page, encoding="utf-8", newline="\n")
    return records


def write_run_meta(out: Path, result: RunResult, runtime_s: float) -> None:
    """What may differ between identical runs: money actually spent, call counts, cache hits, runtime."""
    b = result.budget
    write_json(
        out / "run_meta.json",
        {
            "version": __version__,
            "runtime_s": runtime_s,
            "spent_inr": {**dict(sorted(b.spent.items())), "total": b.total_spent},
            "paid_calls": dict(sorted(b.calls.items())),
            "cache_hits": dict(sorted(b.cache_hits.items())),
            "refused_by_budget": dict(sorted(b.refused.items())),
            "llm_provider_cost_usd": round(b.llm_cost_usd, 6),  # as reported by the provider (e.g. OpenRouter)
            "llm_response_formats": result.llm_formats,
            "max_cost_inr": result.options.max_cost_inr,
            "generation": result.generation,
        },
    )
