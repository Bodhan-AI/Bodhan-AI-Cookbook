"""Lightweight Web UI dashboard showing the Task Agent plan, trace, and artifacts live (Stretch Goal)."""

from __future__ import annotations

import argparse
import http.server
import json
import mimetypes
import os
import sys
from pathlib import Path

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Bodhan Task Agent - Live Trace Dashboard</title>
  <style>
    :root {
      --bg: #0f172a;
      --card-bg: #1e293b;
      --border: #334155;
      --accent: #38bdf8;
      --text: #f8fafc;
      --muted: #94a3b8;
      --success: #4ade80;
      --danger: #f87171;
      --warn: #fbbf24;
    }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      margin: 0;
      padding: 24px;
    }
    .container { max-width: 1000px; margin: 0 auto; }
    header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 16px; margin-bottom: 24px; }
    h1 { margin: 0; font-size: 24px; color: var(--accent); }
    .badge { padding: 4px 10px; border-radius: 999px; font-weight: bold; font-size: 13px; text-transform: uppercase; }
    .badge.completed { background: rgba(74, 222, 128, 0.2); color: var(--success); }
    .badge.impossible_task { background: rgba(251, 191, 36, 0.2); color: var(--warn); }
    .badge.failed { background: rgba(248, 113, 113, 0.2); color: var(--danger); }
    .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px; }
    .stat-card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 16px; text-align: center; }
    .stat-val { font-size: 24px; font-weight: bold; color: var(--accent); }
    .stat-label { font-size: 12px; color: var(--muted); margin-top: 4px; }
    .section { background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 20px; margin-bottom: 24px; }
    h2 { margin-top: 0; font-size: 18px; border-bottom: 1px solid var(--border); padding-bottom: 8px; }
    .step-item { display: flex; gap: 16px; border-left: 2px solid var(--accent); padding-left: 16px; margin-bottom: 16px; position: relative; }
    .step-num { background: var(--accent); color: #000; font-weight: bold; border-radius: 50%; width: 24px; height: 24px; display: flex; align-items: center; justify-content: center; font-size: 12px; position: absolute; left: -29px; }
    .step-tool { font-family: monospace; color: #f472b6; font-weight: bold; }
    .step-meta { font-size: 12px; color: var(--muted); margin-top: 4px; }
    .step-summary { margin-top: 4px; }
    audio { width: 100%; margin-top: 8px; }
    pre { background: #0b1120; padding: 12px; border-radius: 6px; overflow-x: auto; font-size: 13px; border: 1px solid var(--border); }
    table { width: 100%; border-collapse: collapse; margin-top: 10px; }
    th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--border); }
    th { color: var(--muted); font-size: 13px; }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div>
        <h1>Bodhan Task Agent Dashboard</h1>
        <div style="color: var(--muted); font-size: 13px; margin-top: 4px;" id="task-name">Loading task...</div>
      </div>
      <div>
        <span class="badge" id="task-status">Loading...</span>
      </div>
    </header>

    <div class="stats">
      <div class="stat-card">
        <div class="stat-val" id="stat-steps">0</div>
        <div class="stat-label">TOTAL STEPS</div>
      </div>
      <div class="stat-card">
        <div class="stat-val" id="stat-cost">₹0.00</div>
        <div class="stat-label">TOTAL COST</div>
      </div>
      <div class="stat-card">
        <div class="stat-val" id="stat-time">0.0s</div>
        <div class="stat-label">DURATION</div>
      </div>
      <div class="stat-card">
        <div class="stat-val" id="stat-artifacts">0</div>
        <div class="stat-label">ARTIFACTS</div>
      </div>
    </div>

    <div class="section">
      <h2>Generated Artifacts</h2>
      <div id="artifacts-list">None yet</div>
    </div>

    <div class="section">
      <h2>Verification Checks</h2>
      <table>
        <thead><tr><th>Check</th><th>Status</th><th>Details</th></tr></thead>
        <tbody id="verification-table"></tbody>
      </table>
    </div>

    <div class="section">
      <h2>Execution Trace Timeline</h2>
      <div id="trace-timeline">No trace recorded</div>
    </div>
  </div>

  <script>
    function esc(str) {
      return String(str ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }
    async function refreshData() {
      try {
        const repRes = await fetch('/api/report');
        const report = await repRes.json();
        
        document.getElementById('task-name').innerText = report.task || "No task";
        const badge = document.getElementById('task-status');
        badge.innerText = report.status || "UNKNOWN";
        badge.className = 'badge ' + esc(report.status || '');
        
        document.getElementById('stat-steps').innerText = report.total_steps || 0;
        document.getElementById('stat-cost').innerText = '₹' + (report.total_cost_inr || 0).toFixed(4);
        document.getElementById('stat-time').innerText = (report.total_time_seconds || 0).toFixed(1) + 's';
        document.getElementById('stat-artifacts').innerText = report.artifacts_count || 0;

        // Artifacts
        const artList = document.getElementById('artifacts-list');
        if (report.artifacts && report.artifacts.length > 0) {
          artList.innerHTML = report.artifacts.map(a => {
            let player = '';
            if (String(a.path || '').endsWith('.wav')) {
              player = `<audio controls src="/file/${encodeURIComponent(a.path)}"></audio>`;
            }
            return `<div style="margin-bottom: 12px;"><strong>${esc(a.name)}</strong> (${esc(a.description)}) - <a href="/file/${encodeURIComponent(a.path)}" target="_blank" style="color:var(--accent);">Download ${esc(a.path)}</a>${player}</div>`;
          }).join('');
        }

        // Verifications
        const vTable = document.getElementById('verification-table');
        if (report.verification_results) {
          vTable.innerHTML = report.verification_results.map(v => 
            `<tr><td><strong>${esc(v.check)}</strong></td><td style="color:${v.passed ? 'var(--success)' : 'var(--danger)'};">${v.passed ? 'PASSED' : 'FAILED'}</td><td>${esc(v.details)}</td></tr>`
          ).join('');
        }

        // Trace
        const traceRes = await fetch('/api/trace');
        const traceLines = await traceRes.json();
        const traceEl = document.getElementById('trace-timeline');
        if (traceLines.length > 0) {
          traceEl.innerHTML = traceLines.map(s => `
            <div class="step-item">
              <div class="step-num">${esc(s.step)}</div>
              <div>
                <span class="step-tool">${esc(s.tool)}</span>
                <span class="step-meta">(${esc(s.latency_ms)}ms · ₹${Number(s.cost_inr || 0).toFixed(4)})</span>
                <div class="step-meta" style="color:#e2e8f0;"><strong>Plan:</strong> ${esc(s.plan)}</div>
                <div class="step-summary">${esc(s.result_summary)}</div>
              </div>
            </div>
          `).join('');
        }
      } catch (err) {
        console.error("Dashboard refresh error:", err);
      }
    }
    refreshData();
    setInterval(refreshData, 3000);
  </script>
</body>
</html>
"""


def run_ui(out_dir: Path, port: int = 8501) -> None:
    """Start local web server serving the agent dashboard."""
    report_file = out_dir / "report.json"
    trace_file = out_dir / "trace.jsonl"

    class DashboardHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/" or self.path == "/index.html":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(HTML_TEMPLATE.encode("utf-8"))
                return

            if self.path == "/api/report":
                data = {}
                if report_file.exists():
                    try:
                        data = json.loads(report_file.read_text(encoding="utf-8"))
                    except Exception:
                        pass
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode("utf-8"))
                return

            if self.path == "/api/trace":
                lines = []
                if trace_file.exists():
                    try:
                        for line in trace_file.read_text(encoding="utf-8").splitlines():
                            if line.strip():
                                lines.append(json.loads(line))
                    except Exception:
                        pass
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(lines).encode("utf-8"))
                return

            if self.path.startswith("/file/"):
                rel_path = urllib_unquote(self.path[6:])
                out_root = out_dir.resolve()
                target_file = (out_root / rel_path).resolve()
                try:
                    target_file.relative_to(out_root)
                    is_contained = True
                except ValueError:
                    is_contained = False
                if is_contained and target_file.is_file():
                    mime, _ = mimetypes.guess_type(str(target_file))
                    mime = mime or "application/octet-stream"
                    self.send_response(200)
                    self.send_header("Content-Type", mime)
                    self.end_headers()
                    self.wfile.write(target_file.read_bytes())
                    return
                self.send_response(404)
                self.end_headers()
                return

            self.send_response(404)
            self.end_headers()

        def log_message(self, format, *args):
            pass

    def urllib_unquote(s: str) -> str:
        import urllib.parse
        return urllib.parse.unquote(s)

    httpd = http.server.HTTPServer(("0.0.0.0", port), DashboardHandler)
    print(f"Task Agent Web UI dashboard running at http://localhost:{port} (monitoring: {out_dir})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Bodhan Task Agent - Live Web UI Dashboard")
    parser.add_argument("--out", default="./out", help="Path to out/ directory")
    parser.add_argument("--port", type=int, default=8501, help="Port to serve on (default: 8501)")
    args = parser.parse_args()

    run_ui(Path(args.out).resolve(), port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
