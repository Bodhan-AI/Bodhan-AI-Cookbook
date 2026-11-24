"""A single-file static HTML dashboard with filters by language, category, severity and status."""

from __future__ import annotations

import json

from ..models import ItemResult

TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Quality Harness Report</title>
<style>
:root{--bg:#fafafa;--fg:#1d1d1f;--muted:#6b6b70;--card:#fff;--line:#e3e3e6;--crit:#b42318;--major:#b54708;--minor:#4b5563;--ok:#067647}
@media (prefers-color-scheme: dark){:root{--bg:#141416;--fg:#ececef;--muted:#a0a0a8;--card:#1d1d21;--line:#2e2e34;--crit:#f97066;--major:#fdb022;--minor:#c4c4cc;--ok:#47cd89}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,sans-serif}
header{padding:16px;border-bottom:1px solid var(--line)}h1{font-size:18px;margin:0 0 4px}.muted{color:var(--muted)}
.bar{display:flex;flex-wrap:wrap;gap:8px;padding:12px 16px;position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line)}
select,input{background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:6px 8px;font:inherit}
input{flex:1;min-width:160px}main{padding:16px;max-width:1100px;margin:auto}
.item{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px;margin-bottom:10px}
.item h3{margin:0 0 6px;font-size:14px}.tag{display:inline-block;padding:0 6px;border-radius:4px;border:1px solid var(--line);font-size:12px;margin-right:4px}
.critical{color:var(--crit)}.major{color:var(--major)}.minor{color:var(--minor)}.clean{color:var(--ok)}
.text{white-space:pre-wrap;word-break:break-word;font-size:13px;margin:4px 0}mark{background:#fde68a;color:#111;border-radius:2px}
.flag{border-top:1px dashed var(--line);padding-top:6px;margin-top:6px}ul{margin:4px 0;padding-left:18px}
.stats{display:flex;gap:16px;flex-wrap:wrap}.stat b{font-size:18px;display:block}
</style></head><body>
<header><h1>Translation and speech quality report</h1><div class="stats" id="stats"></div></header>
<div class="bar">
<select id="lang"><option value="">All languages</option></select>
<select id="cat"><option value="">All categories</option></select>
<select id="sev"><option value="">All severities</option><option>critical</option><option>major</option><option>minor</option></select>
<select id="status"><option value="">All items</option><option value="flagged">Flagged</option><option value="clean">Clean</option><option value="failed">Failed or skipped</option></select>
<input id="q" placeholder="Search id, text or evidence">
</div><main id="list"></main>
<script id="data" type="application/json">__DATA__</script>
<script>
const items = JSON.parse(document.getElementById('data').textContent);
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function fill(sel, values){ [...new Set(values)].sort().forEach(v => { const o = document.createElement('option'); o.textContent = v; sel.appendChild(o); }); }
fill($('lang'), items.map(i => i.lang));
fill($('cat'), items.flatMap(i => i.flags.map(f => f.category)));
function highlight(text, span){ if(!text) return ''; if(!span) return esc(text); return esc(text.slice(0, span[0])) + '<mark>' + esc(text.slice(span[0], span[1])) + '</mark>' + esc(text.slice(span[1])); }
function render(){
  const lang = $('lang').value, cat = $('cat').value, sev = $('sev').value, st = $('status').value, q = $('q').value.toLowerCase();
  const shown = items.filter(i => {
    if (lang && i.lang !== lang) return false;
    const flags = i.flags.filter(f => (!cat || f.category === cat) && (!sev || f.severity === sev));
    if ((cat || sev) && !flags.length) return false;
    if (st === 'flagged' && !i.flags.length) return false;
    if (st === 'clean' && (i.flags.length || i.status !== 'scored')) return false;
    if (st === 'failed' && i.status === 'scored') return false;
    if (q && !JSON.stringify(i).toLowerCase().includes(q)) return false;
    return true;
  });
  $('stats').innerHTML = `<div class="stat"><b>${shown.length}</b>items shown</div><div class="stat"><b>${shown.filter(i=>i.flags.length).length}</b>flagged</div><div class="stat"><b>${shown.reduce((a,i)=>a+i.flags.length,0)}</b>flags</div>`;
  $('list').innerHTML = shown.slice(0, 500).map(i => {
    const flags = i.flags.filter(f => (!cat || f.category === cat) && (!sev || f.severity === sev));
    const head = i.status !== 'scored' ? `<span class="tag">${esc(i.status)}</span> ${esc(i.error)}` : (i.flags.length ? '' : '<span class="tag clean">clean</span>');
    const first = flags[0];
    return `<div class="item"><h3>${esc(i.id)} <span class="tag">${esc(i.lang)}</span><span class="tag">quality ${i.quality_score ?? 'n/a'}</span>${head}</h3>
      <div class="text"><span class="muted">source:</span> ${highlight(i.source, first && first.source_span)}</div>
      <div class="text"><span class="muted">translation:</span> ${highlight(i.translation, first && first.span)}</div>
      ${i.back_translation ? `<div class="text muted">back-translation: ${esc(i.back_translation)}</div>` : ''}
      ${i.transcript ? `<div class="text muted">transcript: ${esc(i.transcript)}</div>` : ''}
      ${flags.map(f => `<div class="flag"><span class="tag ${f.severity}">${f.severity}</span><b>${esc(f.category)}</b> confidence ${f.confidence.toFixed(2)}${f.audio_span ? ` audio ${f.audio_span[0].toFixed(1)}-${f.audio_span[1].toFixed(1)}s` : ''}
        <ul>${f.evidence.map(e => `<li>${esc(e)}</li>`).join('')}${f.judge ? `<li class="muted">judge: ${esc(f.judge)}</li>` : ''}</ul></div>`).join('')}
    </div>`;
  }).join('') + (shown.length > 500 ? '<p class="muted">Showing first 500 items.</p>' : '');
}
['lang','cat','sev','status','q'].forEach(id => $(id).addEventListener('input', render));
render();
</script></body></html>
"""


def render(results: list[ItemResult], sources: dict[str, str]) -> str:
    rows = []
    for r in results:
        rows.append(
            {
                "id": r.id,
                "lang": r.lang,
                "status": r.status,
                "error": r.error,
                "quality_score": r.quality_score,
                "source": sources.get(r.id, ""),
                "translation": r.translation,
                "back_translation": r.back_translation,
                "transcript": r.transcript,
                "flags": [
                    f.model_dump(mode="json", include={"category", "severity", "confidence", "span", "source_span", "audio_span", "evidence", "judge"})
                    for f in r.flags
                ],
            }
        )
    data = json.dumps(rows, ensure_ascii=False, sort_keys=True).replace("</", "<\\/")
    return TEMPLATE.replace("__DATA__", data)
