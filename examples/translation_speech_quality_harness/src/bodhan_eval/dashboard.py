"""Static, self-contained HTML dashboard (no server, no network): filters by language, category,
severity, outcome and split; per-item detail with highlighted spans and evidence.
The report data is embedded as JSON; all text is HTML-escaped in the browser."""
from __future__ import annotations

import json
from pathlib import Path

_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Quality report dashboard</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#1b1f24;--mute:#5b6570;--line:#dfe3e8;--accent:#2457c5;--ok:#1a7f4b;--bad:#b3261e;--warn:#9a6700;
--minor:#fff3bf;--major:#ffd8a8;--critical:#ffa8a8;--chip:#eef1f5}
@media (prefers-color-scheme:dark){:root{--bg:#12151a;--card:#1b2027;--ink:#e8ebef;--mute:#9aa5b1;--line:#2c333c;--accent:#7aa2ff;--ok:#5ad08a;--bad:#ff8a80;--warn:#e3b341;
--minor:#5c4f12;--major:#6b4210;--critical:#7a2a2a;--chip:#262d36}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
header{padding:16px;border-bottom:1px solid var(--line);background:var(--card)}
h1{margin:0 0 4px;font-size:20px}h2{font-size:15px;margin:0 0 8px}.mute{color:var(--mute)}
main{padding:16px;max-width:1500px;margin:0 auto}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin-bottom:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}
.kpi b{display:block;font-size:24px}.kpi .st{font-size:12px;font-weight:600}.met{color:var(--ok)}.missed{color:var(--bad)}.na{color:var(--mute)}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line);vertical-align:top}th{font-size:12px;color:var(--mute);font-weight:600}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(380px,1fr));gap:12px;margin-bottom:16px}
.filters{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px}
select,input{padding:6px 8px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink);font:inherit}
input[type=search]{min-width:220px}
.layout{display:grid;grid-template-columns:minmax(420px,1fr) minmax(420px,1.3fr);gap:12px;align-items:start}
@media (max-width:980px){.layout{grid-template-columns:1fr}}
.list{max-height:70vh;overflow:auto}.row{cursor:pointer}.row:hover,.row.sel{background:var(--chip)}
.chip{display:inline-block;padding:1px 7px;border-radius:999px;background:var(--chip);font-size:12px;margin:1px 2px 1px 0}
.chip.critical{background:var(--critical)}.chip.major{background:var(--major)}.chip.minor{background:var(--minor)}
.tag{font-weight:600;font-size:12px}.t-ok{color:var(--ok)}.t-caught{color:var(--ok)}.t-missed,.t-falsealarm,.t-wrongcat{color:var(--bad)}
.detail{position:sticky;top:8px;max-height:92vh;overflow:auto}
.txt{white-space:pre-wrap;word-break:break-word;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:8px;margin:4px 0 10px}
mark{border-radius:3px;padding:0 1px;color:inherit}mark.sev-minor{background:var(--minor)}mark.sev-major{background:var(--major)}mark.sev-critical{background:var(--critical)}
.flag{border-left:4px solid var(--warn);padding:4px 8px;margin:6px 0;background:var(--bg);border-radius:4px}
.flag.critical{border-color:var(--bad)}.small{font-size:12px}
</style></head><body>
<header><h1>Quality report dashboard</h1><div class="mute" id="sub"></div></header>
<main>
<div class="kpis" id="kpis"></div>
<div class="grid2">
 <div class="card"><h2>By language</h2><div id="langs"></div></div>
 <div class="card"><h2>By category <span class="mute small" id="catnote"></span></h2><div id="cats"></div></div>
 <div class="card"><h2>Cost and judge</h2><div id="cost"></div></div>
</div>
<div class="card"><h2>Items</h2>
 <div class="filters">
  <label>Language <select id="f-lang"></select></label>
  <label>Category <select id="f-cat"></select></label>
  <label>Severity <select id="f-sev"></select></label>
  <label>Outcome <select id="f-out"></select></label>
  <label>Split <select id="f-split"></select></label>
  <input type="search" id="f-q" placeholder="search id, source or translation">
  <span class="mute" id="count"></span>
 </div>
 <div class="layout"><div class="list"><table id="tbl"><thead><tr><th>id</th><th>lang</th><th>flags</th><th>truth</th><th>outcome</th></tr></thead><tbody></tbody></table></div>
 <div class="detail card" id="detail"><span class="mute">Select an item.</span></div></div>
</div>
</main>
<script id="data" type="application/json">__DATA__</script>
<script>
const D=JSON.parse(document.getElementById('data').textContent);
const S=D.summary, items=D.items;
const $=s=>document.querySelector(s);
const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pct=x=>x==null?'n/a':(x*100).toFixed(1)+'%';
function hl(text,spans){text=text||'';const sp=(spans||[]).filter(x=>x&&x.span).map(x=>({s:Math.max(0,x.span[0]),e:Math.min(text.length,x.span[1]),sev:x.sev,cat:x.cat})).filter(x=>x.e>x.s).sort((a,b)=>a.s-b.s);
 let out='',pos=0;for(const x of sp){if(x.s<pos)continue;out+=esc(text.slice(pos,x.s))+'<mark class="sev-'+esc(x.sev)+'" title="'+esc(x.cat)+'">'+esc(text.slice(x.s,x.e))+'</mark>';pos=x.e}return out+esc(text.slice(pos))}
function outcome(it){const f=(it.flags||[]),l=it.labels;if(it.status!=='ok')return 'not evaluated';
 if(l==null)return f.length?'flagged (unlabelled)':'no flags (unlabelled)';
 if(l.length){const lc=new Set(l.map(x=>x.category||x.type));if(!f.length)return 'missed';return f.some(x=>lc.has(x.category))?'caught':'wrong category'}
 return f.length?'false alarm':'ok'}
const TAGCLS={'caught':'t-caught','ok':'t-ok','missed':'t-missed','false alarm':'t-falsealarm','wrong category':'t-wrongcat'};
items.forEach(it=>it._out=outcome(it));
function fill(sel,vals,all){$(sel).innerHTML=['<option value="">'+all+'</option>'].concat(vals.map(v=>'<option>'+esc(v)+'</option>')).join('')}
const uniq=a=>[...new Set(a)].sort();
// ---- KPIs
const m=S.calibration_metrics, sp=S.split, mt=S.metrics_tuning_set;
const T=[['detection_recall','Detection recall',.8,'min'],['false_alarm_rate','False alarms (clean items)',.1,'max'],['localization_rate','Span overlap',.7,'min'],['category_accuracy','Category accuracy',.7,'min']];
let k='';for(const [key,name,goal,kind] of T){const v=m[key],ok=v!=null&&(kind==='min'?v>=goal:v<=goal);
 k+='<div class="card kpi"><span class="mute small">'+esc(name)+'</span><b>'+pct(v)+'</b><span class="st '+(v==null?'na':ok?'met':'missed')+'">'+(v==null?'n/a':ok?'target met':'target missed')+' ('+(kind==='min'?'\u2265 ':'\u2264 ')+(goal*100)+'%)</span>'+(sp.enabled&&mt?'<div class="mute small">in-sample: '+pct(mt[key])+'</div>':'')+'</div>'}
const j=S.judge;k+='<div class="card kpi"><span class="mute small">Judge consistency</span><b>'+(j?pct(j.verdict_consistency):'n/a')+'</b><span class="st '+(j?(j.verdict_consistency>=.9?'met':'missed'):'na')+'">'+(j?(j.verdict_consistency>=.9?'target met':'target missed')+' (\u2265 90%)':'judge not run')+'</span></div>';
$('#kpis').innerHTML=k;
$('#sub').textContent=S.dataset+' \u2014 '+S.items_evaluated+' items evaluated, '+S.items_flagged+' flagged'+(sp.enabled?' \u2014 targets measured on '+sp.n_holdout+' held-out items (tuned on '+sp.n_tune+')':' \u2014 no held-out split');
// ---- tables
let h='<table><tr><th>language</th><th>items</th><th>flagged</th><th>quality (MQM)</th></tr>';
for(const [l,d] of Object.entries(S.per_language))h+='<tr><td>'+esc(l)+'</td><td>'+d.items+'</td><td>'+d.flagged_items+'</td><td>'+esc(S.mqm_quality_score_by_language[l]??'n/a')+'</td></tr>';
$('#langs').innerHTML=h+'</table>';
const pc=(S.metrics_all_labelled||m).per_category;$('#catnote').textContent='(precision / recall over all labelled items)';
h='<table><tr><th>category</th><th>flags</th><th>TP</th><th>FP</th><th>FN</th><th>precision</th><th>recall</th></tr>';
for(const c of uniq(Object.keys(pc).concat(Object.keys(S.flags_by_category)))){const d=pc[c]||{};h+='<tr><td>'+esc(c)+'</td><td>'+(S.flags_by_category[c]||0)+'</td><td>'+(d.tp??'')+'</td><td>'+(d.fp??'')+'</td><td>'+(d.fn??'')+'</td><td>'+pct(d.precision)+'</td><td>'+pct(d.recall)+'</td></tr>'}
$('#cats').innerHTML=h+'</table>';
const c=S.cost;h='<table>'+Object.entries(c.uncached_cost_inr).map(([a,b])=>'<tr><td>'+esc(a)+'</td><td>\u20b9'+b+'</td><td class="mute">'+(c.calls[a]||0)+' calls</td></tr>').join('')+'<tr><td><b>total (uncached)</b></td><td><b>\u20b9'+c.uncached_total_inr+'</b></td><td class="mute">rerun from cache: \u20b90</td></tr></table>';
if(j)h+='<p class="small">Judge: '+j.items_judged+' items \u00d7 '+j.runs_per_item+' runs; verdict consistency '+pct(j.verdict_consistency)+', category consistency '+pct(j.category_consistency)+'; '+j.parse_failures+' unparseable, '+j.ungrounded_quotes_dropped+' ungrounded quotes dropped.</p>';
$('#cost').innerHTML=h;
// ---- filters
fill('#f-lang',uniq(items.map(i=>i.lang)),'all languages');
fill('#f-cat',uniq(items.flatMap(i=>(i.flags||[]).map(f=>f.category))),'all categories');
fill('#f-sev',['critical','major','minor'],'all severities');
fill('#f-out',['flagged (unlabelled)','caught','wrong category','missed','false alarm','ok'],'all outcomes');
fill('#f-split',['holdout','tune'],'all items');
function visible(){const l=$('#f-lang').value,c=$('#f-cat').value,s=$('#f-sev').value,o=$('#f-out').value,p=$('#f-split').value,q=$('#f-q').value.toLowerCase();
 return items.filter(i=>(!l||i.lang===l)&&(!c||(i.flags||[]).some(f=>f.category===c))&&(!s||(i.flags||[]).some(f=>f.severity===s))&&(!o||i._out===o)&&(!p||i.split===p)&&
  (!q||(i.id+' '+i.source+' '+(i.translation||'')).toLowerCase().includes(q)))}
let sel=null;
function render(){const v=visible();$('#count').textContent=v.length+' of '+items.length+' items';
 $('#tbl tbody').innerHTML=v.map(i=>'<tr class="row'+(i.id===sel?' sel':'')+'" data-id="'+esc(i.id)+'"><td>'+esc(i.id)+'</td><td>'+esc(i.lang)+'</td><td>'+(i.flags||[]).map(f=>'<span class="chip '+esc(f.severity)+'">'+esc(f.category)+'</span>').join('')+'</td><td>'+((i.labels||[]).map(l=>'<span class="chip">'+esc(l.category||l.type)+'</span>').join('')||(i.labels==null?'<span class="mute">?</span>':'<span class="mute">clean</span>'))+'</td><td class="tag '+(TAGCLS[i._out]||'')+'">'+esc(i._out)+'</td></tr>').join('');
 document.querySelectorAll('.row').forEach(r=>r.onclick=()=>{sel=r.dataset.id;render();show(sel)})}
function show(id){const i=items.find(x=>x.id===id);if(!i)return;
 const ss=[],ts=[];(i.flags||[]).forEach(f=>{if(f.span_source)ss.push({span:f.span_source,sev:f.severity,cat:f.category});if(f.span_translation)ts.push({span:f.span_translation,sev:f.severity,cat:f.category})});
 (i.suppressed||[]).forEach(f=>{});
 let h='<h2>'+esc(i.id)+' <span class="mute">('+esc(i.lang)+(i.split?', '+esc(i.split):'')+')</span> <span class="tag '+(TAGCLS[i._out]||'')+'">'+esc(i._out)+'</span></h2>';
 h+='<div class="small mute">Source (highlights = flagged spans)</div><div class="txt">'+hl(i.source,ss)+'</div>';
 h+='<div class="small mute">Translation</div><div class="txt">'+hl(i.translation,ts)+'</div>';
 if(i.signals&&i.signals.text&&i.signals.text.back_translation)h+='<div class="small mute">Back-translation (noisy checker output)</div><div class="txt">'+esc(i.signals.text.back_translation)+'</div>';
 h+='<h2>Flags</h2>'+((i.flags||[]).map(f=>'<div class="flag '+esc(f.severity)+'"><b>'+esc(f.category)+'</b> <span class="chip '+esc(f.severity)+'">'+esc(f.severity)+'</span> <span class="mute small">checks: '+esc((f.checks||[]).join(', '))+' \u00b7 confidence '+esc(f.confidence)+'</span><div>'+esc(f.evidence)+'</div></div>').join('')||'<div class="mute">No flags.</div>');
 if((i.suppressed||[]).length)h+='<h2>Suppressed (weak signals the harness refused to flag)</h2>'+i.suppressed.map(s=>'<div class="flag"><b>'+esc(s.category)+'</b> <span class="mute small">'+esc(s.check)+'</span><div class="small">'+esc(s.evidence)+'</div><div class="small mute">reason: '+esc(s.reason)+'</div></div>').join('');
 h+='<h2>Truth label</h2>'+(i.labels==null?'<span class="mute">unlabelled</span>':(i.labels.length?i.labels.map(l=>'<span class="chip">'+esc(l.category||l.type)+'</span><span class="mute small"> '+esc(l.label_source||'')+'</span>').join(' '):'<span class="mute">clean</span>'));
 const sp=i.signals&&i.signals.speech;if(sp)h+='<h2>Speech</h2><div class="small">duration '+esc(sp.audio&&sp.audio.duration_s)+'s \u00b7 CER '+esc(sp.cer)+' \u00b7 words missing '+pct(sp.deleted_frac)+' \u00b7 max silence '+esc(sp.audio&&sp.audio.max_internal_silence_s)+'s</div>'+(sp.transcript?'<div class="txt">'+esc(sp.transcript)+'</div>':'');
 if((i.errors||[]).length)h+='<h2>Stage errors</h2>'+i.errors.map(e=>'<div class="small missed">'+esc(e.stage)+': '+esc(e.error)+'</div>').join('');
 h+='<div class="small mute" style="margin-top:8px">checks run: '+esc((i.checks_run||[]).join(', '))+'</div>';
 $('#detail').innerHTML=h}
['#f-lang','#f-cat','#f-sev','#f-out','#f-split'].forEach(s=>$(s).onchange=render);$('#f-q').oninput=render;
render();
function fromHash(){const h=decodeURIComponent(location.hash.slice(1));if(h&&items.some(i=>i.id===h)){sel=h;render();show(h)}}
window.addEventListener('hashchange',fromHash);fromHash();
</script></body></html>
"""


def write_dashboard(rows: list[dict], summary: dict, path: str | Path) -> None:
    keep = {k: summary[k] for k in (
        "dataset", "items_evaluated", "items_flagged", "per_language", "flags_by_category", "mqm_quality_score_by_language",
        "calibration_metrics", "metrics_tuning_set", "metrics_all_labelled", "split", "judge", "cost")}
    blob = json.dumps({"summary": keep, "items": rows}, ensure_ascii=False, sort_keys=True)
    # keep the JSON from ever closing the <script> element or breaking out of it
    blob = blob.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    Path(path).write_text(_TEMPLATE.replace("__DATA__", blob), encoding="utf-8")
