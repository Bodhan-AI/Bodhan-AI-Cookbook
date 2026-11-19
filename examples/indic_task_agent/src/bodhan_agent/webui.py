"""A read-only run viewer on the standard library: the trace as a plain timeline.

    python -m bodhan_agent.webui ./out/demo      # a folder of runs, or one run folder

Open http://127.0.0.1:8766/ and leave it open: it follows the runs live as a timeline of
what the agent did and why (plan, assumptions, each tool call with cost and time, skipped
steps, checks, the final status). It never writes and never starts a run, so it cannot change
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
 body{margin:0;background:#14161c;color:#e6e8ee;font:16px/1.55 system-ui,Segoe UI,sans-serif}
 #wrap{display:grid;grid-template-columns:230px minmax(0,1fr);height:100vh}
 nav{border-right:1px solid #2a2e3a;padding:16px;overflow-y:auto;background:#0f1116}
 nav h1{font-size:13px;color:#8b93a7;margin:0 0 12px;font-weight:600}
 .run{padding:8px 10px;margin:0 0 4px;border-radius:6px;cursor:pointer;overflow-wrap:anywhere}
 .run:hover{background:#1a1e29}.run.on{background:#232a3d}
 .run small{display:block;color:#8b93a7}
 main{overflow-y:auto;padding:28px 36px 80px;min-width:0}
 .inner{max-width:820px;margin:0 auto}
 *{overflow-wrap:anywhere}
 h1.task{font-size:21px;margin:0 0 4px}
 .top{color:#aab1c5;margin-bottom:22px}
 .badge{font-weight:700;padding:1px 10px;border-radius:99px;font-size:13px;margin-right:8px}
 .completed{background:#1c4a2e;color:#8ff0b0}.partial{background:#5a4a14;color:#ffd979}
 .failed{background:#5a1f24;color:#ff9aa2}.running{background:#1f3a5a;color:#9ac9ff}
 .ev{display:grid;grid-template-columns:26px minmax(0,1fr);padding:7px 0;border-bottom:1px solid #1f2330}
 .ev .ic{color:#8b93a7;text-align:center}
 .ev.think .ic{color:#9ac9ff}.ev.ok .ic{color:#6fdc98}.ev.bad .ic{color:#ff8a93}.ev.skip .ic{color:#8b93a7}
 .ev .t{font-weight:600}.ev .d{color:#aab1c5}
 .ev .n{color:#8b93a7;font-size:14px}
 .ev.skip{opacity:.7}
 .muted{color:#8b93a7}
</style></head><body><div id="wrap">
<nav><h1>Task runs</h1><div id="runs"><span class="muted">no runs yet</span></div></nav>
<main><div class="inner" id="main"><span class="muted">Waiting for a run. Start one in the terminal.</span></div></main></div>
<script>
const $=(t,c,x)=>{const n=document.createElement(t);if(c)n.className=c;if(x!==undefined)n.textContent=x;return n};
let current=new URLSearchParams(location.search).get('run');
async function g(u,asJson){try{const r=await fetch(u,{cache:'no-store'});if(!r.ok)return null;return asJson?await r.json():await r.text()}catch(_){return null}}
const rs=n=>'Rs '+Number(n||0).toFixed(2);
const sec=ms=>((ms||0)/1000).toFixed(1)+' s';
function row(kind,icon,title,detail,note){
  const r=$('div','ev '+kind);r.append($('div','ic',icon));
  const b=$('div');b.append($('div','t',title));
  if(detail)b.append($('div','d',detail));
  if(note)b.append($('div','n',note));
  r.append(b);return r}
function render(e,plan){
  switch(e.type){
    case 'intake':{const f=(e.inventory||[]).map(i=>i.name+' ('+i.kind+(i.duration_s?', '+Math.round(i.duration_s)+' s':'')+(i.pages?', '+i.pages+' pages':'')+')').join(', ');
      return row('think','•','Looked at the input files for free',f||'no files')}
    case 'plan':
      if(e.feasible===false)return row('bad','✗','Decided it cannot be done',e.refusal||'');
      return row('think','•','Made a plan',e.understanding||'',(e.steps||[]).length+' steps')
    case 'estimate':return row('think','•','Worked out the cost before spending anything','Estimated '+rs(e.estimated_cost_inr)+' of a '+rs(e.max_cost_inr)+' limit');
    case 'replan':return row('think','↻','Changed the plan',e.note||e.reason||'');
    case 'plan_repair':return row('think','↻','Fixed an invalid plan',(e.error||'').slice(0,160));
    case 'resume':return row('think','↻','Resumed an earlier run','Reusing its plan and finished steps');
    case 'llm_call':return row('think','•','Asked the language model: '+(e.purpose||'').replace('_',' '),'',sec(e.latency_ms));
    case 'fit':return row('think','•','Checked speech length before buying speech','Predicted '+e.predicted_s+' s for '+e.wanted_s+' s wanted');
    case 'tool_call':return row('think','▶',e.step+'  '+e.tool,e.intent||'','reserved '+rs(e.reserved_inr));
    case 'tool_result':{const s=e.summary||{};const bits=[];
      if(s.duration_s)bits.push(Math.round(s.duration_s)+' s of audio');if(s.page_count)bits.push(s.page_count+' pages');
      if(s.chars)bits.push(s.chars+' characters');if(s.output_chars&&!s.chars)bits.push(s.output_chars+' characters out');
      if(s.language_hint)bits.push('language '+s.language_hint);if(s.chunks)bits.push(s.chunks+' windows');if(s.clips)bits.push(s.clips+' speech clips');
      if(s.warnings&&s.warnings.length)bits.push('warnings: '+s.warnings.join(', '));
      return row(e.ok?'ok':'bad',e.ok?'✓':'✗',e.step+' '+(e.ok?'done':'failed'),bits.join(' · '),rs(e.cost_inr)+' · '+sec(e.latency_ms)+(e.cache_hit?' · from cache':''))}
    case 'step_skipped':return row('skip','–',e.step+' skipped',e.reason||'','no cost');
    case 'step_reused':return row('ok','↺',e.step+' reused',e.reason||'','no cost');
    case 'step_failed':return row('bad','✗',e.step+' failed',(e.error&&e.error.message)||'');
    case 'verify':{const k=e.result==='passed'?'ok':e.result==='failed'?'bad':'skip';
      return row(k,e.result==='passed'?'✓':e.result==='failed'?'✗':'–','Check: '+e.check+' → '+e.result,e.detail||'',(e.artifact||'').split('/').pop())}
    case 'error':return row('bad','✗','Stopped: '+(e.code||'error'),e.message||'');
    case 'stop':return row(e.status==='completed'?'ok':'bad','■','Finished: '+e.status,'Total '+rs(e.cost_inr)+' in '+e.duration_s+' s');
    default:return null}}
async function show(run){
  const raw=await g('/run/'+encodeURIComponent(run)+'/trace.jsonl')||'';
  const events=raw.split('\\n').filter(Boolean).map(l=>{try{return JSON.parse(l)}catch(_){return null}}).filter(Boolean);
  const report=await g('/run/'+encodeURIComponent(run)+'/report.json',true);
  const stop=[...events].reverse().find(e=>e.type==='stop');
  const status=report?report.status:(stop?stop.status:'running');
  const main=document.getElementById('main'),keep=main.parentElement.scrollTop;main.replaceChildren();
  main.append($('h1','task',report?report.task:run));
  const spent=report?report.budget.cost_inr:events.filter(e=>e.type==='tool_result').reduce((s,e)=>s+(e.cost_inr||0),0);
  const top=$('div','top');top.append($('span','badge '+status,status.toUpperCase()),document.createTextNode('spent '+rs(spent)+(report?' of '+rs(report.budget.max_cost_inr)+' · '+report.duration_s+' s':'')));
  main.append(top);
  const plan=[...events].reverse().find(e=>e.type==='plan');
  events.forEach(e=>{const r=render(e,plan);if(r)main.append(r);
    if(e.type==='plan'&&(e.assumptions||[]).length)e.assumptions.forEach(a=>main.append(row('think','•','Assumed',a.text)))});
  main.parentElement.scrollTop=keep}
async function tick(){
  const runs=await g('/api/runs',true)||[];
  const nav=document.getElementById('runs');nav.replaceChildren();
  if(!runs.length)nav.append($('span','muted','no runs yet'));
  if(!current&&runs.length)current=runs[0].name;
  runs.forEach(r=>{const d=$('div','run'+(r.name===current?' on':''));d.append($('div',null,r.name),$('small',null,r.status+(r.cost!==null?' · '+rs(r.cost):'')));d.onclick=()=>{current=r.name;history.replaceState(null,'','?run='+encodeURIComponent(r.name));tick()};nav.append(d)});
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
