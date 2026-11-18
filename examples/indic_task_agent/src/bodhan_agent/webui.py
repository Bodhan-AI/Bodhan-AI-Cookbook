"""A read-only trace viewer on the standard library: plan on the left, events on the right.

No dependency and no writes: it serves the run folder that already exists, so
`--ui` can never affect a run's cost or its files.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Bodhan run</title>
<style>
 body{font:14px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;margin:0;background:#111;color:#e8e8e8}
 header{padding:12px 18px;border-bottom:1px solid #333;display:flex;gap:24px;align-items:baseline}
 h1{font-size:15px;margin:0;font-weight:600} #status{color:#9be49b}
 main{display:grid;grid-template-columns:minmax(320px,42%) 1fr;height:calc(100vh - 45px)}
 section{overflow:auto;padding:16px 18px} section+section{border-left:1px solid #333}
 .step{border-left:3px solid #444;padding:6px 10px;margin:0 0 10px}
 .step b{color:#8fd3ff} .muted{color:#888}
 ol{list-style:none;padding:0;margin:0} li{padding:3px 0;border-bottom:1px solid #1e1e1e}
 .ok{color:#9be49b}.bad{color:#ff9b9b}.warn{color:#ffd479}
</style></head><body>
<header><h1>Bodhan task run</h1><span id="status" class="muted">waiting for trace…</span></header>
<main><section id="plan"><span class="muted">No plan yet.</span></section><section><ol id="events"></ol></section></main>
<script>
function el(tag, cls, text){const n=document.createElement(tag); if(cls) n.className=cls; if(text!==undefined) n.textContent=text; return n;}
function renderPlan(plan){
  const box=document.getElementById('plan'); box.replaceChildren();
  if(!plan){box.append(el('span','muted','No plan yet.'));return;}
  box.append(el('p','muted', plan.understanding || ''));
  (plan.steps||[]).forEach(s=>{
    const d=el('div','step');
    d.append(el('b',null,s.id+' · '+s.tool));
    d.append(el('div',null,s.intent||''));
    const args=Object.entries(s.args||{}).map(([k,v])=>k+'='+JSON.stringify(v)).join('  ');
    if(args) d.append(el('div','muted',args));
    box.append(d);
  });
  if(plan.feasible===false) box.append(el('p','bad', plan.refusal||'refused'));
}
function label(e){
  if(e.type==='tool_call') return e.step+' '+e.tool+' · reserved Rs '+(e.reserved_inr??0);
  if(e.type==='tool_result') return e.step+' '+e.tool+' · '+(e.ok?'ok':'failed')+' · Rs '+(e.cost_inr??0)+' · '+((e.latency_ms??0)/1000).toFixed(1)+'s'+(e.cache_hit?' · cached':'');
  if(e.type==='verify') return 'verify '+(e.check||'')+' · '+(e.result||'')+' · '+(e.artifact||'').split('/').pop();
  return e.type;
}
function cls(e){
  if(e.type==='tool_result') return e.ok?'ok':'bad';
  if(e.type==='verify') return e.result==='passed'?'ok':e.result==='failed'?'bad':'warn';
  if(e.type==='error'||e.type==='step_failed') return 'bad';
  return '';
}
async function tick(){
  let text=''; try{text=await (await fetch('trace.jsonl',{cache:'no-store'})).text();}catch(_){return;}
  const events=text.split('\\n').filter(Boolean).map(l=>{try{return JSON.parse(l)}catch(_){return null}}).filter(Boolean);
  const plan=events.find(e=>e.type==='plan');
  renderPlan(plan);
  const list=document.getElementById('events'); list.replaceChildren();
  events.forEach(e=>list.append(el('li',cls(e),label(e))));
  const stop=[...events].reverse().find(e=>e.type==='stop');
  const status=document.getElementById('status');
  if(stop){status.textContent='status '+stop.status+' · Rs '+stop.cost_inr+' · '+stop.duration_s+'s';}
  else if(events.length){status.textContent=events.length+' events · running';}
}
tick(); setInterval(tick,1000);
</script></body></html>
"""


def serve(run_dir: str, port: int = 8766, block: bool = True):
    """Serve the run folder; returns the server when `block` is false."""
    root = Path(run_dir)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            name = {"": "trace.jsonl", "/": None, "/index.html": None, "/trace.jsonl": "trace.jsonl", "/report.json": "report.json", "/result.md": "result.md"}.get(self.path.split("?")[0], "missing")
            if name is None:
                body, ctype = PAGE.encode(), "text/html; charset=utf-8"
            elif name == "missing":
                self.send_error(404)
                return
            else:
                path = root / name
                if not path.is_file():
                    self.send_error(404)
                    return
                body, ctype = path.read_bytes(), "application/json" if name.endswith(".json") else "text/plain; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    if not block:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd
    print(f"trace viewer: http://127.0.0.1:{httpd.server_address[1]}/  (Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return httpd
