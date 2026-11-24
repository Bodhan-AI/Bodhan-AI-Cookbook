"""Loopback chat workspace with per-user threads and a shared artifact drawer."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import mimetypes
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse
import unicodedata

from dotenv import load_dotenv

from .agent import RECIPE_ROOT

MAX_REQUEST_BYTES = 250 * 1024 * 1024
MAX_ACTIVE_RUNS = 4
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
USER_RE = re.compile(r"^[A-Za-z0-9._@-]{1,80}$")
PAGE = Path(__file__).with_name("web_ui.html").read_text(encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _user(value: str) -> str:
    value = value.strip()
    if not USER_RE.fullmatch(value):
        raise ValueError("user_id must be 1–80 letters, numbers, dots, underscores, @, or hyphens")
    return value


def _safe_filename(value: str) -> str:
    value = unicodedata.normalize("NFC", value.replace("\\", "/"))
    name = Path(value).name
    name = "".join("_" if ord(char) < 32 or char in {"/", "\\", ":"} else char for char in name)
    name = name.strip(" .")[:180]
    return name or "uploaded-file"


def _bounded_trace_value(value, limit: int = 6000):
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + f"… [truncated {len(value) - limit} characters]"
    if isinstance(value, dict):
        return {str(key): _bounded_trace_value(item, limit) for key, item in list(value.items())[:60]}
    if isinstance(value, list):
        bounded = [_bounded_trace_value(item, limit) for item in value[:40]]
        if len(value) > 40:
            bounded.append(f"… [{len(value) - 40} more items]")
        return bounded
    return value


def _provider_host(base_url: str) -> str | None:
    return urlparse(base_url).hostname or None


class Workspace:
    """Persistent thread metadata and capacity-bounded cached files shared by one user tag."""

    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root.expanduser().resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.thread_root = self.output_root / ".web-threads"
        self.thread_root.mkdir(mode=0o700, exist_ok=True)
        try:
            self.thread_root.chmod(0o700)
        except OSError:
            pass
        self.lock = threading.RLock()
        self.start_lock = threading.Lock()
        self.jobs: dict[str, dict] = {}
        load_dotenv(RECIPE_ROOT / ".env")
        load_dotenv()
        self.cache, self.artifact_dir, self.artifact_max_files, self.publish_file, self.prune_artifacts = self._cache_api()
        self._recover_interrupted_threads()

    @staticmethod
    def _user_key(user_id: str) -> str:
        return hashlib.sha256(user_id.encode()).hexdigest()[:24]

    def _thread_path(self, user_id: str) -> Path:
        return self.thread_root / f"{self._user_key(user_id)}.json"

    def _read_threads(self, user_id: str) -> list[dict]:
        path = self._thread_path(user_id)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _write_threads(self, user_id: str, threads: list[dict]) -> None:
        path = self._thread_path(user_id)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=".tmp", dir=self.thread_root)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(threads, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def list_threads(self, user_id: str) -> list[dict]:
        with self.lock:
            threads = self._read_threads(user_id)
        return [{"id": row["id"], "title": row.get("title", "New conversation"), "updated": row.get("updated", "")} for row in sorted(threads, key=lambda row: row.get("updated", ""), reverse=True)]

    def create_thread(self, user_id: str) -> dict:
        now = _now()
        thread = {"id": uuid.uuid4().hex[:16], "user_id": user_id, "title": "New conversation", "created": now, "updated": now, "messages": []}
        with self.lock:
            threads = self._read_threads(user_id)
            threads.append(thread)
            self._write_threads(user_id, threads)
        return thread

    def delete_thread(self, user_id: str, thread_id: str) -> None:
        with self.lock:
            threads, thread = self._get_thread_locked(user_id, thread_id)
            if any(message.get("role") == "assistant" and message.get("status") == "running" for message in thread.get("messages", [])):
                raise ValueError("Wait for the active task to finish before deleting this conversation")
            threads.remove(thread)
            self._write_threads(user_id, threads)
            expected_parent = (self.output_root / "web-runs" / self._user_key(user_id)).resolve()
            run_root = (expected_parent / thread_id).resolve()
            if run_root.parent == expected_parent and run_root.is_dir():
                shutil.rmtree(run_root)

    def _get_thread_locked(self, user_id: str, thread_id: str) -> tuple[list[dict], dict]:
        if not ID_RE.fullmatch(thread_id):
            raise KeyError("Conversation not found")
        threads = self._read_threads(user_id)
        thread = next((row for row in threads if row.get("id") == thread_id and row.get("user_id") == user_id), None)
        if thread is None:
            raise KeyError("Conversation not found")
        return threads, thread

    def get_thread(self, user_id: str, thread_id: str) -> dict:
        with self.lock:
            _, thread = self._get_thread_locked(user_id, thread_id)
            result = json.loads(json.dumps(thread))
        # Add live progress from the agent trace without rewriting persisted chat history.
        for message in result["messages"]:
            if message.get("role") != "assistant" or not message.get("run_id"):
                continue
            events = self._trace_events(user_id, thread_id, message["run_id"])
            message["step_events"] = self._step_summaries(events)
            if message.get("status") == "running":
                partial = self._live_text_for(message["run_id"])
                if partial:
                    message["stream_content"] = partial
                progress = self._progress_for(message["run_id"])
                if progress:
                    message["progress"] = progress
        return result

    def _trace_events(self, user_id: str, thread_id: str, run_id: str) -> list[dict]:
        if not ID_RE.fullmatch(thread_id) or not ID_RE.fullmatch(run_id):
            return []
        with self.lock:
            try:
                _, thread = self._get_thread_locked(user_id, thread_id)
            except KeyError:
                return []
            if not any(item.get("run_id") == run_id for item in thread.get("messages", [])):
                return []
        run_root = self.output_root / "web-runs" / self._user_key(user_id) / thread_id / run_id
        try:
            roots = sorted(run_root.rglob("trace.jsonl"), key=lambda path: path.stat().st_mtime) if run_root.is_dir() else []
            if not roots:
                return []
            events = []
            for line in roots[-1].read_text(encoding="utf-8").splitlines():
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    events.append(_bounded_trace_value(value))
            return events
        except OSError:
            return []

    def _live_text_for(self, run_id: str) -> str | None:
        with self.lock:
            job = self.jobs.get(run_id)
        if not job:
            return None
        root = Path(job["run_root"])
        try:
            streams = sorted(root.rglob("live_response.md"), key=lambda path: path.stat().st_mtime) if root.is_dir() else []
            if not streams:
                return None
            return streams[-1].read_text(encoding="utf-8")[-40_000:]
        except OSError:
            return None

    @staticmethod
    def _step_summaries(events: list[dict]) -> list[dict]:
        steps: dict[str, dict] = {}
        order: list[str] = []
        for index, event in enumerate(events):
            kind = event.get("type")
            if kind == "plan":
                order.append(f"plan-{index}")
                steps[order[-1]] = {"kind": "plan", "title": "Workflow plan", "summary": str(event.get("plan", ""))[:700], "status": "ready"}
            elif kind in {"tool_call_started", "tool_call"}:
                key = f"step-{event.get('step', index)}"
                is_estimate = event.get("arguments", {}).get("dry_run") is True
                row = {
                    "kind": "tool",
                    "step": event.get("step"),
                    "tool": str(event.get("tool", "tool")),
                    "title": f"{'Estimating' if is_estimate else 'Running'} {event.get('tool', 'tool')}",
                    "status": "failed" if event.get("is_error") else ("running" if kind == "tool_call_started" else ("estimated" if is_estimate else "complete")),
                    "latency_seconds": event.get("latency_seconds"),
                    "cost_inr": event.get("cost_inr"),
                    "is_error": bool(event.get("is_error")),
                }
                result = event.get("result") or {}
                if isinstance(result, dict):
                    summary = result.get("error") or result.get("message") or result.get("text") or result.get("content")
                    if not summary:
                        for field in ("estimated_cost_inr", "cost_inr", "file_id", "duration_seconds", "pages"):
                            if field in result:
                                summary = f"{field.replace('_', ' ')}: {result[field]}"
                                break
                    if summary:
                        row["summary"] = str(summary)[:500]
                if key not in steps:
                    order.append(key)
                steps[key] = row
        return [steps[key] for key in order]

    def _update_thread(self, user_id: str, thread_id: str, fn) -> dict:
        with self.lock:
            threads, thread = self._get_thread_locked(user_id, thread_id)
            fn(thread)
            thread["updated"] = _now()
            self._write_threads(user_id, threads)
            return json.loads(json.dumps(thread))

    def _cache_api(self):
        # Import after dotenv has been loaded; the MCP server and UI share the same Redis and disk cache.
        from bodhan_cache import get_cache
        from bodhan_mcp.server import ARTIFACT_DIR, ARTIFACT_MAX_FILES, _prune_artifacts, _publish_file
        return get_cache(), ARTIFACT_DIR, ARTIFACT_MAX_FILES, _publish_file, _prune_artifacts

    def _recover_interrupted_threads(self) -> None:
        """Resolve abandoned chat spinners after a web-server restart."""
        with self.lock:
            for path in self.thread_root.glob("*.json"):
                try:
                    threads = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                changed = False
                for thread in threads if isinstance(threads, list) else []:
                    for message in thread.get("messages", []):
                        if message.get("role") != "assistant" or message.get("status") != "running":
                            continue
                        run_id = message.get("run_id", "")
                        run_root = self.output_root / "web-runs" / self._user_key(thread.get("user_id", "")) / thread.get("id", "") / run_id
                        reports = sorted(run_root.rglob("report.json")) if run_root.is_dir() else []
                        if reports:
                            report_path = reports[-1]
                            try:
                                report = json.loads(report_path.read_text(encoding="utf-8"))
                                result = report_path.parent / "result.md"
                                message.update({"status": report.get("status", "interrupted"), "content": result.read_text(encoding="utf-8") if result.is_file() else "Run report recovered after the workspace restarted.", "report": report, "finished_at": _now()})
                            except (OSError, json.JSONDecodeError):
                                message.update({"status": "interrupted", "content": "The workspace restarted before this run could be recovered. Check its run folder before retrying.", "finished_at": _now()})
                        else:
                            message.update({"status": "interrupted", "content": "The workspace restarted while this task was running. Its final status is unknown; check the run folder before retrying.", "finished_at": _now()})
                        changed = True
                if changed:
                    self._write_threads(threads[0].get("user_id", "local-user") if threads else "local-user", threads)

    def list_files(self, user_id: str) -> list[dict]:
        self._restore_thread_artifacts(user_id)
        self.prune_artifacts(user_id)
        files = []
        for item in self.cache.list_artifacts(user_id):
            files.append({"file_id": item["file_id"], "filename": item.get("filename", "file"), "media_type": item.get("media_type", "application/octet-stream"), "size_bytes": item.get("size_bytes", 0), "expires_in_seconds": None, "expires_label": f"oldest removed when adding file {self.artifact_max_files + 1}"})
        files.sort(key=lambda item: item["filename"].casefold())
        return files

    def _restore_thread_artifacts(self, user_id: str) -> None:
        """Restore metadata for attached files still on disk after an older Redis key expired."""
        root = self.artifact_dir.resolve()
        for thread in self._read_threads(user_id):
            for message in thread.get("messages", []):
                file_ids = message.get("file_ids") or []
                file_names = message.get("file_names") or []
                for index, raw_id in enumerate(file_ids):
                    file_id = str(raw_id).removeprefix("setu:=")
                    if not ID_RE.fullmatch(file_id) or self.cache.get_artifact(file_id):
                        continue
                    candidates = [path for path in root.glob(f"{file_id}.*") if path.is_file() and path.resolve().parent == root]
                    if len(candidates) != 1:
                        continue
                    path = candidates[0]
                    try:
                        stat = path.stat()
                    except OSError:
                        continue
                    filename = _safe_filename(str(file_names[index])) if index < len(file_names) else path.name
                    self.cache.set_artifact(file_id, {"path": str(path), "filename": filename, "media_type": mimetypes.guess_type(filename)[0] or "application/octet-stream", "size_bytes": stat.st_size, "created_at": stat.st_mtime, "owner_user_id": user_id}, 0)

    def upload_files(self, user_id: str, files: list[tuple[str, bytes]]) -> list[dict]:
        self.artifact_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            self.artifact_dir.chmod(0o700)
        except OSError:
            pass
        created_ids = set()
        for original, content in files:
            name = _safe_filename(original)
            with tempfile.TemporaryDirectory(prefix=".web-upload-", dir=self.artifact_dir) as staging_dir:
                staging_path = Path(staging_dir) / name
                staging_path.write_bytes(content)
                info = self.publish_file(staging_path, owner_user_id=user_id)
            if info.get("file_id"):
                created_ids.add(info["file_id"].removeprefix("setu:="))
        return [item for item in self.list_files(user_id) if item["file_id"] in created_ids]

    def file_for_user(self, user_id: str, file_id: str) -> dict | None:
        if not ID_RE.fullmatch(file_id):
            return None
        item = self.cache.get_artifact(file_id)
        if not item or item.get("owner_user_id") != user_id:
            return None
        path = Path(item.get("path", "")).resolve()
        root = self.artifact_dir.resolve()
        if root not in path.parents or not path.is_file():
            return None
        return {**item, "file_id": file_id, "path": path}

    def send_message(self, user_id: str, thread_id: str, text: str, file_ids: list[str], dry_run_only: bool, options: dict) -> str:
        text = text.strip()
        if not text or len(text) > 20_000:
            if len(text) > 20_000:
                raise ValueError("Messages are limited to 20,000 characters")
            raise ValueError("Write a message before sending")
        file_ids = list(dict.fromkeys(file_ids))
        if not file_ids and re.fullmatch(r"(?:run|do|continue|proceed|retry|translate|transcribe)(?:\s+(?:it|that|now))?[.!?]*", text.casefold()):
            with self.lock:
                _, existing_thread = self._get_thread_locked(user_id, thread_id)
                previous_attachments = next((message.get("file_ids", []) for message in reversed(existing_thread.get("messages", [])) if message.get("role") == "user" and message.get("file_ids")), [])
            file_ids = list(dict.fromkeys(previous_attachments))
        self._restore_thread_artifacts(user_id)
        attachments = []
        for file_id in file_ids:
            item = self.file_for_user(user_id, file_id)
            if not item:
                raise ValueError("An attached file is missing, evicted, or belongs to another user tag")
            attachments.append(item)
        with self.start_lock:
            with self.lock:
                if sum(job["status"] == "running" for job in self.jobs.values()) >= MAX_ACTIVE_RUNS:
                    raise ValueError(f"At most {MAX_ACTIVE_RUNS} task runs can be active at once")
            with self.lock:
                threads, thread = self._get_thread_locked(user_id, thread_id)
                if any(message.get("role") == "assistant" and message.get("status") == "running" for message in thread["messages"]):
                    raise ValueError("Wait for the current conversation turn to finish before sending another message")
                previous = [m for m in thread["messages"] if m.get("role") in {"user", "assistant"} and m.get("status") != "running"][-8:]
                history = "\n".join(f"{m['role'].upper()}: {m.get('content','')[:1500]}" for m in previous)
                full_task = (f"Conversation context (prior messages):\n{history}\n\n" if history else "") + f"CURRENT REQUEST:\n{text}"
                if attachments:
                    full_task += "\n\nAttached files are included as task inputs: " + ", ".join(a.get("filename", "file") for a in attachments)
                user_message = {"role": "user", "content": text, "created_at": _now(), "file_ids": file_ids, "file_names": [item.get("filename", "file") for item in attachments]}
                run_id = uuid.uuid4().hex[:16]
                assistant_message = {"role": "assistant", "content": "", "created_at": _now(), "status": "running", "run_id": run_id, "dry_run_only": dry_run_only}
                thread["messages"].extend([user_message, assistant_message])
                if thread.get("title") == "New conversation":
                    thread["title"] = text[:64] + ("…" if len(text) > 64 else "")
                thread["updated"] = _now()
                root = self.output_root / "web-runs" / self._user_key(user_id) / thread_id / run_id
                root.mkdir(parents=True, exist_ok=True)
                self._write_threads(user_id, threads)
            command = [sys.executable, "-m", "bodhan_agent", "--task", full_task, "--out", str(root), "--max-steps", str(options["max_steps"]), "--max-cost-inr", str(options["max_cost_inr"]), "--max-parallel-tools", str(options["max_parallel_tools"]), "--tool-timeout-seconds", str(options["tool_timeout_seconds"])]
            if attachments:
                command.extend(["--files", *[str(item["path"]) for item in attachments]])
            if dry_run_only:
                command.append("--dry-run-only")
            env = os.environ.copy()
            env["BODHAN_ARTIFACT_OWNER_ID"] = user_id
            try:
                process = subprocess.Popen(command, cwd=RECIPE_ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=(os.name == "posix"))
            except OSError as exc:
                self._finish_message(user_id, thread_id, run_id, f"Could not start the task agent: {exc}", "failed", dry_run_only)
                raise ValueError(f"Could not start task agent: {exc}") from exc
            job = {"id": run_id, "user_id": user_id, "thread_id": thread_id, "status": "running", "process": process, "output": "", "run_root": root, "dry_run_only": dry_run_only, "started_at": _now()}
            with self.lock:
                self.jobs[run_id] = job
            threading.Thread(target=self._watch_run, args=(run_id,), daemon=True).start()
        return run_id

    def _finish_message(self, user_id: str, thread_id: str, run_id: str, content: str, status: str, dry_run_only: bool, report: dict | None = None) -> None:
        def update(thread: dict) -> None:
            for message in reversed(thread["messages"]):
                if message.get("run_id") == run_id:
                    message.update({"content": content, "status": status, "finished_at": _now(), "dry_run_only": dry_run_only})
                    if report:
                        message["report"] = report
                    break
        self._update_thread(user_id, thread_id, update)

    def _watch_run(self, run_id: str) -> None:
        with self.lock:
            job = self.jobs[run_id]
            process = job["process"]
        output: list[str] = []
        assert process.stdout is not None
        for line in process.stdout:
            output.append(line)
            with self.lock:
                job["output"] = "".join(output)[-20000:]
        code = process.wait()
        root = Path(job["run_root"])
        candidates = sorted(root.rglob("report.json"), key=lambda path: path.stat().st_mtime) if root.exists() else []
        report = {}
        result = ""
        if candidates:
            report_path = candidates[-1]
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                report = {}
            result_path = report_path.parent / "result.md"
            if result_path.is_file():
                result = result_path.read_text(encoding="utf-8")
        status = report.get("status") or ("completed" if code == 0 else "failed")
        if not result:
            result = "The task agent exited without writing a result.\n\n" + "".join(output)[-5000:]
        self._finish_message(job["user_id"], job["thread_id"], run_id, result, status, job["dry_run_only"], report)
        with self.lock:
            job["status"] = status
            job["return_code"] = code
            job["output"] = "".join(output)[-20000:]

    def _progress_for(self, run_id: str) -> str | None:
        with self.lock:
            job = self.jobs.get(run_id)
        if not job:
            return None
        events = self._trace_events(job["user_id"], job["thread_id"], run_id)
        for event in reversed(events):
            if event.get("type") == "tool_call_started":
                phase = "Estimating" if event.get("arguments", {}).get("dry_run") is True else "Running"
                return f"{phase} {event.get('tool', 'tool')}…"
            if event.get("type") == "tool_call":
                return f"{event.get('tool', 'Tool')} finished · planning the next step…"
            if event.get("type") == "plan":
                return "Plan ready · selecting the next step…"
        if not events:
            return "Connecting to Bodhan MCP and planning…"
        return "Planning the workflow…"

    def shutdown(self) -> None:
        """Stop child agents cleanly so shutdown cannot leave paid work unattended."""
        with self.lock:
            processes = [job["process"] for job in self.jobs.values() if job.get("status") == "running"]
        for process in processes:
            if process.poll() is None:
                if os.name == "posix":
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                else:
                    process.terminate()
        for process in processes:
            if process.poll() is None:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    if os.name == "posix":
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    else:
                        process.kill()

    def config(self) -> dict:
        base = os.getenv("LLM_BASE_URL", "")
        model = os.getenv("LLM_MODEL", "").strip()
        key = os.getenv("LLM_API_KEY", "").strip()
        configured = bool(base and model and key and not any("<" in v or ">" in v for v in (base, model, key)))
        return {"llm_model": model or None, "llm_configured": configured, "llm_provider_host": _provider_host(base) if configured else None, "cache_folder": str(self.artifact_dir), "cache_max_files": self.artifact_max_files, "cache_backend": os.getenv("BODHAN_CACHE_BACKEND", "memory")}


class Handler(BaseHTTPRequestHandler):
    workspace: Workspace
    server_version = "BodhanSetu"
    sys_version = ""

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(f"[bodhan-setu-chat] {self.address_string()} {fmt % args}\n")

    def end_headers(self) -> None:
        allow_self_frame = getattr(self, "_allow_self_frame", False)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN" if allow_self_frame else "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        frame_ancestors = "'self'" if allow_self_frame else "'none'"
        self.send_header("Content-Security-Policy", f"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com data:; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors {frame_ancestors}")
        super().end_headers()

    def _is_local_request(self) -> bool:
        host_header = self.headers.get("Host", "")
        host = urlparse(f"//{host_header}").hostname
        if host not in {"127.0.0.1", "localhost", "::1"}:
            return False
        origin = self.headers.get("Origin")
        if origin:
            parsed = urlparse(origin)
            if parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.netloc != host_header:
                return False
        return True

    def _json(self, value: dict, status: int = 200) -> None:
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 1 or length > MAX_REQUEST_BYTES:
            raise ValueError(f"Request body must be between 1 byte and {MAX_REQUEST_BYTES // (1024 * 1024)} MB")
        return self.rfile.read(length)

    def _json_body(self) -> dict:
        body = json.loads(self._body())
        if not isinstance(body, dict):
            raise ValueError("Expected a JSON object")
        return body

    def _user_from_query(self) -> str:
        return _user(parse_qs(urlparse(self.path).query).get("user_id", ["local-user"])[0])

    def do_GET(self) -> None:
        if not self._is_local_request():
            self._json({"error": "The local workspace accepts requests from loopback hosts only"}, 403)
            return
        parsed = urlparse(self.path)
        if parsed.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        static_assets = {
            "/assets/bodhan-glow.png": ("bodhan-glow.png", "image/png"),
            "/assets/bodhan-glow-mask.png": ("bodhan-glow-mask.png", "image/png"),
            "/assets/bodhan-ai-mark.svg": ("bodhan-ai-mark.svg", "image/svg+xml"),
        }
        if parsed.path in static_assets:
            filename, media_type = static_assets[parsed.path]
            asset = Path(__file__).with_name("assets") / filename
            try:
                data = asset.read_bytes()
            except OSError:
                self._json({"error": "Chat background asset is unavailable"}, 404)
                return
            self.send_response(200)
            self.send_header("Content-Type", media_type)
            if media_type == "image/svg+xml":
                self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; sandbox")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path in {"/", "/index.html"}:
            data = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        try:
            if parsed.path == "/api/config":
                self._json(self.workspace.config())
                return
            if parsed.path == "/api/threads":
                self._json({"threads": self.workspace.list_threads(self._user_from_query())})
                return
            trace_parts = [unquote(part) for part in parsed.path.split("/") if part]
            if len(trace_parts) == 5 and trace_parts[:2] == ["api", "threads"] and trace_parts[3] == "traces":
                events = self.workspace._trace_events(self._user_from_query(), trace_parts[2], trace_parts[4])
                self._json({"events": events})
                return
            if parsed.path.startswith("/api/threads/"):
                thread_id = unquote(parsed.path.rsplit("/", 1)[-1])
                self._json(self.workspace.get_thread(self._user_from_query(), thread_id))
                return
            if parsed.path == "/api/files":
                self._json({"files": self.workspace.list_files(self._user_from_query())})
                return
            if parsed.path.startswith("/api/files/"):
                file_id = unquote(parsed.path.rsplit("/", 1)[-1])
                item = self.workspace.file_for_user(self._user_from_query(), file_id)
                if not item:
                    self._json({"error": "File not found, evicted, or owned by another user tag"}, 404)
                    return
                data = item["path"].read_bytes()
                inline_requested = parse_qs(parsed.query).get("inline", [""])[0] == "1"
                media_type = item.get("media_type", "application/octet-stream").lower()
                inline_safe = (
                    media_type in {"application/pdf", "application/json", "text/plain", "text/csv"}
                    or media_type.startswith(("audio/", "video/"))
                    or media_type in {"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "image/avif"}
                )
                self._allow_self_frame = inline_requested and inline_safe
                self.send_response(200)
                self.send_header("Content-Type", media_type)
                self.send_header("Content-Length", str(len(data)))
                filename = _safe_filename(item.get("filename", "download"))
                disposition = "inline" if inline_requested and inline_safe else "attachment"
                self.send_header("Content-Disposition", f'{disposition}; filename="download"; filename*=UTF-8\'\'{quote(filename, safe="")}')
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return
            self._json({"error": "Not found"}, 404)
        except KeyError as exc:
            self._json({"error": str(exc).strip("'")}, 404)
        except (ValueError, OSError, RuntimeError) as exc:
            self._json({"error": str(exc)}, 400)

    def do_POST(self) -> None:
        if not self._is_local_request():
            self._json({"error": "Cross-origin requests are not accepted"}, 403)
            return
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/threads":
                body = self._json_body()
                thread = self.workspace.create_thread(_user(str(body.get("user_id", "local-user"))))
                self._json({"thread": {"id": thread["id"], "title": thread["title"], "updated": thread["updated"]}}, 201)
                return
            if parsed.path == "/api/files":
                user_id, files = self._multipart_files()
                if not files:
                    raise ValueError("Choose one or more files to upload")
                stored = self.workspace.upload_files(user_id, files)
                self._json({"files": stored}, 201)
                return
            if parsed.path.startswith("/api/threads/") and parsed.path.endswith("/messages"):
                parts = [unquote(part) for part in parsed.path.split("/") if part]
                if len(parts) != 4:
                    self._json({"error": "Invalid conversation route"}, 404)
                    return
                body = self._json_body()
                user_id = _user(str(body.get("user_id", "local-user")))
                if not self.workspace.config()["llm_configured"]:
                    raise ValueError("Planner LLM is not configured. Set LLM_MODEL, LLM_BASE_URL, and LLM_API_KEY in the recipe .env file.")
                raw_file_ids = body.get("file_ids", [])
                if not isinstance(raw_file_ids, list) or any(not isinstance(item, str) for item in raw_file_ids):
                    raise ValueError("file_ids must be a list of file references")
                raw_dry_run = body.get("dry_run_only", True)
                if not isinstance(raw_dry_run, bool):
                    raise ValueError("dry_run_only must be true or false")
                options = {"max_steps": int(body.get("max_steps", 30)), "max_cost_inr": float(body.get("max_cost_inr", 20)), "max_parallel_tools": int(body.get("max_parallel_tools", 3)), "tool_timeout_seconds": float(body.get("tool_timeout_seconds", 240))}
                if options["max_steps"] < 1 or options["max_steps"] > 100 or options["max_parallel_tools"] < 1 or options["max_parallel_tools"] > 8 or not math.isfinite(options["max_cost_inr"]) or options["max_cost_inr"] < 0 or options["max_cost_inr"] > 100_000 or not math.isfinite(options["tool_timeout_seconds"]) or options["tool_timeout_seconds"] <= 0 or options["tool_timeout_seconds"] > 3600:
                    raise ValueError("Step/concurrency limits and timeout must be positive; cost must be non-negative")
                run_id = self.workspace.send_message(user_id, parts[2], str(body.get("text", "")), raw_file_ids, raw_dry_run, options)
                self._json({"run_id": run_id, "status": "running"}, 202)
                return
            self._json({"error": "Not found"}, 404)
        except KeyError as exc:
            self._json({"error": str(exc).strip("'")}, 404)
        except (ValueError, TypeError, json.JSONDecodeError, OSError, RuntimeError) as exc:
            self._json({"error": str(exc)}, 400)

    def do_DELETE(self) -> None:
        if not self._is_local_request():
            self._json({"error": "Cross-origin requests are not accepted"}, 403)
            return
        parsed = urlparse(self.path)
        parts = [unquote(part) for part in parsed.path.split("/") if part]
        if len(parts) != 3 or parts[:2] != ["api", "threads"]:
            self._json({"error": "Not found"}, 404)
            return
        try:
            self.workspace.delete_thread(self._user_from_query(), parts[2])
            self._json({"deleted": True})
        except KeyError as exc:
            self._json({"error": str(exc).strip("'")}, 404)
        except (ValueError, OSError, RuntimeError) as exc:
            self._json({"error": str(exc)}, 400)

    def _multipart_files(self) -> tuple[str, list[tuple[str, bytes]]]:
        content_type = self.headers.get("Content-Type", "")
        if not content_type.startswith("multipart/form-data"):
            raise ValueError("Upload requires multipart form data")
        from email.parser import BytesParser
        from email.policy import default

        raw = self._body()
        message = BytesParser(policy=default).parsebytes((f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n").encode() + raw)
        user_id = "local-user"
        files = []
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if name == "user_id":
                user_id = _user((part.get_payload(decode=True) or b"").decode("utf-8", "replace"))
            elif name == "files" and part.get_filename():
                filename = _safe_filename(part.get_filename())
                content = part.get_payload(decode=True) or b""
                if content:
                    files.append((filename, content))
                    if len(files) > 24:
                        raise ValueError("Upload no more than 24 files at once")
        return user_id, files


def main() -> None:
    load_dotenv(RECIPE_ROOT / ".env")
    load_dotenv()
    parser = argparse.ArgumentParser(description="Run the local Bodhan Setu chat workspace")
    parser.add_argument("--out", default="./outputs", help="Directory for chat metadata and task-agent run files")
    parser.add_argument("--host", default="127.0.0.1", help="Loopback address only")
    parser.add_argument("--port", type=int, default=7860)
    args = parser.parse_args()
    if args.port < 1 or args.port > 65535:
        parser.error("--port must be between 1 and 65535")
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("The chat workspace binds to loopback addresses only")
    workspace = Workspace(Path(args.out))
    handler = type("BodhanSetuChatHandler", (Handler,), {"workspace": workspace})
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Bodhan Setu chat workspace: http://{args.host}:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Bodhan Setu chat workspace", flush=True)
    finally:
        workspace.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
