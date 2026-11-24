"""Stretch outputs: MQM-style scores, a human review queue, and a static HTML dashboard."""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path

from .calibration import review_id

# MQM-style penalty points per flagged error, as in the MQM scoring model (minor 1, major 5, critical 10).
MQM_WEIGHTS = {"minor": 1, "major": 5, "critical": 10}
WORD = re.compile(r"\S+")


def mqm_scores(rows: list[dict]) -> dict[str, dict]:
    """Return a per-language quality score: 100 minus penalty points per 100 source words, floored at 0."""
    scores: dict[str, dict] = {}
    for row in rows:
        if row["status"] == "unscored":
            continue
        data = scores.setdefault(row["lang"], {"words": 0, "penalty": 0})
        data["words"] += len(WORD.findall(row["source"]))
        data["penalty"] += sum(MQM_WEIGHTS.get(flag["severity"], 5) for flag in row["flags"])
    for data in scores.values():
        data["score"] = round(max(0.0, 100 - 100 * data["penalty"] / data["words"]), 2) if data["words"] else None
    return dict(sorted(scores.items()))


def review_queue(rows: list[dict]) -> str:
    """Export uncertain rows as CSV for blind human labelling.

    A row is uncertain when it carries unconfirmed evidence or a check did not complete.
    The opaque review ID hides injected mutation names. Fill `verdict` with `clean` or a
    category, add `severity`, and pass the file back with `--calibration answers.csv`.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["review_id", "lang", "reason", "source", "translation", "audio", "verdict", "severity", "annotator"])
    for row in sorted(rows, key=lambda value: review_id(value["lang"], value["id"])):
        unconfirmed = sorted({finding["category"] for finding in row["unconfirmed"]})
        incomplete = row["status"] == "partial"
        if not unconfirmed and not incomplete:
            continue
        reason = "; ".join(filter(None, ["weak evidence: " + ", ".join(unconfirmed) if unconfirmed else "", "incomplete checks" if incomplete else ""]))
        writer.writerow([review_id(row["lang"], row["id"]), row["lang"], reason, row["source"], row["translation"] or "", row["audio"] or "", "", "", ""])
    return buffer.getvalue()


def calibration_rows_from_csv(path: Path) -> list[dict]:
    """Turn answered review-queue rows into calibration records. Unanswered rows are skipped."""
    records = []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            verdict = (row.get("verdict") or "").strip().lower()
            if not verdict:
                continue
            labels = [] if verdict == "clean" else [{"type": verdict, "severity": (row.get("severity") or "major").strip().lower()}]
            record = {"review_id": row["review_id"].strip(), "labels": labels}
            if (row.get("annotator") or "").strip():
                record["annotator"] = row["annotator"].strip()
            records.append(record)
    return records


DASHBOARD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bodhan quality dashboard</title>
<style>
:root{--bg:#fff;--fg:#1b1f24;--muted:#5b6470;--line:#d8dde3;--chip:#eef1f4;--major:#b42318;--minor:#8a6100;--critical:#6b1e9a}
@media (prefers-color-scheme:dark){:root{--bg:#121417;--fg:#e8eaed;--muted:#9aa3ad;--line:#2c3138;--chip:#1e2227;
--major:#ff8a7a;--minor:#e6c15c;--critical:#d39bff}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}
main{max-width:1100px;margin:0 auto;padding:16px}
h1{font-size:1.4rem;margin:.2rem 0 1rem}
.controls{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px}
select,input{font:inherit;padding:6px 8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg)}
.tiles{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px}
.tile{border:1px solid var(--line);border-radius:8px;padding:8px 12px;min-width:120px}
.tile b{display:block;font-size:1.3rem}
table{width:100%;border-collapse:collapse}
th,td{text-align:left;vertical-align:top;padding:8px;border-bottom:1px solid var(--line)}
td.ev{font-family:ui-monospace,monospace;word-break:break-word}
.sev{font-weight:600}.sev.major{color:var(--major)}.sev.minor{color:var(--minor)}.sev.critical{color:var(--critical)}
.muted{color:var(--muted)}
@media (max-width:700px){thead{display:none}tr{display:block;border-bottom:1px solid var(--line);padding:6px 0}td{display:block;border:0;padding:2px 0}}
</style></head><body><main>
<h1>Bodhan quality dashboard</h1>
<div class="tiles" id="tiles"></div>
<div class="controls">
<select id="lang"><option value="">All languages</option></select>
<select id="cat"><option value="">All categories</option></select>
<select id="sev"><option value="">All severities</option></select>
<input id="q" type="search" placeholder="Search id or evidence">
</div>
<p class="muted" id="count"></p>
<table><thead><tr><th>Item</th><th>Category</th><th>Severity</th><th>Evidence</th><th>Why</th></tr></thead><tbody id="rows"></tbody></table>
</main>
<script id="data" type="application/json">__DATA__</script>
<script>
const data = JSON.parse(document.getElementById("data").textContent);
const el = id => document.getElementById(id);
const fill = (id, values) => values.forEach(v => { const o = document.createElement("option"); o.value = o.textContent = v; el(id).appendChild(o); });
fill("lang", data.languages); fill("cat", data.categories); fill("sev", ["critical", "major", "minor"]);
el("tiles").innerHTML = data.tiles.map(t => `<div class="tile"><span class="muted">${t[0]}</span><b>${t[1]}</b></div>`).join("");
const esc = s => s.replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function render() {
  const [l, c, s, q] = ["lang", "cat", "sev", "q"].map(id => el(id).value.toLowerCase());
  const shown = data.flags.filter(f => (!l || f.lang === l) && (!c || f.category === c) && (!s || f.severity === s)
    && (!q || (f.id + " " + f.evidence).toLowerCase().includes(q)));
  el("count").textContent = `${shown.length} of ${data.flags.length} flags`;
  el("rows").innerHTML = shown.map(f => `<tr><td>${esc(f.id)} <span class="muted">[${f.lang}]</span></td><td>${f.category}</td>
    <td class="sev ${f.severity}">${f.severity}</td><td class="ev">${esc(f.evidence)}<br><span class="muted">${f.field} ${f.start}–${f.end}</span></td>
    <td>${esc(f.detail)} <span class="muted">(${f.check})</span></td></tr>`).join("");
}
["lang", "cat", "sev", "q"].forEach(id => el(id).addEventListener("input", render));
render();
</script></body></html>
"""


def dashboard(rows: list[dict], summary: dict) -> str:
    """Render a self-contained HTML page with every flag and filters by language, category and severity."""
    flags = [
        {
            "id": row["id"],
            "lang": row["lang"],
            "category": flag["category"],
            "severity": flag["severity"],
            "check": flag["check"],
            "evidence": " ".join(flag["evidence"].split())[:300],
            "detail": flag["detail"],
            "field": flag["span"]["field"],
            "start": flag["span"]["start"],
            "end": flag["span"]["end"],
        }
        for row in rows
        for flag in row["flags"]
    ]
    seeded = summary["quality"]["injected_holdout"]

    def percent(value):
        return "n/a" if value is None else f"{value:.0%}"

    tiles = [
        ["Rows", str(len(rows))],
        ["Flagged rows", str(sum(bool(row["flags"]) for row in rows))],
        ["Seeded recall", percent(seeded["recall"])],
        ["Localization", percent(seeded["localization"])],
        ["Control flag rate", percent(summary["quality"]["unmutated_controls"]["flag_rate"])],
        *([f"MQM {lang}", "n/a" if data["score"] is None else f"{data['score']:.1f}"] for lang, data in summary.get("mqm", {}).items()),
    ]
    payload = {
        "flags": flags,
        "languages": sorted({row["lang"] for row in rows}),
        "categories": sorted({flag["category"] for flag in flags}),
        "tiles": tiles,
    }
    # Escape "</" so evidence text can never close the data script element.
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return DASHBOARD.replace("__DATA__", data)
