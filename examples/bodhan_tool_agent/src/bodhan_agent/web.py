"""A thin local web UI: start a task, watch the plan and trace live, open the artifacts.

    python -m bodhan_agent.web [--port 8000] [--host 127.0.0.1]

Each run is the normal CLI (`python -m bodhan_agent`) in a subprocess with its own
output directory under out/web/. The page follows that run's trace.jsonl as it is
written, so what it shows is exactly what the trace records. Finished runs (demo_runs/,
outputs/, earlier web runs) can be replayed the same way.

It binds to 127.0.0.1 by default: it can read any path the agent can, so it is a local
tool, not a service to expose.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import mimetypes
import os
import re
import secrets
import subprocess
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route

PAGE = Path(__file__).with_name("web_page.html")
POLL_S = 0.4
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass
class Run:
    id: str
    out_dir: Path
    task: str = ""
    process: subprocess.Popen[bytes] | None = None
    replay: bool = False
    files: list[str] = field(default_factory=list)

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None


class WebState:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.web_dir = self.root / "out" / "web"
        self.uploads = self.web_dir / "uploads"
        self.runs: dict[str, Run] = {}

    def past_runs(self) -> list[dict[str, str]]:
        found = []
        for base in ("demo_runs", "outputs", "out/web"):
            folder = self.root / base
            if not folder.is_dir():
                continue
            for d in sorted(folder.iterdir()):
                if (d / "trace.jsonl").is_file():
                    found.append({"id": f"{base}/{d.name}", "label": f"{base}/{d.name}", "task": _task_of(d)})
        return found

    def get(self, run_id: str) -> Run | None:
        if run_id in self.runs:
            return self.runs[run_id]
        known = {p["id"] for p in self.past_runs()}
        if run_id in known:
            run = Run(id=run_id, out_dir=self.root / run_id, task=_task_of(self.root / run_id), replay=True)
            self.runs[run_id] = run
            return run
        return None


def _task_of(run_dir: Path) -> str:
    report = run_dir / "report.json"
    if report.is_file():
        try:
            return str(json.loads(report.read_text(encoding="utf-8")).get("task") or "")
        except (OSError, ValueError):
            return ""
    trace = run_dir / "trace.jsonl"
    try:
        with trace.open(encoding="utf-8") as fh:
            first = json.loads(fh.readline() or "{}")
        return str(first.get("task") or "")
    except (OSError, ValueError):
        return ""


def _inside(base: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _artifacts(run_dir: Path) -> list[dict[str, Any]]:
    folder = run_dir / "artifacts"
    if not folder.is_dir():
        return []
    items = []
    for p in sorted(folder.rglob("*")):
        if p.is_file():
            mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            items.append({"name": p.relative_to(run_dir).as_posix(), "size": p.stat().st_size, "mime": mime})
    return items


def _final(run: Run) -> dict[str, Any]:
    report: dict[str, Any] = {}
    path = run.out_dir / "report.json"
    if path.is_file():
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            report = {}
    result_md = run.out_dir / "result.md"
    return {
        "status": report.get("status"),
        "answer": report.get("answer"),
        "total_cost_inr": report.get("total_cost_inr"),
        "elapsed_s": report.get("elapsed_s"),
        "assumptions": report.get("assumptions") or [],
        "verification_failures": report.get("verification_failures") or [],
        "artifacts": _artifacts(run.out_dir),
        "result_md": result_md.read_text(encoding="utf-8") if result_md.is_file() else None,
        "exit_code": run.process.poll() if run.process else None,
    }


async def _follow(run: Run) -> AsyncIterator[str]:
    trace = run.out_dir / "trace.jsonl"
    offset = 0
    pending = ""
    while True:
        if trace.is_file():
            with trace.open("rb") as fh:
                fh.seek(offset)
                chunk = fh.read()
            offset += len(chunk)
            pending += chunk.decode("utf-8", errors="replace")
            *lines, pending = pending.split("\n")
            for line in lines:
                if line.strip():
                    yield f"event: trace\ndata: {line}\n\n"
        if not run.running:
            if pending.strip():
                yield f"event: trace\ndata: {pending}\n\n"
            yield f"event: done\ndata: {json.dumps(_final(run), ensure_ascii=False)}\n\n"
            return
        await asyncio.sleep(POLL_S)


def build_app(root: Path | None = None, python: str = sys.executable) -> Starlette:
    state = WebState(root or Path.cwd())

    async def index(_: Request) -> Response:
        return HTMLResponse(PAGE.read_text(encoding="utf-8"))

    async def samples(_: Request) -> Response:
        base = state.root / "sample_data"
        files = []
        if base.is_dir():
            for p in sorted(base.rglob("*")):
                rel = p.relative_to(state.root).as_posix()
                hidden = any(part.startswith(("_", ".")) for part in p.relative_to(base).parts)
                if hidden:
                    continue
                if p.is_file() and p.name != "manifest.json":
                    files.append(rel)
                elif p.is_dir():
                    files.append(rel + "/")
        return JSONResponse({"files": files, "past_runs": state.past_runs()})

    async def upload(request: Request) -> Response:
        name = _SAFE_NAME.sub("_", request.query_params.get("name", "upload")).strip("._") or "upload"
        body = await request.body()
        if not body:
            return JSONResponse({"error": "empty upload"}, status_code=400)
        state.uploads.mkdir(parents=True, exist_ok=True)
        target = state.uploads / f"{hashlib.sha256(body).hexdigest()[:8]}_{name}"
        target.write_bytes(body)
        return JSONResponse({"path": target.relative_to(state.root).as_posix()})

    async def start(request: Request) -> Response:
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "expected JSON"}, status_code=400)
        task = str(body.get("task") or "").strip()
        if not task:
            return JSONResponse({"error": "task is required"}, status_code=400)
        files = [str(f).strip() for f in body.get("files") or [] if str(f).strip()]
        run_id = f"out/web/run_{secrets.token_hex(4)}"
        out_dir = state.root / run_id
        out_dir.mkdir(parents=True, exist_ok=True)
        cmd = [python, "-m", "bodhan_agent", "--task", task, "--out", str(out_dir)]
        for f in files:
            cmd += ["--files", f]
        for flag, key in (("--max-cost-inr", "max_cost_inr"), ("--max-steps", "max_steps")):
            if body.get(key) not in (None, ""):
                cmd += [flag, str(body[key])]
        log = (out_dir / "agent_stderr.log").open("wb")
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        process = subprocess.Popen(cmd, cwd=state.root, stdout=log, stderr=subprocess.STDOUT, env=env)
        state.runs[run_id] = Run(id=run_id, out_dir=out_dir, task=task, process=process, files=files)
        return JSONResponse({"id": run_id})

    async def events(request: Request) -> Response:
        run = state.get(request.query_params.get("id", ""))
        if run is None:
            return JSONResponse({"error": "unknown run"}, status_code=404)
        return StreamingResponse(_follow(run), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    async def artifact(request: Request) -> Response:
        run = state.get(request.query_params.get("id", ""))
        name = request.query_params.get("name", "")
        if run is None or not name:
            return JSONResponse({"error": "unknown run or file"}, status_code=404)
        path = run.out_dir / name
        if not _inside(run.out_dir, path) or not path.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(path)

    async def stop(request: Request) -> Response:
        run = state.runs.get(request.query_params.get("id", ""))
        if run and run.running and run.process:
            run.process.terminate()
        return JSONResponse({"ok": True})

    return Starlette(routes=[
        Route("/", index),
        Route("/api/samples", samples),
        Route("/api/upload", upload, methods=["POST"]),
        Route("/api/runs", start, methods=["POST"]),
        Route("/api/events", events),
        Route("/api/artifact", artifact),
        Route("/api/stop", stop, methods=["POST"]),
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bodhan_agent.web", description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--root", default=".", help="Project directory (sample_data/, demo_runs/, out/).")
    args = parser.parse_args(argv)
    import uvicorn

    print(f"Bodhan task agent UI on http://{args.host}:{args.port}")
    uvicorn.run(build_app(Path(args.root)), host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
