"""A thin, read-only live view of one run: plan, steps, cost, verification and artifacts.

    python -m bodhan_agent.viewer --out ./out [--port 8766]

Open the printed URL while the agent runs (or afterwards). The page polls the run's
trace.jsonl and report.json once a second. It binds to localhost only, refuses other Host headers (DNS rebinding), never calls a
model, never shows keys (none are in the trace), renders every value as plain text
(textContent, never innerHTML), and serves files only from <out>/artifacts.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agent run</title><style>
:root{--bg:#fafaf9;--fg:#1c1917;--muted:#78716c;--card:#fff;--line:#e7e5e4;--ok:#15803d;--warn:#b45309;--bad:#b91c1c}
@media (prefers-color-scheme:dark){:root{--bg:#1c1917;--fg:#f5f5f4;--muted:#a8a29e;--card:#292524;--line:#44403c}}
body{font:14px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--fg);margin:0 auto;max-width:1100px;padding:16px}
h1{font-size:18px;margin:0 0 4px} h2{font-size:15px;margin:20px 0 8px} .muted{color:var(--muted)}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px;margin:8px 0;overflow-wrap:anywhere}
table{width:100%;border-collapse:collapse} td,th{border-bottom:1px solid var(--line);padding:6px;text-align:left;vertical-align:top}
.pass{color:var(--ok)} .warn{color:var(--warn)} .fail,.err{color:var(--bad)} code{font-size:12px}
</style></head><body>
<h1 id="status">Loading…</h1><div class="muted" id="task"></div>
<h2>Plan</h2><div class="card" id="plan"></div>
<h2>Steps</h2><div class="card"><table><thead><tr><th>#</th><th>Tool</th><th>Why</th><th>Result</th><th>s</th><th>₹</th></tr></thead><tbody id="steps"></tbody></table></div>
<h2>Artifacts</h2><div class="card" id="artifacts"></div>
<h2>Cost</h2><div class="card" id="cost"></div>
<script>
function el(tag, text, cls){const e=document.createElement(tag); if(text!==undefined&&text!==null) e.textContent=String(text); if(cls) e.className=cls; return e;}
async function tick(){
  let s; try { s = await (await fetch('/api/state')).json(); } catch(e) { return; }
  document.getElementById('status').textContent = s.status ? ('Run ' + s.status) : 'Run in progress…';
  document.getElementById('task').textContent = s.task || '';
  const plan = document.getElementById('plan'); plan.replaceChildren();
  if (s.plan) { const ol = el('ol'); (s.plan.steps||[]).forEach(x => ol.appendChild(el('li', x))); plan.appendChild(ol);
    (s.plan.deliverables||[]).forEach(d => plan.appendChild(el('div',
      'Deliverable: ' + d.kind + (d.language ? ' (' + d.language + ')' : '') + (d.description ? ' - ' + d.description : ''), 'muted'))); }
  else plan.textContent = 'No plan recorded yet.';
  const tb = document.getElementById('steps'); tb.replaceChildren();
  (s.steps||[]).forEach(st => { const tr = el('tr'); tr.appendChild(el('td', st.step)); tr.appendChild(el('td', st.tool));
    tr.appendChild(el('td', st.rationale)); const v = st.verification || (st.ok ? 'ok' : (st.error || 'error'));
    tr.appendChild(el('td', v, st.ok ? (v==='fail'?'fail':(v==='warn'?'warn':'pass')) : 'err'));
    tr.appendChild(el('td', st.latency_s)); tr.appendChild(el('td', st.cost_inr)); tb.appendChild(tr); });
  const arts = document.getElementById('artifacts'); arts.replaceChildren();
  (s.artifacts||[]).forEach(a => { const d = el('div'); const link = el('a', a.name); link.href = '/artifacts/' + encodeURIComponent(a.name);
    d.appendChild(link); d.appendChild(el('span', '  ' + a.bytes + ' bytes', 'muted')); arts.appendChild(d); });
  if (!(s.artifacts||[]).length) arts.textContent = 'None yet.';
  document.getElementById('cost').textContent = 'Bodhan: ₹' + s.cost.bodhan_inr + ' over ' + s.cost.api_calls + ' requests (' + s.cost.cache_hits + ' served from cache)';
}
tick(); setInterval(tick, 1000);
</script></body></html>"""


def read_state(out: Path) -> dict:
    """Summarise the run from its trace and report (both written by the agent)."""
    state: dict = {"task": None, "status": None, "plan": None, "steps": [], "artifacts": [],
                   "cost": {"bodhan_inr": 0.0, "api_calls": 0, "cache_hits": 0}}
    trace = out / "trace.jsonl"
    if trace.is_file():
        for line in trace.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue  # a line being written right now
            kind = e.get("type")
            if kind == "run_start":
                state["task"] = e.get("task")
            elif kind == "plan":
                state["plan"] = {"steps": e.get("steps", []), "deliverables": e.get("deliverables", [])}
            elif kind == "tool_call":
                summary = e.get("result_summary") or {}
                state["steps"].append({"step": e.get("step"), "tool": e.get("tool"), "rationale": e.get("rationale", ""), "ok": e.get("ok"),
                                       "verification": (summary.get("verification") or {}).get("status"),
                                       "error": (summary.get("error") or {}).get("code"), "latency_s": e.get("latency_s"), "cost_inr": e.get("cost_inr")})
            elif kind == "api_call":
                if e.get("cache") in ("this_run", "previous_run", "in_flight"):
                    state["cost"]["cache_hits"] += 1
                else:
                    state["cost"]["api_calls"] += 1
                    state["cost"]["bodhan_inr"] = round(state["cost"]["bodhan_inr"] + float(e.get("cost_inr") or 0), 5)
            elif kind in ("finish", "stop"):
                state["status"] = e.get("status")
    report = out / "report.json"
    if report.is_file():
        try:
            r = json.loads(report.read_text(encoding="utf-8"))
            state["status"] = r.get("status")
            state["cost"]["bodhan_inr"] = r.get("cost", {}).get("total_counted_inr", state["cost"]["bodhan_inr"])
        except ValueError:
            pass
    arts = out / "artifacts"
    if arts.is_dir():
        state["artifacts"] = [{"name": f.name, "bytes": f.stat().st_size} for f in sorted(arts.iterdir()) if f.is_file()]
    return state


def make_handler(out: Path):
    out = out.resolve()
    arts = (out / "artifacts").resolve()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; media-src 'self'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802 (http.server API)
            port = self.server.server_address[1]
            if self.headers.get("Host", "").lower() not in (f"127.0.0.1:{port}", f"localhost:{port}"):
                # DNS rebinding: a web page whose name now points at 127.0.0.1 still sends its own Host header
                return self._send(403, b"forbidden host", "text/plain; charset=utf-8")
            path = unquote(urlparse(self.path).path)
            if path == "/":
                return self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            if path == "/api/state":
                return self._send(200, json.dumps(read_state(out), ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
            if path.startswith("/artifacts/"):
                target = (arts / path[len("/artifacts/"):]).resolve()
                if target.parent == arts and target.is_file():  # only direct children of artifacts/, no traversal
                    ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                    if ctype.startswith("text/") or target.suffix in (".md", ".srt", ".json"):
                        ctype = "text/plain; charset=utf-8"  # never rendered as HTML
                    return self._send(200, target.read_bytes(), ctype)
            return self._send(404, b"not found", "text/plain; charset=utf-8")

    return Handler


def serve(out: Path, port: int = 8766) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(out))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m bodhan_agent.viewer", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="The run's output folder (the agent's --out).")
    ap.add_argument("--port", type=int, default=8766)
    a = ap.parse_args(argv)
    httpd = serve(Path(a.out), a.port)
    print(f"Viewing {Path(a.out).resolve()} at http://127.0.0.1:{httpd.server_port}/ (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
