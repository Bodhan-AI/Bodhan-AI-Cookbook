"""``dashboard.html``: one static page per report, for reading flags item by item.

Vanilla JavaScript, no network: the run's items are embedded as JSON. Filters by language, category, severity
and check; each item shows the source and the translation with every flagged span highlighted, and the
evidence behind each flag. Text is inserted with ``textContent`` only, so nothing in the data can run as code.
Deterministic: the same run writes the same bytes.
"""

from __future__ import annotations

import json
from typing import Any

from . import mqm
from .models import Flag, ItemResult


def _flag(f: Flag, uncertain: bool = False) -> dict[str, Any]:
    return {
        "category": f.category,
        "severity": f.severity,
        "confidence": round(f.confidence, 2),
        "span": list(f.span) if f.span else None,
        "source_span": list(f.source_span) if f.source_span else None,
        "checks": f.checks,
        "evidence": [str(e.get("detail", ""))[:400] for e in f.evidence],
        "uncertain": uncertain,
    }


def data(results: list[ItemResult]) -> list[dict[str, Any]]:
    return [
        {
            "id": r.item.id,
            "lang": r.item.lang,
            "status": r.status,
            "source": r.item.source,
            "translation": r.item.translation or "",
            "mqm": mqm.item_score(r),
            "flags": [_flag(f) for f in r.flags] + [_flag(f, True) for f in r.uncertain],
            "failed_checks": sorted(n for n, c in r.checks.items() if c.get("status") == "error"),
        }
        for r in results
        if not r.item.calibration_only
    ]


def render(results: list[ItemResult], title: str) -> str:
    payload = json.dumps(data(results), ensure_ascii=False, sort_keys=True).replace("</", "<\\/")
    return PAGE.replace("__TITLE__", json.dumps(title)[1:-1].replace("<", "")).replace("__DATA__", payload)


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Quality report: __TITLE__</title>
<style>
:root{--bg:#fff;--fg:#1d1d1f;--muted:#6b6b70;--line:#e3e3e8;--card:#f7f7f9;--crit:#c62828;--major:#ef6c00;--minor:#b59400;--unc:#7b61ff}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--fg:#ececf0;--muted:#9a9aa3;--line:#2c2c31;--card:#1d1d21}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,"Noto Sans","Nirmala UI",sans-serif}
header{padding:16px;border-bottom:1px solid var(--line)}h1{font-size:18px;margin:0 0 8px}
.filters{display:flex;flex-wrap:wrap;gap:8px}select,input{font:inherit;padding:4px 6px;background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:6px}
main{padding:0 16px 32px}.item{border:1px solid var(--line);border-radius:8px;margin:12px 0;padding:12px;background:var(--card)}
.meta{color:var(--muted);font-size:13px}.text{white-space:pre-wrap;margin:6px 0}
mark{background:none;border-bottom:3px solid var(--major);cursor:help}mark.critical{border-color:var(--crit)}mark.minor{border-color:var(--minor)}mark.uncertain{border-bottom-style:dashed;border-color:var(--unc)}
.flag{font-size:13px;margin:4px 0;padding-left:8px;border-left:3px solid var(--major)}.flag.critical{border-color:var(--crit)}.flag.minor{border-color:var(--minor)}.flag.uncertain{border-color:var(--unc)}
.tag{display:inline-block;font-size:12px;padding:0 6px;border-radius:10px;border:1px solid var(--line);margin-right:4px}
</style></head><body>
<header><h1>Quality report: __TITLE__</h1><div class="filters">
<select id="lang"><option value="">All languages</option></select>
<select id="cat"><option value="">All categories</option></select>
<select id="sev"><option value="">All severities</option><option>critical</option><option>major</option><option>minor</option></select>
<select id="chk"><option value="">All checks</option></select>
<select id="show"><option value="flagged">Flagged items</option><option value="uncertain">Uncertain items</option><option value="all">All items</option><option value="failed">Items with a failed check</option></select>
<input id="q" placeholder="Search id or text"><span id="count" class="meta"></span></div></header>
<main id="list"></main>
<script id="data" type="application/json">__DATA__</script>
<script>
const items = JSON.parse(document.getElementById("data").textContent);
const $ = id => document.getElementById(id);
function el(tag, cls, text){const e=document.createElement(tag); if(cls) e.className=cls; if(text!==undefined) e.textContent=text; return e;}
function fill(id, values){[...new Set(values)].sort().forEach(v=>{const o=el("option","",v); o.value=v; $(id).appendChild(o);});}
fill("lang", items.map(i=>i.lang)); fill("cat", items.flatMap(i=>i.flags.map(f=>f.category))); fill("chk", items.flatMap(i=>i.flags.flatMap(f=>f.checks)));
function highlighted(text, flags, key){
  const spans = flags.filter(f=>f[key]).map(f=>({a:f[key][0], b:f[key][1], f})).sort((x,y)=>x.a-y.a);
  const box = el("div","text"); let pos = 0;
  for (const s of spans){ if (s.a < pos) continue;
    box.appendChild(document.createTextNode(text.slice(pos, s.a)));
    const m = el("mark", s.f.uncertain ? "uncertain" : s.f.severity, text.slice(s.a, s.b)); m.title = s.f.category + " (" + s.f.severity + ")"; box.appendChild(m); pos = s.b; }
  box.appendChild(document.createTextNode(text.slice(pos))); return box;
}
function keep(i){
  const lang=$("lang").value, cat=$("cat").value, sev=$("sev").value, chk=$("chk").value, show=$("show").value, q=$("q").value.toLowerCase();
  if (lang && i.lang!==lang) return false;
  const fl = i.flags.filter(f=>(!cat||f.category===cat)&&(!sev||f.severity===sev)&&(!chk||f.checks.includes(chk)));
  if (show==="flagged" && !fl.some(f=>!f.uncertain)) return false;
  if (show==="uncertain" && !fl.some(f=>f.uncertain)) return false;
  if (show==="failed" && !i.failed_checks.length) return false;
  if ((cat||sev||chk) && !fl.length) return false;
  return !q || (i.id+" "+i.source+" "+i.translation).toLowerCase().includes(q);
}
function draw(){
  const list=$("list"); list.replaceChildren(); const shown=items.filter(keep);
  $("count").textContent = shown.length + " of " + items.length + " items";
  for (const i of shown.slice(0, 300)){
    const card = el("div","item");
    card.appendChild(el("div","meta", i.lang + " · " + i.id + " · MQM " + i.mqm + (i.failed_checks.length ? " · failed checks: " + i.failed_checks.join(", ") : "")));
    card.appendChild(el("div","meta","Source")); card.appendChild(highlighted(i.source, i.flags, "source_span"));
    card.appendChild(el("div","meta","Translation")); card.appendChild(highlighted(i.translation, i.flags, "span"));
    for (const f of i.flags){
      const d = el("div","flag " + (f.uncertain ? "uncertain" : f.severity));
      d.appendChild(el("span","tag", f.category)); d.appendChild(el("span","tag", f.uncertain ? "uncertain" : f.severity)); d.appendChild(el("span","tag", "conf " + f.confidence));
      d.appendChild(el("span","tag", f.checks.join("+")));
      for (const e of f.evidence) d.appendChild(el("div","", e));
      card.appendChild(d);
    }
    list.appendChild(card);
  }
  if (shown.length > 300) list.appendChild(el("p","meta","Showing the first 300; narrow the filters to see more."));
}
["lang","cat","sev","chk","show","q"].forEach(id=>$(id).addEventListener("input", draw)); draw();
</script></body></html>
"""
