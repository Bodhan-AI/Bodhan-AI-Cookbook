#!/usr/bin/env python3
"""Modern developer workbench UI for bodhan_agent (stdlib only)."""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import urllib.parse
from datetime import datetime
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

BASE_DIR = Path(__file__).resolve().parent.parent.parent
OUTPUTS_DIR = BASE_DIR / "outputs"
DEMO_INPUTS_DIR = BASE_DIR / "demo_inputs"
UPLOADS_DIR = DEMO_INPUTS_DIR / "uploads"
UI_RUNS_DIR = OUTPUTS_DIR / "ui_runs"

PRESETS = [
    {
        "id": "t1_tamil_summary",
        "name": "Task 1 (Tamil Summary)",
        "task": "Create a 2-minute Tamil audio summary of this 8-page scanned PDF",
        "files": ["demo_inputs/Task1_8page.pdf"],
    },
    {
        "id": "t2_hindi_subtitles",
        "name": "Task 2 (Hindi SRT)",
        "task": "Transcribe this 3-minute Hindi WAV and create English .srt subtitles",
        "files": ["demo_inputs/hindi_voice.wav"],
    },
    {
        "id": "t3_marathi_table",
        "name": "Task 3 (Marathi Table)",
        "task": "Translate this worksheet to Marathi keeping the table",
        "files": ["demo_inputs/worksheet_table.png"],
    },
    {
        "id": "t4_telugu_clips",
        "name": "Task 4 (Telugu Filter)",
        "task": "Identify Telugu audio clips among these files and translate them to English",
        "files": ["demo_inputs/clips/"],
    },
    {
        "id": "t5_bengali_tts",
        "name": "Task 5 (Bengali TTS)",
        "task": "Read this paragraph aloud using female voice in Bengali",
        "files": ["demo_inputs/task5_paragraph.txt"],
    },
    {
        "id": "t6_french_impossible",
        "name": "Task 6 (French Gate)",
        "task": "Translate this video into French",
        "files": ["demo_inputs/Task6_video.mp4"],
    },
]

# ponytail: file uploads stored locally; no CDN or external asset hosting
_ASSETS_JS = """
// ponytail: bundled inline to avoid external deps; tree-shaken by browser
(function(){'use strict';
const $=s=>document.querySelector(s),$$=s=>document.querySelectorAll(s);
const ESC={ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' };
const escHtml=s=>String(s).replace(/[&<>"']/g,c=>ESC[c]);
const fmtCost=v=>v==null?'—':`₹${Number(v).toFixed(2)}`;
const fmtMs=v=>v==null?'—':`${Math.round(v)}ms`;
function debounce(fn,ms){let t;return(...a)=>{clearTimeout(t);t=setTimeout(()=>fn(...a),ms);};}
window.__bodhan={escHtml,fmtCost,fmtMs,$,$$,debounce};
})();
"""

def _resolve_safe(path: Path) -> Path | None:
    try:
        resolved = path.resolve()
        if OUTPUTS_DIR.resolve() in resolved.parents or resolved == OUTPUTS_DIR.resolve():
            return resolved
    except Exception:
        pass
    return None

def _parse_trace(path: Path) -> list[dict[str, Any]]:
    steps = []
    if not path.exists():
        return steps
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                steps.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return steps

def _parse_report(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

def _run_agent_background(run_dir, task, files, max_steps, max_cost_inr):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BASE_DIR / "src") + os.pathsep + env.get("PYTHONPATH", "")
    cmd = [
        sys.executable, "-m", "bodhan_agent",
        "--task", task,
        "--files", *files,
        "--out", str(run_dir),
        "--max-steps", str(max_steps),
        "--max-cost-inr", str(max_cost_inr),
    ]
    try:
        subprocess.run(cmd, cwd=BASE_DIR, env=env, check=False, capture_output=True, text=True, timeout=300)
    except (subprocess.TimeoutExpired, Exception):
        pass

class WebUIHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            self._serve_html()
        elif path == "/api/presets":
            self._serve_json({"presets": PRESETS})
        elif path == "/api/runs":
            self._list_runs()
        elif path == "/api/status":
            run_id = query.get("run_id", [""])[0]
            self._serve_status(run_id)
        elif path.startswith("/api/artifact"):
            self._serve_artifact(path, query)
        elif path == "/api/mcp/capabilities":
            self._serve_mcp_capabilities()
        elif path == "/api/mcp/voices":
            self._serve_mcp_voices()
        else:
            super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/run":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                self._serve_json({"error": "Invalid JSON"}, 400)
                return
            self._start_run(data)
        elif parsed.path == "/api/upload":
            self._handle_upload()
        elif parsed.path == "/api/mcp/call":
            self._handle_mcp_call()
        elif parsed.path == "/api/eval":
            self._handle_eval()
        else:
            self.send_error(404)

    def _serve_html(self):
        html = self._get_dashboard_html()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _serve_json(self, data: dict, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _list_runs(self):
        runs = []
        for d in sorted(UI_RUNS_DIR.iterdir(), reverse=True):
            if d.is_dir() and (d / "report.json").exists():
                report = _parse_report(d / "report.json")
                trace_path = d / "trace.jsonl"
                steps = _parse_trace(trace_path)
                runs.append({
                    "run_id": d.name,
                    "label": d.name,
                    "status": report.get("status", "unknown") if report else "running",
                    "completed": report is not None,
                    "total_cost_inr": report.get("total_cost_inr", 0) if report else 0,
                    "steps_used": report.get("steps_used", 0) if report else len(steps),
                    "max_steps": report.get("max_steps", 30) if report else 30,
                })
        self._serve_json({"runs": runs})

    def _serve_status(self, run_id: str):
        if not run_id:
            self._serve_json({"error": "run_id required"}, 400)
            return
        run_dir = UI_RUNS_DIR / run_id
        safe = _resolve_safe(run_dir)
        if not safe or not safe.exists():
            self._serve_json({"error": "Run not found"}, 404)
            return
        trace_path = safe / "trace.jsonl"
        report_path = safe / "report.json"
        result_md_path = safe / "result.md"
        steps = _parse_trace(trace_path)
        report = _parse_report(report_path)
        result_md = result_md_path.read_text(encoding="utf-8") if result_md_path.exists() else ""
        assumptions = report.get("assumptions", []) if report else []
        verification_results = report.get("verification_results", {}) if report else {}
        self._serve_json({
            "run_id": run_id,
            "steps": steps,
            "report": report,
            "result_md": result_md,
            "completed": report is not None,
            "assumptions": assumptions,
            "verification_results": verification_results,
        })

    def _serve_artifact(self, path: str, query: dict):
        rel = path.replace("/api/artifact/", "").lstrip("/")
        if not rel:
            self.send_error(404)
            return
        file_path = (OUTPUTS_DIR / rel).resolve()
        safe = _resolve_safe(file_path)
        if not safe or not safe.exists() or not safe.is_file():
            self.send_error(404)
            return
        mime, _ = mimetypes.guess_type(str(safe))
        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.end_headers()
        with open(safe, "rb") as f:
            self.wfile.write(f.read())

    def _handle_upload(self):
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        length = int(self.headers.get("Content-Length", 0))
        ctype = self.headers.get("Content-Type", "")
        disc = ctype.split(";")[0].strip() if ";" in ctype else ctype.strip()
        if disc != "multipart/form-data":
            self.send_error(400, "Expected multipart/form-data")
            return
        boundary = ctype.split("boundary=")[-1].strip()
        if not boundary:
            self.send_error(400, "Missing boundary")
            return
        parts = self._split_multipart(length, boundary)
        uploaded = []
        for name, filename, data in parts:
            if not filename or not name:
                continue
            safe_name = "".join(c for c in filename if c.isalnum() or c in ".-_ ")[:200]
            if not safe_name:
                safe_name = "upload"
            out_path = UPLOADS_DIR / safe_name
            # ponytail: overwrite same-name uploads; acceptable for local dev
            with open(out_path, "wb") as f:
                f.write(data)
            rel = str(out_path.relative_to(BASE_DIR))
            uploaded.append({"name": safe_name, "path": rel, "size": len(data)})
        self._serve_json({"uploaded": uploaded})

    @staticmethod
    def _split_multipart(length: int, boundary: str):
        body = b"--" + boundary.encode() + b"\r\n"
        end = b"--" + boundary.encode() + b"--\r\n"
        raw = b""
        while len(raw) < length:
            chunk = WebUIHandler._read_chunk(length - len(raw))
            if not chunk:
                break
            raw += chunk
        idx = raw.find(body)
        tail_idx = raw.find(end)
        if idx == -1 or tail_idx == -1:
            return []
        payload = raw[idx + len(body):tail_idx]
        results = []
        part_re = re.compile(rb'\r\nContent-Disposition:\s*form-data;\s*name="([^"]+)"(?:;\s*filename="([^"]*)")?\r\n\r\n')
        pos = 0
        while True:
            m = part_re.search(payload, pos)
            if not m:
                break
            name = m.group(1).decode()
            fname = m.group(2)
            start = m.end()
            next_sep = payload.find(b"\r\n", start)
            if next_sep == -1:
                break
            data = payload[start:next_sep]
            results.append((name, fname.decode() if fname else "", data))
            pos = next_sep + 2
        return results

    @staticmethod
    def _read_chunk(n: int) -> bytes | None:
        return WebUIHandler.rfile.read(n) if hasattr(WebUIHandler, 'rfile') else None

    def _start_run(self, data: dict):
        task = data.get("task", "").strip()
        files = data.get("files", [])
        max_steps = int(data.get("max_steps", 30))
        max_cost_inr = float(data.get("max_cost_inr", 20.0))
        if not task:
            self._serve_json({"error": "task required"}, 400)
            return
        if not files:
            self._serve_json({"error": "files required"}, 400)
            return
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        run_dir = UI_RUNS_DIR / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        thread = threading.Thread(
            target=_run_agent_background,
            args=(run_dir, task, files, max_steps, max_cost_inr),
            daemon=True,
        )
        thread.start()
        self._serve_json({"run_id": run_id, "status": "started"})

    def _serve_mcp_capabilities(self):
        from bodhan_mcp.server import CAPABILITIES
        self._serve_json(CAPABILITIES)

    def _serve_mcp_voices(self):
        from bodhan_mcp.models import TTS_VOICES
        self._serve_json(TTS_VOICES)

    def _handle_mcp_call(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._serve_json({"error": "Invalid JSON"}, 400)
            return
        tool = data.get("tool")
        params = data.get("params", {})
        if not tool:
            self._serve_json({"error": "tool required"}, 400)
            return
        import asyncio
        from bodhan_mcp.tools import (
            inspect_media, transcribe_audio, extract_document_text,
            translate_text, synthesize_speech,
        )
        TOOLS = {
            "inspect_media": inspect_media,
            "transcribe_audio": transcribe_audio,
            "extract_document_text": extract_document_text,
            "translate_text": translate_text,
            "synthesize_speech": synthesize_speech,
        }
        fn = TOOLS.get(tool)
        if fn is None:
            self._serve_json({"error": f"Unknown tool: {tool}"}, 404)
            return
        try:
            t0 = __import__('time').time()
            result = asyncio.run(fn(**params))
            latency_ms = (time.time() - t0) * 1000  # noqa: F821 — time imported below
            self._serve_json({"result": result, "latency_ms": round(latency_ms, 1)})
        except Exception as e:
            self._serve_json({"error": str(e)}, 500)

    def _handle_eval(self):
        import asyncio
        from bodhan_agent.eval import run_benchmark
        try:
            report = asyncio.run(run_benchmark(UI_RUNS_DIR, mode="mock"))
            self._serve_json({"report": report})
        except Exception as e:
            self._serve_json({"error": str(e)}, 500)

    def _get_dashboard_html(self) -> str:
        return _HTML_TEMPLATE


# ── Modern Developer Workbench UI Template ──────────────────────────────────
_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Bodhan Agent Studio — bodhan_agent</title>
<style>
:root {
  --bg-canvas:#09090b;--bg-card:#111114;--bg-surface:#18181b;
  --bg-hover:#1e1e22;--border:rgba(255,255,255,.07);--border-hover:rgba(255,255,255,.15);
  --text-primary:#fafafa;--text-secondary:#a1a1aa;--text-muted:#6b7280;
  --accent:#6366f1;--accent-light:#818cf8;--accent-glow:rgba(99,102,241,.22);
  --emerald:#10b981;--amber:#f59e0b;--rose:#ef4444;
  --radius:10px;--radius-sm:7px;
  --mono:"JetBrains Mono","Cascadia Code","Fira Code",Consolas,monospace;
  --sans:"Inter",-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  --transition:150ms ease;
}
[data-theme="light"]{
  --bg-canvas:#f0f2f5;--bg-card:#ffffff;--bg-surface:#f8f9fa;
  --bg-hover:#eef0f4;--border:rgba(0,0,0,.09);--border-hover:rgba(0,0,0,.18);
  --text-primary:#0f172a;--text-secondary:#374151;--text-muted:#6b7280;
  --accent:#4f46e5;--accent-light:#6366f1;--accent-glow:rgba(79,70,229,.12);
  --mono:"JetBrains Mono","Cascadia Code","Fira Code",Consolas,monospace;
  --sans:"Inter",-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  --transition:150ms ease;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;background:var(--bg-canvas);color:var(--text-primary);font-family:var(--sans);font-size:14px;line-height:1.6;overflow:hidden;transition:background var(--transition),color var(--transition)}
button,input,select,textarea{font:inherit;color:inherit}
button{cursor:pointer;border:none;background:none}
a{color:var(--accent);text-decoration:none}

/* ── Header ── */
.topbar{display:flex;align-items:center;gap:.85rem;padding:.7rem 1.25rem;background:var(--bg-card);border-bottom:1px solid var(--border);position:sticky;top:0;z-index:100;height:54px}
.brand{font-weight:700;font-size:15px;color:var(--text-primary);letter-spacing:-.3px;display:flex;align-items:center;gap:.55rem}
.brand-dot{width:9px;height:9px;border-radius:50%;background:var(--emerald);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
.mcp-pill{display:inline-flex;align-items:center;gap:.45rem;padding:.22rem .65rem;border-radius:99px;background:rgba(16,185,129,.1);color:var(--emerald);font-size:12px;font-family:var(--mono);font-weight:500}
.spacer{flex:1}
.runs-select{background:var(--bg-surface);border:1px solid var(--border);border-radius:var(--radius-sm);padding:.35rem .6rem;color:var(--text-secondary);font-size:13px;min-width:220px;outline:none;transition:border-color var(--transition)}
.runs-select:focus{border-color:var(--accent)}
.runs-select option{background:var(--bg-card);color:var(--text-primary)}
.budget-wrap{display:flex;align-items:center;gap:.6rem;width:180px}
.budget-label{font-size:12px;color:var(--text-muted);white-space:nowrap;font-family:var(--mono);font-weight:500}
.progress-bar{flex:1;height:5px;background:var(--bg-surface);border-radius:99px;overflow:hidden}
.progress-fill{height:100%;background:linear-gradient(90deg,var(--accent),var(--accent-light));border-radius:99px;transition:width .4s ease}

/* ── Tabs ── */
.tabs{display:flex;gap:.25rem;padding:.45rem 1.25rem;background:var(--bg-card);border-bottom:1px solid var(--border);align-items:center}
.tab-btn{padding:.5rem 1.1rem;border-radius:var(--radius-sm);color:var(--text-secondary);font-size:13px;font-weight:500;transition:all var(--transition);display:flex;align-items:center;gap:.4rem;border:1px solid transparent}
.tab-btn:hover{color:var(--text-primary);background:var(--bg-surface)}
.tab-btn.active{color:var(--text-primary);background:var(--bg-surface);border-color:var(--border);font-weight:600}
.badge{font-size:11px;padding:.1rem .45rem;border-radius:99px;font-family:var(--mono);font-weight:600}
.badge-emerald{background:rgba(16,185,129,.12);color:var(--emerald)}
.badge-amber{background:rgba(245,158,11,.12);color:var(--amber)}
.badge-rose{background:rgba(239,68,68,.12);color:var(--rose)}
.badge-accent{background:rgba(99,102,241,.12);color:var(--accent-light)}

/* ── Full-screen sections ── */
.workspace{display:flex;height:calc(100vh - 54px - 46px);overflow:hidden}
.tab-section{display:none;width:100%;height:100%;overflow:hidden;flex-direction:column}
.tab-section.active{display:flex}
.section-inner{flex:1;overflow-y:auto;padding:1.25rem;scrollbar-width:thin;scrollbar-color:var(--bg-surface) transparent}
.panel{background:var(--bg-card);border:1px solid var(--border);border-radius:var(--radius)}
.panel-head{padding:.75rem 1rem;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between}
.panel-title{font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.6px;color:var(--text-muted)}
.panel-body{padding:1rem}

/* ── Control Rail (Workbench left) ── */
.control-rail{width:440px;min-width:340px;max-width:480px;border-right:1px solid var(--border);overflow-y:auto;padding:.85rem;display:flex;flex-direction:column;gap:.85rem;scrollbar-width:thin;scrollbar-color:var(--bg-surface) transparent;align-self:stretch}
.canvas-main{flex:1;overflow-y:auto;padding:1.25rem;scrollbar-width:thin;scrollbar-color:var(--bg-surface) transparent;align-self:stretch}

/* ── Presets ── */
.preset-grid{display:flex;flex-direction:column;gap:.4rem}
.preset-chip{display:flex;align-items:center;gap:.6rem;padding:.65rem .75rem;border:1px solid var(--border);border-radius:var(--radius-sm);cursor:pointer;transition:all var(--transition);background:var(--bg-surface);text-align:left;width:100%}
.preset-chip:hover{border-color:var(--border-hover);background:var(--bg-hover)}
.preset-chip.active{border-color:var(--accent);background:rgba(99,102,241,.08);box-shadow:0 0 0 1px var(--accent)}
.preset-num{font-family:var(--mono);font-size:11px;color:var(--text-muted);width:24px;height:24px;display:flex;align-items:center;justify-content:center;border:1px solid var(--border);border-radius:5px;flex-shrink:0;font-weight:600}
.preset-chip.active .preset-num{border-color:var(--accent);color:var(--accent)}
.preset-info{flex:1;min-width:0}
.preset-name{font-size:13px;font-weight:600;color:var(--text-primary);margin-bottom:2px}
.preset-task{font-size:12px;color:var(--text-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

/* ── Dropzone ── */
.dropzone{border:2px dashed var(--border);border-radius:var(--radius);padding:1.5rem;text-align:center;color:var(--text-muted);cursor:pointer;transition:all var(--transition)}
.dropzone:hover,.dropzone.dragover{border-color:var(--accent);color:var(--text-secondary);background:rgba(99,102,241,.04)}
.dropzone svg{margin-bottom:.5rem;opacity:.6}
.file-chips{display:flex;flex-wrap:wrap;gap:.4rem;margin-top:.6rem}
.file-chip{display:inline-flex;align-items:center;gap:.4rem;padding:.25rem .55rem;background:var(--bg-surface);border:1px solid var(--border);border-radius:99px;font-size:12px;font-family:var(--mono);color:var(--text-secondary)}
.file-ext{font-size:10px;padding:.05rem .3rem;border-radius:3px;background:rgba(99,102,241,.15);color:var(--accent-light);font-weight:700}
.file-remove{color:var(--text-muted);cursor:pointer;font-size:15px;line-height:1;margin-left:.1rem}
.file-remove:hover{color:var(--rose)}

/* ── Controls ── */
.ctrl-row{display:flex;gap:.6rem}
.ctrl-input{flex:1;background:var(--bg-surface);border:1px solid var(--border);border-radius:var(--radius-sm);padding:.5rem .65rem;font-size:13px;color:var(--text-primary);outline:none;transition:border-color var(--transition)}
.ctrl-input:focus{border-color:var(--accent)}
.ctrl-label{font-size:12px;color:var(--text-muted);margin-bottom:.3rem;display:block;font-weight:500}
.btn-run{background:var(--accent);color:#fff;padding:.6rem 1.4rem;border-radius:var(--radius-sm);font-weight:600;font-size:14px;transition:all var(--transition);display:flex;align-items:center;gap:.4rem}
.btn-run:hover:not(:disabled){background:#4f46e5;box-shadow:0 0 16px var(--accent-glow)}
.btn-run:disabled{opacity:.35;cursor:not-allowed}
.btn-stop{background:var(--bg-surface);border:1px solid var(--border);color:var(--text-secondary);padding:.6rem 1.2rem;border-radius:var(--radius-sm);font-weight:500;font-size:14px;transition:all var(--transition)}
.btn-stop:hover:not(:disabled){border-color:var(--rose);color:var(--rose)}
.btn-stop:disabled{opacity:.25;cursor:not-allowed}
.task-textarea{width:100%;background:var(--bg-surface);border:1px solid var(--border);border-radius:var(--radius-sm);padding:.6rem;font-size:13px;color:var(--text-primary);resize:vertical;min-height:72px;outline:none;transition:border-color var(--transition);font-family:var(--sans)}
.task-textarea:focus{border-color:var(--accent)}

/* ── Timeline ── */
.timeline{position:relative;padding-left:32px}
.timeline::before{content:'';position:absolute;left:13px;top:10px;bottom:10px;width:1px;background:var(--border)}
.step-card{position:relative;margin-bottom:.6rem;background:var(--bg-surface);border:1px solid var(--border);border-radius:var(--radius-sm);overflow:hidden;transition:all var(--transition)}
.step-card:hover{border-color:var(--border-hover)}
.step-card.completed{border-left:3px solid var(--emerald)}
.step-card.error{border-left:3px solid var(--rose)}
.step-card.running{border-left:3px solid var(--accent);animation:step-pulse 1.5s infinite}
@keyframes step-pulse{0%,100%{border-left-color:var(--accent)}50%{border-left-color:rgba(99,102,241,.25)}}
.step-dot{position:absolute;left:3px;top:12px;width:10px;height:10px;border-radius:50%;border:2px solid var(--border);background:var(--bg-canvas);z-index:1}
.step-card.completed .step-dot{border-color:var(--emerald);background:var(--emerald)}
.step-card.error .step-dot{border-color:var(--rose);background:var(--rose)}
.step-card.running .step-dot{border-color:var(--accent);background:var(--accent)}
.step-header{display:flex;align-items:center;gap:.6rem;padding:.6rem .7rem;border-bottom:1px solid var(--border);cursor:pointer}
.tool-badge{font-size:11px;padding:.2rem .5rem;border-radius:4px;background:rgba(99,102,241,.12);color:var(--accent-light);font-weight:600;font-family:var(--mono)}
.step-label{font-size:13px;color:var(--text-primary);flex:1;font-weight:500}
.step-meta{display:flex;gap:1rem;font-family:var(--mono);font-size:12px;color:var(--text-muted)}
.step-meta .cost{color:var(--amber);font-weight:600}
.step-toggle{color:var(--text-muted);transition:transform var(--transition);font-size:14px}
.step-toggle.open{transform:rotate(90deg)}
.step-body{max-height:0;overflow:hidden;transition:max-height .3s ease}
.step-body.open{max-height:500px}
.step-json{padding:.6rem .7rem;font-size:12px;font-family:var(--mono);color:var(--text-secondary);line-height:1.7;white-space:pre-wrap;word-break:break-all;max-height:220px;overflow-y:auto}
.status-bar{display:flex;gap:.5rem;margin-bottom:1rem;flex-wrap:wrap}
.assumption-pill{padding:.25rem .6rem;border-radius:99px;background:rgba(99,102,241,.08);border:1px solid rgba(99,102,241,.18);color:#a5b4fc;font-size:12px;font-family:var(--mono);font-weight:500}
.verify-pass{background:rgba(16,185,129,.08);border:1px solid rgba(16,185,129,.2);color:var(--emerald)}
.verify-fail{background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.2);color:var(--rose)}

/* ── Artifacts ── */
.artifact-tabs{display:flex;gap:0;border-bottom:1px solid var(--border);overflow-x:auto}
.artifact-tab{padding:.55rem .85rem;font-size:13px;color:var(--text-muted);border-bottom:2px solid transparent;transition:all var(--transition);cursor:pointer;font-family:var(--mono);white-space:nowrap;flex-shrink:0}
.artifact-tab:hover{color:var(--text-secondary)}
.artifact-tab.active{color:var(--accent);border-bottom-color:var(--accent);font-weight:600}
.artifact-panel{padding:1rem;min-height:80px}
.md-table{width:100%;border-collapse:collapse;font-size:13px}
.md-table th,.md-table td{border:1px solid var(--border);padding:.5rem .75rem;text-align:left}
.md-table th{background:var(--bg-surface);color:var(--text-secondary);font-weight:600;font-size:12px}
.md-table tr:nth-child(even) td{background:var(--bg-surface)}
.code-viewer{background:var(--bg-surface);border:1px solid var(--border);border-radius:var(--radius-sm);padding:.75rem;font-family:var(--mono);font-size:12px;color:var(--text-secondary);white-space:pre-wrap;word-break:break-all;max-height:420px;overflow-y:auto;line-height:1.7}
.copy-btn{float:right;padding:.25rem .6rem;font-size:12px;background:var(--bg-surface);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text-muted);transition:all var(--transition)}
.copy-btn:hover{border-color:var(--accent);color:var(--accent)}
.audio-player{width:100%;margin-top:.5rem;border-radius:var(--radius-sm)}
.json-diff{font-size:12px;font-family:var(--mono);color:var(--text-secondary);white-space:pre-wrap;word-break:break-all;max-height:420px;overflow-y:auto;line-height:1.7}

/* ── MCP Playground ── */
.tool-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:1rem}
.tool-card{background:var(--bg-surface);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;transition:all var(--transition)}
.tool-card:hover{border-color:var(--border-hover);transform:translateY(-1px);box-shadow:0 4px 12px rgba(0,0,0,.15)}
.tool-card-head{padding:.7rem .85rem;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between}
.tool-card-title{font-size:13px;font-weight:600;color:var(--text-primary)}
.tool-card-desc{font-size:12px;color:var(--text-muted);padding:.5rem .85rem;border-bottom:1px solid var(--border)}
.tool-card-body{padding:.75rem;display:flex;flex-direction:column;gap:.5rem}
.param-input{background:var(--bg-card);border:1px solid var(--border);border-radius:var(--radius-sm);padding:.45rem .6rem;font-size:12px;color:var(--text-primary);width:100%;outline:none;transition:border-color var(--transition)}
.param-input:focus{border-color:var(--accent)}
.tool-result{background:var(--bg-card);border:1px solid var(--border);border-radius:var(--radius-sm);padding:.6rem;font-size:12px;font-family:var(--mono);color:var(--text-secondary);max-height:200px;overflow-y:auto;white-space:pre-wrap;word-break:break-all;line-height:1.6}
.tool-result-empty{color:var(--text-muted);font-style:italic;font-family:var(--sans)}

/* ── Benchmark ── */
.bench-table{width:100%;border-collapse:collapse;font-size:13px}
.bench-table th{text-align:left;padding:.65rem .8rem;background:var(--bg-surface);color:var(--text-muted);font-weight:600;border-bottom:1px solid var(--border);font-size:12px;text-transform:uppercase;letter-spacing:.4px}
.bench-table td{padding:.6rem .8rem;border-bottom:1px solid var(--border);color:var(--text-secondary)}
.bench-table tr:last-child td{border-bottom:none}
.bench-table tr:hover td{background:var(--bg-hover)}
.pass-rate{text-align:center;padding:1.5rem 1rem}
.pass-rate-value{font-size:52px;font-weight:800;color:var(--emerald);font-family:var(--mono);letter-spacing:-2px}
.pass-rate-label{font-size:13px;color:var(--text-muted);margin-top:.25rem}

/* ── Scrollbar ── */
::-webkit-scrollbar{width:7px;height:7px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--bg-surface);border-radius:4px}
::-webkit-scrollbar-thumb:hover{background:var(--border-hover)}

.hidden{display:none!important}
.loading-dots::after{content:'.';animation:dots 1.5s steps(4) infinite}
@keyframes dots{0%{content:''}25%{content:'.'}50%{content:'..'}75%{content:'...'}}

/* ── Theme Toggle ── */
.theme-toggle{display:inline-flex;align-items:center;justify-content:center;width:34px;height:34px;border-radius:var(--radius-sm);border:1px solid var(--border);background:var(--bg-surface);color:var(--text-secondary);transition:all var(--transition);cursor:pointer;flex-shrink:0}
.theme-toggle:hover{border-color:var(--border-hover);color:var(--text-primary)}
.theme-toggle .icon-moon{display:none}
.theme-toggle .icon-sun{display:block}
[data-theme="light"] .theme-toggle .icon-moon{display:block}
[data-theme="light"] .theme-toggle .icon-sun{display:none}
</style>
</head>
<body>

<!-- ── Top Bar ── -->
<header class="topbar">
  <div class="brand">
    <span class="brand-dot"></span>
    Bodhan Agent Studio
  </div>
  <div class="mcp-pill" id="mcp-status">Connected · 5 Tools · 2 Resources</div>
  <div class="spacer"></div>
  <select class="runs-select" id="runs-select"><option value="">Load Saved Run…</option></select>
  <div class="budget-wrap">
    <span class="budget-label" id="budget-label">₹0.00 / ₹20.00</span>
    <div class="progress-bar"><div class="progress-fill" id="budget-fill" style="width:0%"></div></div>
  </div>
  <button class="theme-toggle" id="theme-toggle" title="Toggle light/dark theme" aria-label="Toggle theme">
    <svg class="icon-sun" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>
    <svg class="icon-moon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>
  </button>
</header>

<!-- ── Tab Nav ── -->
<nav class="tabs">
  <button class="tab-btn active" data-tab="workbench">Agent Workbench</button>
  <button class="tab-btn" data-tab="mcp">MCP Playground <span class="badge badge-emerald">5</span></button>
  <button class="tab-btn" data-tab="benchmark">Benchmark Suite <span class="badge badge-emerald">10/10</span></button>
</nav>

<!-- ── Workspace ── -->
<div class="workspace">

  <!-- ══════════ TAB 1: Agent Workbench ══════════ -->
  <div class="tab-section active" id="tab-workbench">
    <div style="display:flex;flex:1;overflow:hidden">
    <aside class="control-rail">
      <!-- Presets -->
      <div class="panel">
        <div class="panel-head"><span class="panel-title">1. Preset Tasks</span></div>
        <div class="panel-body">
          <div class="preset-grid" id="preset-grid"></div>
        </div>
      </div>

      <!-- Custom Task -->
      <div class="panel">
        <div class="panel-head"><span class="panel-title">2. Custom Task</span></div>
        <div class="panel-body">
          <label class="ctrl-label">Task Prompt</label>
          <textarea class="task-textarea" id="custom-task" placeholder="Describe the task…" rows="3"></textarea>
        </div>
      </div>

      <!-- File Manager -->
      <div class="panel">
        <div class="panel-head"><span class="panel-title">3. File Manager</span></div>
        <div class="panel-body">
          <div class="dropzone" id="dropzone">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M17 8l-5-5-5 5M12 3v12"/></svg>
            <div style="font-size:12px">Drag & Drop Files or Browse</div>
          </div>
          <input type="file" id="file-input" multiple style="display:none">
          <div class="file-chips" id="file-chips"></div>
          <div style="margin-top:.4rem;display:flex;gap:.4rem">
            <input class="ctrl-input" id="manual-path" placeholder="Or enter path (e.g. demo_inputs/foo.pdf)">
            <button class="btn-add-file" style="padding:.35rem .6rem;background:var(--accent);color:#fff;border-radius:var(--radius-sm);font-size:11px;font-weight:600">Add</button>
          </div>
        </div>
      </div>

      <!-- Execution Controls -->
      <div class="panel">
        <div class="panel-head"><span class="panel-title">4. Parameters</span></div>
        <div class="panel-body">
          <div class="ctrl-row">
            <div style="flex:1">
              <label class="ctrl-label">Max Steps</label>
              <input class="ctrl-input" type="number" id="max-steps" value="30" min="1" max="100">
            </div>
            <div style="flex:1">
              <label class="ctrl-label">Max Cost (₹)</label>
              <input class="ctrl-input" type="number" id="max-cost" value="20" min="0.01" max="100" step="0.01">
            </div>
          </div>
          <div style="margin-top:.75rem;display:flex;gap:.5rem">
            <button class="btn-run" id="run-btn" disabled>▶ Run Task</button>
            <button class="btn-stop" id="stop-btn" disabled>■ Stop Run</button>
          </div>
        </div>
      </div>
    </aside>

    <main class="canvas-main">
      <!-- Status Pills -->
      <div class="status-bar hidden" id="status-bar"></div>

      <!-- Trace Timeline -->
      <div class="panel" style="margin-bottom:.85rem">
        <div class="panel-head">
          <span class="panel-title">Live Trace</span>
          <span style="font-size:12px;color:var(--text-muted)" id="trace-status">Status: idle</span>
        </div>
        <div class="panel-body">
          <div class="timeline" id="timeline"></div>
          <div id="empty-timeline" style="text-align:center;padding:2.5rem;color:var(--text-muted);font-size:13px">
            No steps yet — start a run to see the trace
          </div>
        </div>
      </div>

      <!-- Completion Panel -->
      <div class="panel hidden" id="completion-panel">
        <div class="panel-head"><span class="panel-title">Completion</span></div>
        <div class="panel-body" id="completion-body"></div>
      </div>

      <!-- Artifact Inspector -->
      <div class="panel">
        <div class="panel-head"><span class="panel-title">Artifact & Report Inspector</span></div>
        <div class="artifact-tabs" id="artifact-tabs"></div>
        <div class="artifact-panel" id="artifact-panel"></div>
      </div>
    </main>
    </div>
  </div>

  <!-- ══════════ TAB 2: MCP Playground ══════════ -->
  <div class="tab-section" id="tab-mcp">
    <div class="section-inner">
      <div class="panel">
        <div class="panel-head">
          <span class="panel-title">MCP Server Playground</span>
          <span style="font-size:12px;color:var(--text-muted)">Test all 5 tools directly over stdio</span>
        </div>
        <div class="panel-body">
          <div class="tool-grid" id="tool-grid"></div>
          <div style="margin-top:1.25rem;display:flex;gap:.6rem">
            <button class="btn-stop" onclick="__bodhan.__mcpShow('capabilities')" style="font-size:13px;padding:.5rem 1rem">View bodhan://capabilities</button>
            <button class="btn-stop" onclick="__bodhan.__mcpShow('voices')" style="font-size:13px;padding:.5rem 1rem">View bodhan://voices</button>
          </div>
          <div id="mcp-resource-panel" class="hidden" style="margin-top:.85rem">
            <div class="code-viewer" id="mcp-resource-content"></div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- ══════════ TAB 3: Benchmark Suite ══════════ -->
  <div class="tab-section" id="tab-benchmark">
    <div class="section-inner">
      <div class="panel">
        <div class="panel-head">
          <span class="panel-title">Self-Evaluation Benchmark Runner</span>
          <button class="btn-run" id="bench-run-btn">▶ Run 10-Task Benchmark</button>
        </div>
        <div class="panel-body">
          <div id="bench-summary" class="pass-rate hidden">
            <div class="pass-rate-value" id="bench-pass-rate">—</div>
            <div class="pass-rate-label">Overall Pass Rate</div>
          </div>
          <table class="bench-table" id="bench-table">
            <thead><tr><th>#</th><th>Task</th><th>Status</th><th>Result</th><th>Details</th></tr></thead>
            <tbody id="bench-tbody"></tbody>
          </table>
          <div id="bench-empty" style="text-align:center;padding:3rem;color:var(--text-muted);font-size:14px">Run the benchmark to see results</div>
        </div>
      </div>
    </div>
  </div>

</div><!-- /workspace -->

<script>
(function(){
"use strict";
const $=s=>document.querySelector(s);
const $$=s=>document.querySelectorAll(s);
const ESC={ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' };
const escHtml=s=>String(s).replace(/[&<>"']/g,c=>ESC[c]);
const fmtCost=v=>v==null?'—':`₹${Number(v).toFixed(2)}`;
const fmtMs=v=>v==null?'—':`${Math.round(v)}ms`;
function debounce(fn,ms){let t;return(...a)=>{clearTimeout(t);t=setTimeout(()=>fn(...a),ms);};}

// ── State ──
let presets=[];
let selectedPreset=null;
let customFiles=[];
let currentRunId=null;
let pollTimer=null;
let benchRunning=false;

// ── Init ──
async function init(){
  await loadPresets();
  await loadRuns();
  renderToolCards();
  bindEvents();
}

async function loadPresets(){
  const r=await fetch('/api/presets');
  presets=(await r.json()).presets||[];
  renderPresets(presets);
}

async function loadRuns(){
  const r=await fetch('/api/runs');
  const runs=(await r.json()).runs||[];
  const sel=$('#runs-select');
  sel.innerHTML='<option value="">Load Saved Run…</option>';
  runs.forEach(run=>{
    const opt=document.createElement('option');
    opt.value=run.run_id;
    opt.textContent=`${run.label} · ${run.status} · ${fmtCost(run.total_cost_inr)}`;
    sel.appendChild(opt);
  });
}

function renderPresets(ps){
  $('#preset-grid').innerHTML=ps.map((p,i)=>`
    <button class="preset-chip" data-id="${p.id}" data-files='${JSON.stringify(p.files)}'>
      <span class="preset-num">${i+1}</span>
      <div class="preset-info">
        <div class="preset-name">${escHtml(p.name)}</div>
        <div class="preset-task">${escHtml(p.task)}</div>
      </div>
    </button>`).join('');
  $$('.preset-chip').forEach(ch=>ch.addEventListener('click',()=>selectPreset(ch)));
}

function selectPreset(ch){
  selectedPreset=ch.dataset.id;
  customFiles=[];
  renderFileChips();
  $$('.preset-chip').forEach(c=>c.classList.toggle('active',c===ch));
  checkRunReady();
}

// ── File Manager ──
const dropzone=$('#dropzone'),fileInput=$('#file-input'),manualPath=$('#manual-path');
dropzone.addEventListener('click',()=>fileInput.click());
fileInput.addEventListener('change',e=>handleFiles(e.target.files));
dropzone.addEventListener('dragover',e=>{e.preventDefault();dropzone.classList.add('dragover');});
dropzone.addEventListener('dragleave',()=>dropzone.classList.remove('dragover'));
dropzone.addEventListener('drop',e=>{e.preventDefault();dropzone.classList.remove('dragover');handleFiles(e.dataTransfer.files);});
$('.btn-add-file').addEventListener('click',addManualFile);
manualPath.addEventListener('keydown',e=>{if(e.key==='Enter')addManualFile();});

async function handleFiles(files){
  if(!files.length)return;
  const form=new FormData();
  files.forEach(f=>form.append('file',f,f.name));
  try{
    const r=await fetch('/api/upload',{method:'POST',body:form});
    const data=await r.json();
    customFiles=[...customFiles,...(data.uploaded||[])];
    renderFileChips();checkRunReady();
  }catch(e){console.error(e);}
}

function addManualFile(){
  const p=manualPath.value.trim();
  if(!p)return;
  const ext=p.split('.').pop().toLowerCase();
  customFiles.push({name:p.split('/').pop(),path:p,size:0});
  manualPath.value='';
  renderFileChips();checkRunReady();
}

function renderFileChips(){
  $('#file-chips').innerHTML=customFiles.map((f,i)=>`
    <span class="file-chip">
      ${escHtml(f.name)}
      <span class="file-ext">${f.path? '.'+f.path.split('.').pop().slice(-4):''}</span>
      <span class="file-remove" data-i="${i}">×</span>
    </span>`).join('');
  $$('.file-remove').forEach(b=>b.addEventListener('click',()=>{customFiles.splice(+b.dataset.i,1);renderFileChips();checkRunReady();}));
}

// ── Run Controls ──
function checkRunReady(){
  const task=getActiveTask();
  const hasFiles=selectedPreset||customFiles.length>0;
  $('#run-btn').disabled=!task||!hasFiles;
}
function getActiveTask(){
  if(selectedPreset){const p=presets.find(x=>x.id===selectedPreset);return p?p.task:'';}
  return $('#custom-task').value.trim();
}
$('#custom-task').addEventListener('input',checkRunReady);

$('#run-btn').addEventListener('click',startRun);
$('#stop-btn').addEventListener('click',stopRun);
$('#runs-select').addEventListener('change',loadRun);

async function startRun(){
  const task=getActiveTask();
  if(!task)return;
  const files=(selectedPreset&&customFiles.length===0)?presets.find(p=>p.id===selectedPreset).files:[...new Set(customFiles.map(f=>f.path))];
  if(!files.length)return;
  $('#run-btn').disabled=true;
  $('#run-btn').textContent='Starting…';
  $('#stop-btn').disabled=false;
  try{
    const r=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({task,files,max_steps:+$('#max-steps').value,max_cost_inr:+$('#max-cost').value})});
    const data=await r.json();
    if(data.run_id){currentRunId=data.run_id;startPolling();}
  }catch(e){alert('Error: '+e.message);}
  finally{$('#run-btn').textContent='▶ Run Task';}
}
function stopRun(){
  clearInterval(pollTimer);
  currentRunId=null;
  $('#stop-btn').disabled=true;
  $('#trace-status').textContent='Status: stopped';
}
function startPolling(){
  clearInterval(pollTimer);
  pollTimer=setInterval(pollStatus,500);
}
async function pollStatus(){
  if(!currentRunId)return;
  try{
    const r=await fetch(`/api/status?run_id=${encodeURIComponent(currentRunId)}`);
    const data=await r.json();
    renderTrace(data.steps||[],data.report,data.completed);
    renderAssumptions(data.assumptions||[],data.verification_results||{});
    updateBudget(data.report);
    if(data.completed){
      clearInterval(pollTimer);
      $('#trace-status').textContent='Status: COMPLETED';
      renderCompletion(data);
      loadRuns();
    }
  }catch(e){console.error('Poll error:',e);}
}

function renderTrace(steps,report,completed){
  $('#empty-timeline').classList.toggle('hidden',steps.length>0||completed);
  $('#timeline').innerHTML=steps.map((s,i)=>{
    const cls=s.status||'';
    const args=s.arguments?s.arguments:(s.args||{});
    const out=s.output||s.result||{};
    return `<div class="step-card ${cls}" data-idx="${i}">
      <div class="step-dot"></div>
      <div class="step-header" style="cursor:pointer">
        <span class="tool-badge">${escHtml(s.tool||'step')}</span>
        <span class="step-label">${cls==='running'?'Running':`Step ${s.step}: ${escHtml(s.tool||'')}`}</span>
        <span class="step-meta">
          ${s.latency_ms!=null?`<span>${fmtMs(s.latency_ms)}</span>`:''}
          ${s.cost_inr!=null?`<span class="cost">${fmtCost(s.cost_inr)}</span>`:''}
          ${report?`<span class="cost">${fmtCost(report.total_cost_inr||0)}</span>`:''}
        </span>
        <span class="step-toggle">›</span>
      </div>
      <div class="step-body">
        ${s.plan_rationale?`<div class="step-json"><strong style="color:var(--text-muted)">Rationale:</strong>\n${escHtml(s.plan_rationale)}</div>`:''}
        <div class="step-json"><strong style="color:var(--text-muted)">Arguments:</strong>\n${escHtml(JSON.stringify(args,null,2))}</div>
        ${(s.result_summary||s.result)?`<div class="step-json"><strong style="color:var(--text-muted)">Result:</strong>\n${escHtml(String(s.result_summary||JSON.stringify(out)))}</div>`:''}
      </div>
    </div>`;
  }).join('');
  $$('.step-header').forEach(h=>h.addEventListener('click',()=>{
    const body=h.closest('.step-card').querySelector('.step-body');
    h.closest('.step-card').querySelector('.step-toggle').classList.toggle('open');
    body.classList.toggle('open');
  }));
}

function renderAssumptions(assumptions,vresults){
  const pills=[...assumptions.map(a=>`<span class="assumption-pill">${escHtml(typeof a==='string'?a:a.message||String(a))}</span>`),
    ...Object.entries(vresults).map(([k,v])=>`<span class="assumption-pill ${v? 'verify-pass':'verify-fail'}">${escHtml(k)}: ${v?'PASS':'FAIL'}</span>`)];
  $('#status-bar').innerHTML=pills.join('');
  $('#status-bar').classList.toggle('hidden',!pills.length);
}

function updateBudget(report){
  if(!report)return;
  const total=report.total_cost_inr||0,max=report.max_cost_inr||20;
  const pct=Math.min(100,Math.round(total/max*100));
  $('#budget-label').textContent=`₹${total.toFixed(2)} / ₹${max.toFixed(2)}`;
  $('#budget-fill').style.width=pct+'%';
  $('#budget-fill').style.background=pct>90?'var(--rose)':pct>70?'var(--amber)':'linear-gradient(90deg,var(--accent),#818cf8)';
}

function renderCompletion(data){
  const rp=$('#completion-body');
  const report=data.report||{};
  const artifacts=report.artifacts||[];
  // result md
  const panels=artifactTabs(['result.md','report.json'],artifacts,data.result_md||'',report);
  rp.innerHTML='';
  rp.style.display='flex';rp.style.flexDirection='column';rp.style.gap='.5rem';
  $('#completion-panel').classList.remove('hidden');
  renderArtifactTabs(panels,artifacts,data.result_md,report);
}

function artifactTabs(defaultTabs,artifacts,resultMd,report){
  const ts=[...defaultTabs];
  artifacts.forEach(a=>{const ext=a.split('.').pop().toLowerCase();if(ext==='json'&&ts.indexOf('report.json')===-1)ts.push('report.json');else if(ext!=='json'&&ext!=='md'&&ts.indexOf(a.split('/').pop())===-1)ts.push(a.split('/').pop());});
  return ts.filter(Boolean);
}

function renderArtifactTabs(tabs,artifacts,resultMd,report){
  $('#artifact-tabs').innerHTML=tabs.map((t,i)=>`<div class="artifact-tab ${i===0?'active':''}" data-tab="${escHtml(t)}">${escHtml(t)}</div>`).join('');
  renderArtifactTab(tabs[0]||'',artifacts,resultMd,report);
  $$('.artifact-tab').forEach(t=>t.addEventListener('click',()=>{
    $$('.artifact-tab').forEach(x=>x.classList.remove('active'));t.classList.add('active');
    renderArtifactTab(t.dataset.tab,artifacts,resultMd,report);
  }));
}

function renderArtifactTab(name,artifacts,resultMd,report){
  const p=$('#artifact-panel');
  if(name==='result.md'){
    p.innerHTML=renderMarkdownTable(resultMd);
  }else if(name==='report.json'){
    p.innerHTML=`<button class="copy-btn" onclick="navigator.clipboard.writeText(this.nextElementSibling.textContent)">Copy</button><div class="json-diff">${escHtml(JSON.stringify(report||{},null,2))}</div>`;
  }else{
    const art=artifacts.find(a=>a.split('/').pop()===name);
    if(!art){p.innerHTML='<span style="color:var(--text-muted)">Artifact not found</span>';return;}
    const ext=name.split('.').pop().toLowerCase();
    const url=`/api/artifact/${encodeURIComponent(art.replace(/\\/g,'/') )}`;
    if(['mp3','wav'].includes(ext)){
      p.innerHTML=`<audio class="audio-player" controls src="${url}"></audio>`;
    }else if(ext==='md'){
      p.innerHTML=renderMarkdownTable('');
      fetch(url).then(r=>r.text()).then(t=>{$('#artifact-panel').innerHTML=renderMarkdownTable(t);});
    }else if(['srt','txt'].includes(ext)){
      p.innerHTML=`<button class="copy-btn" onclick="navigator.clipboard.writeText(this.nextElementSibling.textContent)">Copy</button><div class="code-viewer" id="code-artifact"></div>`;
      fetch(url).then(r=>r.text()).then(t=>{$('#code-artifact').textContent=t;});
    }else if(ext==='json'){
      p.innerHTML=`<button class="copy-btn" onclick="navigator.clipboard.writeText(this.nextElementSibling.textContent)">Copy</button><div class="json-diff" id="json-artifact"></div>`;
      fetch(url).then(r=>r.text()).then(t=>{$('#json-artifact').textContent=t;});
    }else{
      p.innerHTML=`<img src="${url}" style="max-width:100%;border-radius:var(--radius-sm)">`;
    }
  }
}

function renderMarkdownTable(md){
  if(!md)return '';
  const lines=md.split('\n');let html='',inTable=false,tbl='';
  for(const line of lines){
    if(line.trim().startsWith('|')){
      if(!inTable){inTable=true;tbl='<table class="md-table"><thead><tr>';}
      const cells=line.split('|').filter((_,i,a)=>i>0&&i<a.length-1).map(c=>c.trim());
      const sep=line.includes('---');
      if(sep){tbl+='</tr></thead><tbody>';continue;}
      tbl+='<tr>'+cells.map(c=>`<td>${escHtml(c)}</td>`).join('')+ (inTable&&!tbl.includes('</thead>')?'</tr>':sep?'': '') ;
      if(tbl.includes('<thead>')&&!tbl.includes('</thead>')){
        // first row
      }
    }else{
      if(inTable){tbl+='</tbody></table>';html+=tbl;tbl='';inTable=false;}
      html+=line?`<div>${escHtml(line)}</div>`:'';
    }
  }
  if(inTable)html+=tbl+'</tbody></table>';
  return html;
}

function loadRun(){
  const rid=$('#runs-select').value;
  if(!rid)return;
  $('#run-btn').disabled=true;$('#stop-btn').disabled=true;
  currentRunId=rid;
  pollStatus().then(()=>{clearInterval(pollTimer);});
}

// ── Tab Navigation ──
$$('.tab-btn').forEach(btn=>btn.addEventListener('click',()=>{
  const tab=btn.dataset.tab;
  $$('.tab-btn').forEach(b=>b.classList.toggle('active',b===btn));
  ['workbench','mcp','benchmark'].forEach(t=>{
    const el=$(`#tab-${t}`);
    if(el)el.classList.toggle('active',t===tab);
  });
}));

// ── MCP Playground ──
const TOOLS_DEF=[
  {name:'inspect_media',desc:'Inspect media file metadata & cost estimate',params:[{k:'file_path',v:''}]},
  {name:'transcribe_audio',desc:'Transcribe audio/video to text',params:[{k:'file_path',v:''},{k:'language_hint',v:''},{k:'max_duration_sec',v:''}]},
  {name:'extract_document_text',desc:'Extract text from images/PDFs',params:[{k:'file_path',v:''},{k:'page_range',v:''}]},
  {name:'translate_text',desc:'Translate between Indic languages',params:[{k:'source_lang',v:'en'},{k:'target_lang',v:'mr'},{k:'text',v:''}]},
  {name:'synthesize_speech',desc:'Synthesize speech from text',params:[{k:'language',v:'mr'},{k:'output_file',v:'output.wav'},{k:'text',v:''}]},
];
function renderToolCards(){
  $('#tool-grid').innerHTML=TOOLS_DEF.map(t=>`
    <div class="tool-card">
      <div class="tool-card-head"><span class="tool-card-title">${escHtml(t.name)}</span><span class="badge badge-accent">Tool</span></div>
      <div class="tool-card-desc">${escHtml(t.desc)}</div>
      <div class="tool-card-body">
        ${t.params.map(p=>`<input class="param-input" data-tool="${t.name}" data-param="${p.k}" placeholder="${p.k}" value="${p.v}">`).join('')}
        <button class="btn-run" data-tool="${t.name}" style="font-size:11px;padding:.35rem .6rem">Call Tool</button>
        <div class="tool-result tool-result-empty" id="result-${t.name}">Awaiting call…</div>
      </div>
    </div>`).join('');
  $$('.tool-card-body .btn-run').forEach(btn=>btn.addEventListener('click',()=>callTool(btn.dataset.tool)));
}
async function callTool(name){
  const card=$(`#result-${name}`);
  card.classList.remove('tool-result-empty');card.textContent='Calling…';
  const params={};const inputs=$$(`.param-input[data-tool="${CSS.escape(name)}"]`);
  inputs.forEach(inp=>{if(inp.value.trim())params[inp.dataset.param]=inp.value.trim();});
  try{
    const t0=Date.now();
    const r=await fetch('/api/mcp/call',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tool:name,params})});
    const data=await r.json();const ms=Date.now()-t0;
    if(data.error)throw new Error(data.error);
    card.textContent=`[${ms}ms]\n${JSON.stringify(data.result||data,lat=>null,2)}`;
  }catch(e){card.textContent=`Error: ${e.message}`;}
}
window.__bodhan={__mcpShow:async function(which){
  const panel=$('#mcp-resource-panel'),content=$('#mcp-resource-content');
  panel.classList.remove('hidden');
  content.textContent='Loading…';
  const r=await fetch(`/api/mcp/${which}`);const data=await r.json();
  content.textContent=JSON.stringify(data,null,2);
}};

// ── Benchmark ──
async function runBenchmark(){
  if(benchRunning)return;benchRunning=true;
  const btn=$('#bench-run-btn');btn.disabled=true;btn.textContent='Running…';
  $('#bench-empty').classList.add('hidden');
  try{
    const r=await fetch('/api/eval',{method:'POST'});const data=await r.json();
    if(data.error)throw new Error(data.error);
    renderBenchmark(data.report);
  }catch(e){alert('Benchmark failed: '+e.message);}
  finally{btn.disabled=false;btn.textContent='▶ Run 10-Task Benchmark';benchRunning=false;}
}
$('#bench-run-btn').addEventListener('click',runBenchmark);

function renderBenchmark(report){
  const tests=report.results||[];
  const passCount=tests.filter(t=>t.passed).length;
  const total=report.total||tests.length;
  $('#bench-summary').classList.remove('hidden');
  $('#bench-pass-rate').textContent=`${report.passed}/${total}`;
  $('#bench-pass-rate').style.color=report.passed===total?'var(--emerald)':report.passed>=8?'var(--amber)':'var(--rose)';
  $('#bench-tbody').innerHTML=tests.map((t,i)=>`
    <tr>
      <td>${i+1}</td>
      <td>${escHtml(t.name||t.id||'')}</td>
      <td>${t.passed?'<span class="badge badge-emerald">PASS</span>':'<span class="badge badge-rose">FAIL</span>'}</td>
      <td style="font-family:var(--mono);color:var(--text-muted)">—</td>
      <td style="font-family:var(--mono);color:var(--text-muted)">${escHtml(t.status||'—')}</td>
      <td style="font-size:11px;color:var(--text-muted)">${escHtml(t.expected!==t.status?(t.expected+' → '+t.status):(t.error||''))}</td>
    </tr>`).join('');
}

// ── Theme Toggle ──
(function(){
  const STORAGE_KEY='bodhan-theme';
  const html=document.documentElement;
  function getPreferred(){
    const saved=localStorage.getItem(STORAGE_KEY);
    if(saved==='light'||saved==='dark')return saved;
    return window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';
  }
  function applyTheme(t){html.setAttribute('data-theme',t);try{localStorage.setItem(STORAGE_KEY,t);}catch(e){}}
  let current=getPreferred();
  applyTheme(current);
  $('#theme-toggle').addEventListener('click',()=>{
    const next=current==='dark'?'light':'dark';
    current=next;applyTheme(next);
  });
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change',e=>{
    if(!localStorage.getItem(STORAGE_KEY)){current=e.matches?'dark':'light';applyTheme(current);}
  });
})();

init();
})();
</script>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="bodhan_agent Modern Web UI")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=8080, help="Bind port")
    args = parser.parse_args()
    UI_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), WebUIHandler)
    print(f"Serving on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
