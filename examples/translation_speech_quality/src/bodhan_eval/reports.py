"""Stable assessment artifacts and a separate per-execution billing audit."""

import csv
import html
import json
from collections import Counter
from pathlib import Path

from .calibration import calibrate, evaluate


def dump(path, value):
    path = Path(path)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def finalize(rows, originals, labels, invalid, out, baseline=None):
    baseline_text = (Path(baseline) / "items.jsonl").read_text() if baseline else None
    thresholds, calibration = calibrate(rows, labels)
    for row in rows:
        row["flags"] = [f for f in row["candidates"] if not f.get("review_only") and f["confidence"] >= thresholds[f["type"]]]
        row["review_candidates"] = ([f for f in row["candidates"] if f not in row["flags"]]
                                    + row["checks"].get("judge", {}).get("uncertain", []))
    injected_records = [{"id": r["id"], "labels": r["labels"],
                         "annotation_source": r.get("annotation_source", "unspecified")}
                        for r in originals if "labels" in r]
    calibration["labelled_demo_evaluation"] = evaluate(rows, injected_records)
    repeats = [r["checks"]["judge"]["repeat_agrees"] for r in rows
               if "repeat_agrees" in r["checks"].get("judge", {})]
    languages = {}
    for lang in sorted({r["lang"] for r in rows}):
        selected = [r for r in rows if r["lang"] == lang]
        languages[lang] = {"items": len(selected), "flagged": sum(bool(r["flags"]) for r in selected),
                           "partial": sum(r["status"] != "complete" for r in selected),
                           "evaluation": evaluate(selected, [r for r in injected_records
                                                              if r["id"] in {s["id"] for s in selected}]),
                           "categories": dict(Counter(f["type"] for r in selected for f in r["flags"]))}
    summary = {"items": len(rows), "invalid_items": invalid, "languages": languages,
               "execution_artifact": "execution.json",
               "status": "partial" if invalid or any(r["status"] != "complete" for r in rows) else "complete",
               "flagged_items": sum(bool(r["flags"]) for r in rows),
               "judge_consistency": sum(repeats) / len(repeats) if repeats else None,
               "judge_repeat_items": len(repeats),
               "categories": dict(Counter(f["type"] for r in rows for f in r["flags"])),
               "evaluation": calibration["labelled_demo_evaluation"]}
    out.mkdir(parents=True, exist_ok=True)
    dump(out / "summary.json", summary)
    dump(out / "calibration.json", calibration)
    with (out / "items.jsonl").open("w") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    md = ["# Translation and speech quality report", "", f"Status: {summary['status']}", "",
          f"{len(rows)} valid items, {len(invalid)} invalid lines, {summary['flagged_items']} flagged items.", "",
          "| Language | Items | Flagged | Partial |", "|---|---:|---:|---:|"]
    md.extend(f"| {lang} | {s['items']} | {s['flagged']} | {s['partial']} |" for lang, s in languages.items())
    md += ["", "## Detection measurements", "", json.dumps(summary["evaluation"], indent=2), "",
           "## Quality targets", "",
           "These measurements use controlled injections and assumed-clean reference controls, not human-reviewed natural errors."]
    for name, target, upper in (("detection_recall", 0.8, False), ("false_alarm_rate", 0.1, True),
                                ("localization_overlap", 0.7, False), ("category_accuracy", 0.7, False)):
        value = summary["evaluation"].get(name)
        state = "unmeasured" if value is None else ("met" if (value <= target if upper else value >= target) else "missed")
        md.append(f"- {name}: {value}; target {'at most' if upper else 'at least'} {target}; {state}.")
    md += ["", "Misses can reflect weak semantic judgments, conservative uncalibrated thresholds, noisy round trips, "
           "incomplete API checks, and approximate omission labels. Inspect per-item evidence before assigning a cause.", "",
           "## Cost and runtime", "", "See [execution.json](execution.json) for this invocation's estimated cost, "
           "cache/API counts, scope and elapsed time. Assessment artifacts stay identical on cached replays; "
           "execution cost falls to zero.", "",
           "## Calibration and limits", "", f"Human-labelled calibration items: {calibration['human_items']}.",
           "An unset precision or recall is null, including categories with no labelled positives.",
           "Injected labels measure detection of controlled perturbations. They are not human judgments of natural model errors.",
           "Back-translations and ASR are fallible. Unsupported claims remain in the review queue.",
           f"Judge agreement: {summary['judge_consistency']} over {len(repeats)} repeated items.", "",
           "## Review first", ""]
    for row in rows:
        if row["flags"] or row["unavailable"]:
            md.append(f"- `{row['id']}` ({row['lang']}): " + ", ".join(f["type"] for f in row["flags"]))
            md.extend("  - " + str(message) for message in row["unavailable"])
            for finding in row["flags"]:
                md.append("  - " + json.dumps(finding, ensure_ascii=False))
    (out / "summary.md").write_text("\n".join(md) + "\n")
    with (out / "review.csv").open("w", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(["id", "lang", "source", "translation", "candidates", "human_labels_json"])
        for row in rows:
            writer.writerow([row["id"], row["lang"], row["source"], row["translation"],
                             json.dumps(row["flags"] + row["review_candidates"], ensure_ascii=False), ""])
    dashboard(rows, summary, out)
    if baseline_text is not None:
        baseline_diff(rows, baseline_text, out)
    else:
        (out / "diff.md").unlink(missing_ok=True)
    return summary


def baseline_diff(rows, baseline_text, out):
    old = {}
    for line in baseline_text.splitlines():
        item = json.loads(line)
        old[item["id"]] = item
    lines = ["# Comparison with baseline", "", "Counts compare supported findings, not correctness.", ""]
    for row in rows:
        before = old.pop(row["id"], None)
        if before is None:
            lines.append(f"- Added item `{row['id']}`.")
            continue
        a = Counter(f["type"] for f in before.get("flags", []))
        b = Counter(f["type"] for f in row["flags"])
        if a != b:
            lines.append(f"- `{row['id']}`: added {dict(b - a)}; resolved {dict(a - b)}.")
        if before.get("status") != row["status"]:
            lines.append(f"- `{row['id']}`: status {before.get('status')} → {row['status']}.")
    lines.extend(f"- Removed item `{key}`." for key in sorted(old))
    if len(lines) == 4:
        lines.append("No changes to supported findings or item status.")
    (out / "diff.md").write_text("\n".join(lines) + "\n")


def dashboard(rows, summary, out):
    # All user/model content is rendered as escaped text, never trusted HTML.
    records = []
    for row in rows:
        categories = ", ".join(dict.fromkeys(f["type"] for f in row["flags"]))
        cells = [html.escape(str(value)) for value in [row["id"], row["lang"], row["status"], categories]]
        cells.append('<div class="label">English source</div>' + html.escape(row["source"])
                     + '<div class="label">Supplied output</div>' + html.escape(str(row["translation"])))
        evidence = []
        for title, findings in (("Findings", row["flags"]), ("Needs review", row["review_candidates"])):
            if findings:
                evidence.append(f'<details><summary>{title} ({len(findings)})</summary><pre>'
                                + html.escape(json.dumps(findings, ensure_ascii=False, indent=2)) + '</pre></details>')
        if row["unavailable"]:
            evidence.append('<p>' + html.escape("; ".join(row["unavailable"])) + '</p>')
        cells.append("".join(evidence) or "No supported finding")
        records.append("<tr><td>" + "</td><td>".join(cells) + "</td></tr>")
    text = """<!doctype html><html lang="en"><meta charset="utf-8">
<title>Translation and speech quality</title><style>
body{font:16px system-ui;margin:36px;color:#172b35;background:#fafaf7}input{padding:10px;width:400px}
table{border-collapse:collapse;width:100%;table-layout:fixed;margin-top:25px}
th,td{text-align:left;vertical-align:top;padding:12px;border-bottom:1px solid #ccd4d3;overflow-wrap:anywhere}
th{background:#e6efeb;position:sticky;top:0}th:nth-child(1){width:15%}th:nth-child(2){width:4%}
th:nth-child(3){width:7%}th:nth-child(4){width:10%}th:nth-child(5){width:32%}th:last-child{width:32%}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.5 monospace;max-height:500px;overflow:auto}
summary{cursor:pointer;padding:8px 0;font-weight:600}.label{font-size:12px;font-weight:700;color:#526c72;margin:8px 0}
</style><h1>Translation and speech quality</h1><p>SUMMARY</p>
<label>Filter items <input id="filter" placeholder="Language, category, item ID or text"></label>
<table><thead><tr><th>Item</th><th>Lang</th><th>Status</th><th>Flags</th>
<th>Source and output</th><th>Evidence and review</th></tr></thead><tbody>ROWS</tbody></table>
<script>document.getElementById('filter').addEventListener('input', e => {
const q=e.target.value.toLowerCase();for(const row of document.querySelectorAll('tbody tr')){
row.hidden=!row.textContent.toLowerCase().includes(q);}});</script></html>"""
    text = text.replace("SUMMARY", html.escape(f"{len(rows)} items · {summary['flagged_items']} flagged · {summary['status']}"))
    (out / "dashboard.html").write_text(text.replace("ROWS", "\n".join(records)))
