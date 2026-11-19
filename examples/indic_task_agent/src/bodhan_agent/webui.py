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
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bodhan task runs</title>
<style>
 :root{color-scheme:dark;--bg:#0d1017;--panel:#151a24;--panel2:#1b2130;--line:#262d3d;--text:#e8ebf3;--muted:#8d97ad;
   --blue:#5b9dff;--green:#4cd08a;--red:#ff6b78;--amber:#ffc857;--violet:#a98bff;--orange:#ff9a5c;--pink:#ff7eb6}
 *{box-sizing:border-box}
 body{margin:0;background:radial-gradient(1200px 600px at 70% -10%,#16203a 0%,var(--bg) 60%);color:var(--text);
   font:15px/1.55 Inter,system-ui,Segoe UI,Roboto,sans-serif}
 *{overflow-wrap:anywhere}
 #wrap{display:grid;grid-template-columns:250px minmax(0,1fr);height:100vh}
 nav{background:#0a0d13;border-right:1px solid var(--line);padding:18px 12px;overflow-y:auto}
 .brand{display:flex;align-items:center;gap:10px;padding:0 8px 16px;font-weight:700;font-size:15px}
 .logo{width:28px;height:28px;border-radius:8px;background:linear-gradient(135deg,var(--blue),var(--violet));display:grid;place-items:center;font-size:15px}
 .label{color:var(--muted);font-size:11px;letter-spacing:.12em;text-transform:uppercase;padding:0 8px 8px}
 .run{display:flex;gap:10px;align-items:flex-start;padding:9px 10px;margin-bottom:3px;border-radius:9px;cursor:pointer;border:1px solid transparent}
 .run:hover{background:var(--panel)}.run.on{background:var(--panel2);border-color:#33405f}
 .dot{width:9px;height:9px;border-radius:50%;margin-top:7px;flex:none;background:var(--muted)}
 .dot.completed{background:var(--green)}.dot.failed{background:var(--red)}.dot.partial{background:var(--amber)}.dot.running{background:var(--blue)}
 .run b{display:block;font-weight:600;font-size:14px}.run small{color:var(--muted)}
 main{overflow-y:auto;padding:30px 40px 90px;min-width:0}
 .inner{max-width:880px;margin:0 auto}
 .hero{background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:16px;padding:22px 24px;margin-bottom:26px}
 .hero h1{font-size:22px;line-height:1.3;margin:0 0 14px}
 .pill{display:inline-block;padding:3px 12px;border-radius:99px;font-size:12px;font-weight:700;letter-spacing:.06em;margin-bottom:12px}
 .pill.completed{background:#14392a;color:var(--green)}.pill.failed{background:#4a1c24;color:var(--red)}
 .pill.partial{background:#4a3c12;color:var(--amber)}.pill.running{background:#173458;color:var(--blue)}
 .stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}
 .stat{background:#0e131c;border:1px solid var(--line);border-radius:11px;padding:10px 14px}
 .stat small{color:var(--muted);display:block;font-size:12px}.stat b{font-size:18px}
 .bar{height:7px;background:#0e131c;border-radius:5px;margin-top:14px;overflow:hidden}
 .bar i{display:block;height:100%;background:linear-gradient(90deg,var(--blue),var(--violet))}
 .tl{position:relative;padding-left:46px}
 .tl:before{content:"";position:absolute;left:15px;top:6px;bottom:6px;width:2px;background:var(--line)}
 .item{position:relative;margin-bottom:12px}
 .item .ic{position:absolute;left:-46px;top:10px;width:32px;height:32px;border-radius:50%;display:grid;place-items:center;
   background:var(--panel2);border:2px solid var(--line);font-size:14px}
 .item.ok .ic{border-color:var(--green);color:var(--green)}.item.bad .ic{border-color:var(--red);color:var(--red)}
 .item.think .ic{border-color:#3a4a74;color:var(--blue)}.item.warn .ic{border-color:var(--amber);color:var(--amber)}
 .item.skip .ic{color:var(--muted)}.item.run .ic{border-color:var(--blue);color:var(--blue)}
 .card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 16px}
 .item.ok .card{border-left:3px solid var(--green)}.item.bad .card{border-left:3px solid var(--red)}
 .item.skip .card{opacity:.65}.item.run .card{border-left:3px solid var(--blue)}
 .kicker{font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-bottom:3px}
.chips+.kicker{margin-top:14px}
 .title{font-weight:600}.sub{color:#b6bfd3;margin-top:2px}
 .chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:9px}
 .chip{font-size:12px;padding:2px 9px;border-radius:99px;background:#0e131c;border:1px solid var(--line);color:#b6bfd3}
 .tool{font-weight:600;border:0}
 .t-ocr_document{background:#2a2250;color:var(--violet)}.t-transcribe_audio{background:#173458;color:var(--blue)}
 .t-translate_text{background:#14392a;color:var(--green)}.t-speak_text{background:#4a2c14;color:var(--orange)}
 .t-write_text{background:#4a1c36;color:var(--pink)}.t-probe_media,.t-detect_language{background:#262d3d;color:#b6bfd3}
 .sid{color:var(--muted);font-weight:700;margin-right:8px}
 ul{margin:6px 0 0;padding-left:18px;color:#b6bfd3}li{margin:3px 0}
 .chk{display:grid;grid-template-columns:20px minmax(0,1fr);gap:4px 8px;padding:5px 0;border-top:1px solid var(--line)}
 .chk:first-of-type{border-top:0}
 .chk .r{font-weight:700}.passed{color:var(--green)}.failed{color:var(--red)}.warning{color:var(--amber)}.skipped{color:var(--muted)}
 .muted{color:var(--muted)}
 .empty{margin:80px auto;text-align:center;color:var(--muted)}
</style></head><body><div id="wrap">
<nav><div class="brand"><div class="logo">B</div>Bodhan agent</div><div class="label">Task runs</div><div id="runs"></div></nav>
<main><div class="inner" id="main"><div class="empty">Waiting for a run.<br>Start one in the terminal and it will appear here.</div></div></main></div>
<script>
const NL=String.fromCharCode(10);
const $=(t,c,x)=>{const n=document.createElement(t);if(c)n.className=c;if(x!==undefined)n.textContent=x;return n};
let current=new URLSearchParams(location.search).get('run');
async function g(u,asJson){try{const r=await fetch(u,{cache:'no-store'});if(!r.ok)return null;return asJson?await r.json():await r.text()}catch(_){return null}}
const rs=n=>'Rs '+Number(n||0).toFixed(2);
const sec=ms=>((ms||0)/1000).toFixed(1)+' s';
const chip=(t,c)=>$('span','chip'+(c?' '+c:''),t);

function item(kind,icon){const i=$('div','item '+kind);i.append($('div','ic',icon));const c=$('div','card');i.append(c);i.card=c;return i}
function note(kind,icon,kicker,title,sub,chips){
  const i=item(kind,icon),c=i.card;
  if(kicker)c.append($('div','kicker',kicker));
  if(title)c.append($('div','title',title));
  if(sub)c.append($('div','sub',sub));
  if(chips&&chips.length){const w=$('div','chips');chips.forEach(x=>w.append(x));c.append(w)}
  return i}

function resultChips(e){
  const s=e.summary||{},out=[];
  out.push(chip(rs(e.cost_inr)));out.push(chip(sec(e.latency_ms)));
  if(e.cache_hit)out.push(chip('from cache'));
  if(s.duration_s)out.push(chip(Math.round(s.duration_s)+' s audio'));
  if(s.page_count)out.push(chip(s.page_count+' pages'));
  if(s.chars)out.push(chip(s.chars+' characters'));
  if(s.language_hint)out.push(chip('language: '+s.language_hint));
  if(s.chunks)out.push(chip(s.chunks+' windows'));
  if(s.clips)out.push(chip(s.clips+' speech clips'));
  (s.warnings||[]).forEach(w=>out.push(chip(w)));
  return out}

async function show(run){
  const raw=await g('/run/'+encodeURIComponent(run)+'/trace.jsonl')||'';
  const events=raw.split(NL).filter(Boolean).map(l=>{try{return JSON.parse(l)}catch(_){return null}}).filter(Boolean);
  const report=await g('/run/'+encodeURIComponent(run)+'/report.json',true);
  const stop=[...events].reverse().find(e=>e.type==='stop');
  const status=report?report.status:(stop?stop.status:'running');
  const results=events.filter(e=>e.type==='tool_result');
  const spent=report?report.budget.cost_inr:results.reduce((s,e)=>s+(e.cost_inr||0),0);
  const est=events.find(e=>e.type==='estimate');
  const cap=report?report.budget.max_cost_inr:(est?est.max_cost_inr:null);
  const main=document.getElementById('main'),scroller=main.parentElement,keep=scroller.scrollTop;
  main.replaceChildren();

  const hero=$('div','hero');
  hero.append($('div','pill '+status,status.toUpperCase()),$('h1',null,report?report.task:run));
  const st=$('div','stats');
  const tile=(l,v)=>{const d=$('div','stat');d.append($('small',null,l),$('b',null,v));return d};
  const steps=events.filter(e=>e.type==='tool_call').length;
  st.append(tile('Spent',rs(spent)),tile('Budget',cap?rs(cap):'-'),tile('Time',report?report.duration_s+' s':'running'),tile('Steps',String(report?report.budget.steps_used:steps)));
  hero.append(st);
  if(cap){const b=$('div','bar'),i=$('i');i.style.width=Math.min(100,spent/cap*100)+'%';b.append(i);hero.append(b)}
  main.append(hero);

  const tl=$('div','tl');main.append(tl);
  const cards={};let checks=null;
  for(const e of events){
    if(e.type==='intake'){
      const f=(e.inventory||[]).map(i=>chip(i.name+' · '+i.kind+(i.duration_s?' · '+Math.round(i.duration_s)+' s':'')+(i.pages?' · '+i.pages+' pages':'')));
      tl.append(note('think','1','Step 0 · free','Looked at your files','Nothing is spent while the agent inspects them.',f))}
    else if(e.type==='llm_call'){
      tl.append(note('think','…','Thinking','Asked the language model: '+(e.purpose||'').replace('_',' '),null,[chip(sec(e.latency_ms))]))}
    else if(e.type==='plan'){
      if(e.feasible===false){tl.append(note('bad','✗','Decision','It cannot be done',e.refusal||''));continue}
      const i=note('think','◆','Plan',e.understanding||'',null,[chip((e.steps||[]).length+' steps')]);
      if((e.assumptions||[]).length){const u=$('ul');e.assumptions.forEach(a=>u.append($('li',null,a.text)));i.card.append($('div','kicker','Assumptions it made'),u)}
      tl.append(i)}
    else if(e.type==='estimate'){
      tl.append(note('think','₹','Cost check','Estimated before spending anything','About '+rs(e.estimated_cost_inr)+' against a limit of '+rs(e.max_cost_inr)+'.'))}
    else if(e.type==='replan'){tl.append(note('warn','↻','Re-planning','Changed the plan',e.note||e.reason||''))}
    else if(e.type==='plan_repair'){tl.append(note('warn','↻','Re-planning','Fixed an invalid plan','The first plan was not valid, so it asked again once.'))}
    else if(e.type==='resume'){tl.append(note('think','↻','Resume','Continued an earlier run','Reusing its plan and every finished step.'))}
    else if(e.type==='fit'){tl.append(note('think','≈','Length check','Checked speech length before buying speech','Predicted '+e.predicted_s+' s of speech for the '+e.wanted_s+' s asked for.'))}
    else if(e.type==='tool_call'){
      const i=item('run','▶'),c=i.card;
      const h=$('div','title');h.append($('span','sid',e.step),$('span','chip tool t-'+e.tool,e.tool));
      c.append(h,$('div','sub',e.intent||''));
      const w=$('div','chips');w.append(chip('running… reserved '+rs(e.reserved_inr)));c.append(w);i.chips=w;cards[e.step]=i;tl.append(i)}
    else if(e.type==='tool_result'){
      const i=cards[e.step];if(!i)continue;
      i.className='item '+(e.ok?'ok':'bad');i.firstChild.textContent=e.ok?'✓':'✗';
      i.chips.replaceChildren(...resultChips(e))}
    else if(e.type==='step_skipped'||e.type==='step_reused'){
      const skip=e.type==='step_skipped';
      const i=item(skip?'skip':'ok',skip?'–':'↺'),c=i.card;
      const h=$('div','title');h.append($('span','sid',e.step),$('span','chip tool t-'+e.tool,e.tool),$('span','muted',skip?'  skipped':'  reused'));
      c.append(h,$('div','sub',e.reason||''));const w=$('div','chips');w.append(chip('no cost'));c.append(w);tl.append(i)}
    else if(e.type==='step_failed'){
      const i=cards[e.step];if(i){i.className='item bad';i.firstChild.textContent='✗';if(e.error&&e.error.message)i.card.append($('div','sub',e.error.message))}}
    else if(e.type==='verify'){
      if(!checks){checks=note('think','✔','Verification','Checked its own work',null);tl.append(checks)}
      const r=e.result,row=$('div','chk');
      row.append($('div','r '+r,r==='passed'?'✓':r==='failed'?'✗':r==='warning'?'!':'–'));
      const t=$('div');t.append($('b',null,e.check),$('span','muted','  '+(e.artifact||'').split('/').pop()),$('div','muted',e.detail||''));row.append(t);
      checks.card.append(row)}
    else if(e.type==='error'){tl.append(note('bad','✗','Stopped',e.code||'error',e.message||''))}
    else if(e.type==='stop'){
      tl.append(note(e.status==='completed'?'ok':'bad','■','Finished',e.status==='completed'?'Done':'Ended: '+e.status,null,[chip('Total '+rs(e.cost_inr)),chip(e.duration_s+' s')]))}
  }
  scroller.scrollTop=keep}

async function tick(){
  const runs=await g('/api/runs',true)||[];
  const nav=document.getElementById('runs');nav.replaceChildren();
  if(!current&&runs.length)current=runs[0].name;
  runs.forEach(r=>{
    const d=$('div','run'+(r.name===current?' on':''));
    const t=$('div');t.append($('b',null,r.name),$('small',null,r.status+(r.cost!==null?' · '+rs(r.cost):'')));
    d.append($('div','dot '+r.status),t);
    d.onclick=()=>{current=r.name;history.replaceState(null,'','?run='+encodeURIComponent(r.name));tick()};nav.append(d)});
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
