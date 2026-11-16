"""Write the report files. Everything here is deterministic: no timestamps, sorted output, rounded numbers."""

import json
from collections import Counter
from pathlib import Path

SCHEMA_VERSION = 1


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


def write(out: Path, results: list[dict], skipped: list[dict], meta: dict, log):
    out.mkdir(parents=True, exist_ok=True)
    results = sorted(results, key=lambda r: (r["id"], r["lang"]))

    with open(out / "items.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for r in results:
            f.write(_dump(r) + "\n")

    langs = sorted({r["lang"] for r in results})
    per_lang = {lang: dict(Counter(r["status"] for r in results if r["lang"] == lang)) for lang in langs}
    not_run = Counter()
    for r in results:
        for check, status in r["checks_run"].items():
            if status.startswith("not_run"):
                not_run[f"{check}: {status}"] += 1
    summary = {
        "schema_version": SCHEMA_VERSION,
        "items": len(results),
        "status": dict(sorted(Counter(r["status"] for r in results).items())),
        "per_language": per_lang,
        "skipped": {"count": len(skipped), "reasons": dict(sorted(Counter(s["reason"] for s in skipped).items()))},
        "checks_not_run": dict(sorted(not_run.items())),
        "cost": report_cost(log),
        "warnings": sorted(meta.get("warnings", [])),
        "regen_agreement": meta.get("regen_agreement", {}),
        "judge_consistency": meta.get("judge_consistency", {}),
        "langs_requested": meta.get("langs", []),
    }
    (out / "summary.json").write_text(_dump(summary) + "\n", encoding="utf-8")
    (out / "summary.md").write_text(_summary_md(summary), encoding="utf-8")
    (out / "calibration.json").write_text(_dump(meta.get("calibration", {"labelled_items": 0})) + "\n", encoding="utf-8")

    diff = out / "diff.md"
    if meta.get("diff_md") is not None:
        diff.write_text(meta["diff_md"], encoding="utf-8")
    elif diff.exists():
        diff.unlink()  # stale diff from an earlier run with --baseline


def _summary_md(s: dict) -> str:
    lines = ["# Quality report", ""]
    for w in s["warnings"]:
        lines.append(f"> **Warning:** {w}")
    if s["warnings"]:
        lines.append("")
    lines += [f"Items scored: **{s['items']}** · skipped: **{s['skipped']['count']}** · model cost: **₹{s['cost']['total_inr']:.2f}**", ""]
    cols = ("clean", "flagged", "review", "unscored")
    lines += ["| Language | " + " | ".join(cols) + " |", "| --- " * (len(cols) + 1) + "|"]
    for lang, c in s["per_language"].items():
        lines.append(f"| {lang} | " + " | ".join(str(c.get(k, 0)) for k in cols) + " |")
    if s["skipped"]["reasons"]:
        lines += ["", "## Skipped rows", ""] + [f"- {r}: {n}" for r, n in s["skipped"]["reasons"].items()]
    if s["checks_not_run"]:
        lines += ["", "## Checks not run", ""] + [f"- {r}: {n}" for r, n in s["checks_not_run"].items()]
    return "\n".join(lines) + "\n"
