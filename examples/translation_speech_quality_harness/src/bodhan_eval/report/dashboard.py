"""A single static HTML file with filters by language, category and severity. No external assets."""
from __future__ import annotations

import json
from pathlib import Path

TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Quality Harness Dashboard</title>
<style>
:root{--bg:#fafaf9;--fg:#1c1917;--muted:#78716c;--card:#fff;--line:#e7e5e4;--crit:#b91c1c;--maj:#c2410c;--min:#a16207;--ok:#15803d}
@media (prefers-color-scheme:dark){:root{--bg:#1c1917;--fg:#f5f5f4;--muted:#a8a29e;--card:#292524;--line:#44403c}}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,sans-serif}
main{max-width:1100px;margin:0 auto;padding:16px}
h1{font-size:20px;margin:8px 0}.sub{color:var(--muted)}
.filters{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}
select,input{background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:6px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px;margin:12px 0}
.tile{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px}.tile b{font-size:20px;display:block}
.item{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin:8px 0}
.flag{border-left:3px solid var(--maj);padding:4px 8px;margin:6px 0}.flag.critical{border-color:var(--crit)}.flag.minor{border-color:var(--min)}
.tag{display:inline-block;font-size:12px;border:1px solid var(--line);border-radius:10px;padding:0 8px;margin-right:4px}
.text{white-space:pre-wrap;word-break:break-word}.muted{color:var(--muted)}mark{background:#fde68a;color:#1c1917}
</style></head><body><main>
<h1>Translation &amp; speech quality</h1><div class="sub" id="sub"></div>
<div class="tiles" id="tiles"></div>
<div class="filters">
<select id="lang"><option value="">All languages</option></select>
<select id="cat"><option value="">All categories</option></select>
<select id="sev"><option value="">All severities</option><option>critical</option><option>major</option><option>minor</option></select>
<select id="status"><option value="flagged">Flagged items</option><option value="">All items</option><option value="clean">Clean items</option></select>
<input id="q" placeholder="Search id or text">
</div>
<div id="list"></div></main>
<script>
const DATA = __DATA__; const SUMMARY = __SUMMARY__;
const $ = id => document.getElementById(id);
const esc = s => (s ?? "").toString().replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const langs = [...new Set(DATA.map(r => r.lang))].sort();
const cats = [...new Set(DATA.flatMap(r => r.flags.map(f => f.category)))].sort();
langs.forEach(l => $("lang").insertAdjacentHTML("beforeend", `<option>${esc(l)}</option>`));
cats.forEach(c => $("cat").insertAdjacentHTML("beforeend", `<option>${esc(c)}</option>`));
$("sub").textContent = `${SUMMARY.items_scored} items scored · judge: ${SUMMARY.llm_model || "disabled"}`;
const tiles = [["Scored", SUMMARY.items_scored], ["Flagged", SUMMARY.status_counts.flagged || 0], ["Clean", SUMMARY.status_counts.clean || 0],
  ["Speech checked", SUMMARY.speech_checked_items], ["Cost (₹)", SUMMARY.cost.attributed_total_inr]];
$("tiles").innerHTML = tiles.map(([k, v]) => `<div class="tile"><span class="muted">${k}</span><b>${esc(v)}</b></div>`).join("");
function hl(text, span) { if (!span || !text) return esc(text); return esc(text.slice(0, span[0])) + "<mark>" + esc(text.slice(span[0], span[1])) + "</mark>" + esc(text.slice(span[1])); }
function render() {
  const l = $("lang").value, c = $("cat").value, s = $("sev").value, st = $("status").value, q = $("q").value.toLowerCase();
  const rows = DATA.filter(r => (!l || r.lang === l) && (!st || r.status === st) && (!q || (r.id + r.source + r.translation).toLowerCase().includes(q))
    && (!c || r.flags.some(f => f.category === c)) && (!s || r.flags.some(f => f.severity === s)));
  $("list").innerHTML = rows.slice(0, 300).map(r => {
    const fl = r.flags.filter(f => (!c || f.category === c) && (!s || f.severity === s));
    const first = fl.find(f => f.span);
    return `<div class="item"><div><b>${esc(r.id)}</b> <span class="tag">${esc(r.lang)}</span><span class="tag">${esc(r.status)}</span></div>
      <div class="muted text">${esc(r.source)}</div><div class="text">${hl(r.translation, first && first.span)}</div>
      ${fl.map(f => `<div class="flag ${f.severity}"><b>${esc(f.category)}</b> · ${esc(f.severity)} · conf ${f.confidence} · ${esc(f.sources.join(", "))}<br>${esc(f.message)}
      ${f.text ? `<br><span class="muted">flagged:</span> ${esc(f.text)}` : ""}</div>`).join("")}
      ${r.back_translation ? `<div class="muted">BT: ${esc(r.back_translation)}</div>` : ""}</div>`;
  }).join("") || "<p class='muted'>No items match.</p>";
}
["lang", "cat", "sev", "status", "q"].forEach(id => $(id).addEventListener("input", render));
render();
</script></body></html>
"""


def write_dashboard(rows: list[dict], summary: dict, path: Path) -> None:
    slim = [{k: r[k] for k in ("id", "lang", "status", "flags", "source", "translation", "back_translation")} for r in rows]
    html = TEMPLATE.replace("__DATA__", json.dumps(slim, ensure_ascii=False).replace("</", "<\\/")).replace(
        "__SUMMARY__", json.dumps(summary, ensure_ascii=False).replace("</", "<\\/"))
    path.write_text(html, encoding="utf-8", newline="\n")
