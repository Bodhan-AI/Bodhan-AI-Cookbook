"""A read-only run viewer on the standard library.

    python -m bodhan_agent.webui ./out/demo      # a folder of runs, or one run folder

Open http://127.0.0.1:8766/ and leave it open: it follows the runs live (plan, every
tool call with cost and latency, verification checks, artifacts you can play or read,
and the final result.md). It never writes and never starts a run, so it cannot change
what a run costs or produces. Only files inside the chosen folder are served.
"""
from __future__ import annotations

import json
import mimetypes
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Bodhan task runs</title>
<style>
 :root{color-scheme:dark}
 *{box-sizing:border-box}
 body{font:15px/1.55 system-ui,Segoe UI,sans-serif;margin:0;background:#12141a;color:#e6e8ee;overflow-x:hidden}
 code,pre,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px}
 #wrap{display:grid;grid-template-columns:240px minmax(0,1fr);height:100vh}
 nav{border-right:1px solid #2a2e3a;padding:14px;background:#0e1016;overflow-y:auto}
 nav h1{font-size:14px;margin:0 0 12px;color:#aab1c5}
 .run{padding:8px 10px;margin:0 0 6px;border-radius:6px;cursor:pointer;border:1px solid transparent;overflow-wrap:anywhere}
 .run:hover{background:#1a1e29}.run.on{background:#1f2535;border-color:#3a4a7a}
 .run small{display:block;color:#8b93a7}
 main{padding:22px 32px 60px;overflow-y:auto;min-width:0}
 .inner{max-width:960px;margin:0 auto}
 main *{overflow-wrap:anywhere;min-width:0}
 h2{font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:#8b93a7;margin:28px 0 10px;border-bottom:1px solid #232838;padding-bottom:6px}
 .badge{display:inline-block;padding:2px 12px;border-radius:99px;font-weight:700;font-size:12px;letter-spacing:.05em}
 .completed{background:#1c4a2e;color:#8ff0b0}.partial{background:#5a4a14;color:#ffd979}
 .failed{background:#5a1f24;color:#ff9aa2}.running{background:#1f3a5a;color:#9ac9ff}
 .task{font-size:19px;font-weight:600;margin:10px 0 6px}
 .meta{color:#aab1c5}
 .bar{height:6px;background:#232838;border-radius:4px;overflow:hidden;margin:8px 0;max-width:360px}
 .bar i{display:block;height:100%;background:#4f8cff}
 .step{border:1px solid #2a2e3a;border-radius:8px;padding:10px 14px;margin:0 0 8px;background:#161923}
 .step.ok{border-left:4px solid #3fbf6f}.step.bad{border-left:4px solid #e5535d}
 .step.skip{border-left:4px solid #77809a;opacity:.7}.step.run{border-left:4px solid #4f8cff}
 .step b{color:#9ac9ff}.step .why{display:block;margin:3px 0}.step .nums{color:#8b93a7;font-size:13px}
 details{margin-top:4px}summary{cursor:pointer;color:#8b93a7;font-size:13px}
 details pre{margin:6px 0 0}
 .tablewrap{overflow-x:auto}
 table{border-collapse:collapse;width:100%;table-layout:fixed}
 td,th{border-bottom:1px solid #232838;padding:6px 8px;text-align:left;vertical-align:top;font-size:13px}
 th{color:#8b93a7;font-weight:600}
 th:nth-child(1){width:30%}th:nth-child(2){width:14%}th:nth-child(3){width:11%}
 .passed{color:#8ff0b0}.warning{color:#ffd979}.skipped{color:#9aa3b8}.failedc{color:#ff9aa2}
 pre{background:#0e1016;border:1px solid #2a2e3a;border-radius:6px;padding:12px;white-space:pre-wrap;word-break:break-word;overflow:auto;max-height:340px;margin:6px 0}
 .art{border:1px solid #2a2e3a;border-radius:8px;padding:10px 14px;margin:0 0 10px;background:#161923}
 .art audio{width:100%;margin:8px 0 2px}
 a{color:#8bb8ff}
 .muted{color:#8b93a7}
</style></head><body><div id="wrap">
<nav><h1>Task runs</h1><div id="runs"><span class="muted">no runs yet</span></div></nav>
<main><div class="inner" id="main"><span class="muted">Waiting for a run. Start one in the terminal.</span></div></main></div>
<script>
const $=(t,c,x)=>{const n=document.createElement(t);if(c)n.className=c;if(x!==undefined)n.textContent=x;return n};
let current=new URLSearchParams(location.search).get('run');
async function j(u){try{const r=await fetch(u,{cache:'no-store'});return r.ok?await r.json():null}catch(_){return null}}
async function t(u){try{const r=await fetch(u,{cache:'no-store'});return r.ok?await r.text():null}catch(_){return null}}
const rs=n=>Number(n||0).toFixed(2);
function argsText(a){return Object.entries(a||{}).filter(([k])=>k!=='out_dir'&&k!=='max_cost_inr').map(([k,v])=>k+'='+(typeof v==='string'?(v.startsWith('/')?v.split('/').pop():v):JSON.stringify(v))).join('   ')}
function steps(events,plan){
  const box=$('div'),byId={};
  events.forEach(e=>{if(e.step){byId[e.step]=byId[e.step]||{};
    if(e.type==='tool_call')byId[e.step].call=e;
    if(e.type==='tool_result')byId[e.step].res=e;
    if(e.type==='step_skipped')byId[e.step].skip=e;
    if(e.type==='step_failed')byId[e.step].fail=e;
    if(e.type==='step_reused')byId[e.step].reused=e}});
  (plan?plan.steps:[]).forEach(s=>{
    const st=byId[s.id]||{};let cls='step',tail='waiting',nums='';
    if(st.skip){cls+=' skip';tail='skipped: '+(st.skip.reason||'')}
    else if(st.fail){cls+=' bad';tail='FAILED '+JSON.stringify(st.fail.error&&st.fail.error.code||'')}
    else if(st.res){cls+=st.res.ok?' ok':' bad';tail=st.res.ok?'done':'failed';nums='Rs '+(st.res.cost_inr||0)+' · '+((st.res.latency_ms||0)/1000).toFixed(1)+' s'+(st.res.cache_hit?' · from cache':'')}
    else if(st.call){cls+=' run';tail='running…';nums='reserved Rs '+(st.call.reserved_inr||0)}
    else if(st.reused){cls+=' ok';tail='reused from earlier run'}
    const d=$('div',cls);const h=$('div');h.append($('b',null,s.id+'  '+s.tool),$('span','nums','   '+tail+(nums?'  ·  '+nums:'')));
    d.append(h,$('span','why',s.intent||''));
    const w=s.when?'only if '+s.when.ref+' = '+JSON.stringify(s.when.equals):'';
    const f=s.fit?'fit to '+s.fit.seconds+' s of speech':'';
    const a=[argsText(s.args),w,f].filter(Boolean).join('   ');
    if(a.length<=140){if(a)d.append($('span','muted mono',a))}
    else{const de=$('details');de.append($('summary',null,'arguments'),$('pre',null,a));d.append(de)}
    if(st.res&&st.res.summary&&st.res.summary.warnings&&st.res.summary.warnings.length)d.append($('span','warning','warnings: '+st.res.summary.warnings.join(', ')));
    box.append(d)});
  return box}
function checks(rows){
  if(!rows.length)return $('span','muted','no checks yet');
  const tb=$('table');const h=$('tr');['artifact','check','result','detail'].forEach(x=>h.append($('th',null,x)));tb.append(h);
  rows.forEach(c=>{const r=$('tr');r.append($('td','mono',(c.artifact||'').split('/').pop()),$('td',null,c.check),$('td',c.result==='failed'?'failedc':c.result,c.result),$('td','muted',c.detail||''));tb.append(r)});
  const w=$('div','tablewrap');w.append(tb);return w}
async function arts(run,list){
  const box=$('div');
  for(const a of list){
    const d=$('div','art');d.append($('b',null,a.name),$('span','muted','   '+a.kind+' · '+a.bytes+' bytes'+(a.detail?' · '+a.detail:'')));
    const u='/run/'+encodeURIComponent(run)+'/artifacts/'+encodeURIComponent(a.name);
    if(/\\.(mp3|wav)$/i.test(a.name)){const au=$('audio');au.controls=true;au.src=u;d.append(au,$('span','muted','AI-generated speech (Indic-Speak)'))}
    else if(/\\.(txt|md|srt|json)$/i.test(a.name)&&a.bytes<200000){const x=await t(u);if(x)d.append($('pre',null,x.slice(0,4000)+(x.length>4000?'\\n…':'')))}
    d.append($('a',null,'open file'));d.lastChild.href=u;d.lastChild.target='_blank';
    box.append(d)}
  return box.childNodes.length?box:$('span','muted','none yet')}
async function show(run){
  const main=document.getElementById('main');
  const raw=await t('/run/'+encodeURIComponent(run)+'/trace.jsonl')||'';
  const events=raw.split('\\n').filter(Boolean).map(l=>{try{return JSON.parse(l)}catch(_){return null}}).filter(Boolean);
  const report=await j('/run/'+encodeURIComponent(run)+'/report.json');
  const plan=[...events].reverse().find(e=>e.type==='plan');
  const stop=[...events].reverse().find(e=>e.type==='stop');
  const verifies=events.filter(e=>e.type==='verify');
  const status=report?report.status:(stop?stop.status:'running');
  main.replaceChildren();
  const top=$('div');top.append($('span','badge '+status,status.toUpperCase()));
  const spent=report?report.budget.cost_inr:events.filter(e=>e.type==='tool_result').reduce((s,e)=>s+(e.cost_inr||0),0);
  const cap=report?report.budget.max_cost_inr:null;
  top.append($('span','meta','   spent Rs '+rs(spent)+(cap?' of Rs '+rs(cap):'')+(report?'  ·  '+report.duration_s+' s  ·  '+report.budget.steps_used+' steps':'')));
  main.append(top);
  const intake=events.find(e=>e.type==='intake');
  main.append($('div','task',report?report.task:(run)));
  if(cap){const b=$('div','bar');const i=$('i');i.style.width=Math.min(100,spent/cap*100)+'%';b.append(i);main.append(b)}
  if(plan){main.append($('h2',null,'How the agent understood it'),$('div',null,plan.understanding||''));
    if((plan.assumptions||[]).length){const de=$('details');de.append($('summary',null,plan.assumptions.length+' assumption(s) the agent recorded'));plan.assumptions.forEach(a=>de.append($('div','muted','• '+a.text)));main.append($('h2',null,'Assumptions'),de)}
    if(plan.feasible===false)main.append($('h2',null,'Refused'),$('pre',null,plan.refusal||''))}
  const est=events.find(e=>e.type==='estimate');
  if(est)main.append($('div','meta','Estimated before spending: Rs '+rs(est.estimated_cost_inr)+' (limit Rs '+rs(est.max_cost_inr)+')'));
  main.append($('h2',null,'Steps'),plan?steps(events,plan):$('span','muted','planning…'));
  main.append($('h2',null,'Checks on the outputs'),checks(report?report.verification:verifies));
  if(report){main.append($('h2',null,'Files produced'),await arts(run,report.artifacts||[]));
    if((report.errors||[]).length){main.append($('h2',null,'Why it did not complete'));report.errors.forEach(e=>main.append($('pre',null,e.code+': '+e.message+(e.hint?'\\n'+e.hint:''))))}
    const md=await t('/run/'+encodeURIComponent(run)+'/result.md');if(md){const de=$('details');de.open=true;de.append($('summary',null,'result.md (the written report)'),$('pre',null,md));main.append($('h2',null,'Report'),de)}}
  else main.append($('h2',null,'Live trace'),$('pre',null,events.slice(-12).map(e=>e.type+(e.step?' '+e.step:'')+(e.tool?' '+e.tool:'')).join('\\n')))}
async function tick(){
  const runs=await j('/api/runs')||[];
  const nav=document.getElementById('runs');nav.replaceChildren();
  if(!runs.length)nav.append($('span','muted','no runs yet'));
  if(!current&&runs.length)current=runs[0].name;
  runs.forEach(r=>{const d=$('div','run'+(r.name===current?' on':''));d.append($('div',null,r.name),$('small',null,r.status+(r.cost!==null?' · Rs '+rs(r.cost):'')));d.onclick=()=>{current=r.name;history.replaceState(null,'','?run='+encodeURIComponent(r.name));tick()};nav.append(d)});
  if(current)await show(current)}
tick();setInterval(tick,1500);
</script></body></html>
"""


def _runs(root: Path) -> list[Path]:
    if (root / "trace.jsonl").is_file():
        return [root]
    found = [p for p in root.iterdir() if p.is_dir() and (p / "trace.jsonl").is_file()] if root.is_dir() else []
    return sorted(found, key=lambda p: (p / "trace.jsonl").stat().st_mtime, reverse=True)


def _summary(run: Path) -> dict:
    status, cost = "running", None
    report = run / "report.json"
    if report.is_file():
        try:
            data = json.loads(report.read_text(encoding="utf-8"))
            status, cost = data.get("status", "running"), data.get("budget", {}).get("cost_inr")
        except ValueError:
            pass
    return {"name": run.name, "status": status, "cost": cost}


def serve(run_dir: str, port: int = 8766, block: bool = True):
    """Serve a folder of runs (or one run); returns the server when `block` is false."""
    root = Path(run_dir).resolve()

    def resolve(parts: list[str]) -> Path | None:
        """Only files inside a run folder inside `root`, with no path tricks."""
        runs = {p.name: p for p in _runs(root)}
        if not parts or parts[0] not in runs:
            return None
        target = runs[parts[0]].joinpath(*parts[1:]).resolve()
        return target if target.is_file() and runs[parts[0]].resolve() in target.parents else None

    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: bytes, ctype: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            path = unquote(urlparse(self.path).path)
            if path in ("/", "/index.html"):
                return self._send(PAGE.encode(), "text/html; charset=utf-8")
            if path == "/api/runs":
                return self._send(json.dumps([_summary(r) for r in _runs(root)]).encode(), "application/json")
            parts = [p for p in path.split("/") if p]
            if len(parts) >= 3 and parts[0] == "run":
                allowed = parts[2] in ("trace.jsonl", "report.json", "result.md") and len(parts) == 3
                allowed = allowed or (parts[2] == "artifacts" and len(parts) == 4)
                target = resolve(parts[1:]) if allowed else None
                if target:
                    ctype = mimetypes.guess_type(target.name)[0] or "text/plain"
                    ctype += "; charset=utf-8" if ctype.startswith("text/") or ctype.endswith("json") else ""
                    return self._send(target.read_bytes(), ctype)
            self.send_error(404)

        def log_message(self, *args) -> None:
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    if not block:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd
    print(f"run viewer: http://127.0.0.1:{httpd.server_address[1]}/   (Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return httpd


if __name__ == "__main__":
    serve(sys.argv[1] if len(sys.argv) > 1 else "./out")
