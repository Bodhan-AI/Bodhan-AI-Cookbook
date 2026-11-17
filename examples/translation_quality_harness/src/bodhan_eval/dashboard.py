"""Static, self-contained HTML dashboard (no network, no build step): headline metrics, per-language
table, and every flag with its highlighted span, filterable by language, category, severity and check."""

from __future__ import annotations

import json
from pathlib import Path

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Quality dashboard</title>
<style>
:root{--bg:#fafaf9;--fg:#1c1917;--muted:#78716c;--card:#fff;--line:#e7e5e4;--mark:#fde68a;--minor:#a8a29e;--major:#d97706;--critical:#dc2626}
@media (prefers-color-scheme:dark){:root{--bg:#1c1917;--fg:#f5f5f4;--muted:#a8a29e;--card:#292524;--line:#44403c;--mark:#854d0e}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,sans-serif}
main{max-width:1200px;margin:0 auto;padding:24px 16px}h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 8px}
.muted{color:var(--muted)}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-top:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px}.card b{display:block;font-size:22px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}th{font-weight:600;font-size:12px;color:var(--muted)}
.wrap{overflow-x:auto}.filters{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}select,input{background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:6px 8px;font:inherit}
mark{background:var(--mark);color:inherit;border-radius:3px;padding:0 2px}.sev{font-weight:600}.minor{color:var(--minor)}.major{color:var(--major)}.critical{color:var(--critical)}
.ev{font-size:12px;color:var(--muted);max-width:380px;word-break:break-word}.text{max-width:420px}.ok{color:#16a34a}.no{color:var(--critical)}
</style></head><body><main>
<h1>Translation &amp; speech quality</h1><div class="muted" id="sub"></div>
<div class="cards" id="cards"></div>
<h2>Per language</h2><div class="wrap"><table id="langs"></table></div>
<h2>Quality targets</h2><div class="wrap"><table id="targets"></table></div>
<h2>Flags</h2>
<div class="filters"><select id="f-lang"></select><select id="f-cat"></select><select id="f-sev"></select><select id="f-check"></select>
<input id="f-q" placeholder="search id or evidence"></div>
<div class="muted" id="count"></div><div class="wrap"><table id="flags"></table></div>
</main>
<script type="application/json" id="data">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const pct = v => v == null ? '–' : (100 * v).toFixed(1) + '%';
const S = D.summary;
document.getElementById('sub').textContent = `${S.items_evaluated} items · ${S.flagged_items} flagged · model cost ₹${S.model_cost_inr}`;
const cal = D.calibration || {};
const cards = [['Items', S.items_evaluated], ['Flagged', S.flagged_items], ['Recall', pct(cal.detection_recall)],
  ['False alarms', pct(cal.false_alarm_rate)], ['Localization', pct(cal.localization)], ['Category acc.', pct(cal.category_accuracy)]];
document.getElementById('cards').innerHTML = cards.map(([k, v]) => `<div class="card"><span class="muted">${k}</span><b>${v}</b></div>`).join('');
document.getElementById('langs').innerHTML = '<tr><th>lang</th><th>items</th><th>flagged</th><th>MQM</th><th>BT chrF++</th><th>speech checked</th><th>top categories</th></tr>' +
  Object.entries(S.per_language).map(([l, s]) => `<tr><td>${l}</td><td>${s.items}</td><td>${s.flagged_items}</td><td>${s.mqm_score ?? '–'}</td>
  <td>${s.mean_backtranslation_chrf ?? '–'}</td><td>${s.speech_checked}</td><td>${Object.entries(s.flags_by_category).sort((a,b)=>b[1]-a[1]).slice(0,3).map(([c,n])=>`${c} ${n}`).join(', ') || '–'}</td></tr>`).join('');
document.getElementById('targets').innerHTML = '<tr><th>metric</th><th>value</th><th>target</th><th>met</th></tr>' +
  (cal.targets || []).map(t => `<tr><td>${t.metric}</td><td>${t.value ?? '–'}</td><td>${esc(t.target)}</td><td class="${t.met ? 'ok' : t.met === false ? 'no' : ''}">${t.met == null ? '–' : t.met ? 'yes' : 'no'}</td></tr>`).join('');
const rows = D.items.flatMap(it => it.flags.map(f => ({it, f})));
const opts = (id, label, vals) => { document.getElementById(id).innerHTML = `<option value="">${label}: all</option>` + [...new Set(vals)].sort().map(v => `<option>${esc(v)}</option>`).join(''); };
opts('f-lang', 'language', rows.map(r => r.it.lang)); opts('f-cat', 'category', rows.map(r => r.f.category));
opts('f-sev', 'severity', rows.map(r => r.f.severity)); opts('f-check', 'check', rows.flatMap(r => r.f.checks));
function highlight(text, span) {
  if (!text) return '';
  if (!span) return esc(text);
  const [a, b] = span; return esc(text.slice(0, a)) + '<mark>' + esc(text.slice(a, Math.max(b, a + 1))) + '</mark>' + esc(text.slice(Math.max(b, a + 1)));
}
function render() {
  const v = id => document.getElementById(id).value; const q = v('f-q').toLowerCase();
  const shown = rows.filter(({it, f}) => (!v('f-lang') || it.lang === v('f-lang')) && (!v('f-cat') || f.category === v('f-cat')) &&
    (!v('f-sev') || f.severity === v('f-sev')) && (!v('f-check') || f.checks.includes(v('f-check'))) &&
    (!q || it.id.toLowerCase().includes(q) || JSON.stringify(f.evidence).toLowerCase().includes(q)));
  document.getElementById('count').textContent = `${shown.length} of ${rows.length} flags`;
  document.getElementById('flags').innerHTML = '<tr><th>item</th><th>category</th><th>severity</th><th>score</th><th>where (translation)</th><th>evidence</th></tr>' +
    shown.map(({it, f}) => `<tr><td>${esc(it.id)}<div class="muted">${it.lang}</div></td><td>${f.category}<div class="muted">${f.checks.join(', ')}</div></td>
    <td class="sev ${f.severity}">${f.severity}</td><td>${f.score}</td><td class="text" dir="auto">${highlight(it.translation, f.span)}</td>
    <td class="ev">${f.evidence.map(e => esc(Object.entries(e).filter(([k]) => k !== 'back_translation').map(([k, x]) => `${k}: ${typeof x === 'object' ? JSON.stringify(x) : x}`).join(' · '))).join('<br>')}</td></tr>`).join('');
}
['f-lang','f-cat','f-sev','f-check','f-q'].forEach(id => document.getElementById(id).addEventListener('input', render)); render();
</script></body></html>
"""


def write_dashboard(path: Path, records: list[dict], summary: dict, calibration: dict | None) -> None:
    items = [{"id": r["id"], "lang": r["lang"], "translation": r["translation"], "flags": r["flags"]} for r in records if r["flags"]]
    data = {"summary": summary, "calibration": calibration if calibration and calibration.get("labelled_items") else None, "items": items}
    blob = json.dumps(data, ensure_ascii=False, sort_keys=True).replace("</", "<\\/")
    path.write_text(PAGE.replace("__DATA__", blob), encoding="utf-8")
