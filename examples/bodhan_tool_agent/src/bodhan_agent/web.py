from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import math
import mimetypes
import ntpath
import os
import re
import shutil
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email.message import Message
from email.parser import BytesParser
from email.policy import default as email_policy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urlsplit

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from bodhan_agent.runner import TaskRunner
from bodhan_mcp.config import Settings
from bodhan_mcp.errors import ToolError

LOGGER = logging.getLogger(__name__)
MAX_REQUEST_BYTES = 100 * 1024 * 1024
MAX_UPLOAD_FILES = 20
MAX_TASK_CHARS = 4000
MAX_TRACE_BYTES_PER_POLL = 256 * 1024
MAX_JSON_REQUEST_BYTES = 1024 * 1024
MCP_CALL_TIMEOUT_SECONDS = 180
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
ROOT_FILES = {"result.md", "report.json", "trace.jsonl"}
MCP_TOOL_LABELS = {
    "transcribe_audio": "Indic-Transcribe",
    "translate_text": "Indic-Translate",
    "generate_speech": "Indic-Speak",
    "ocr_document": "Indic-OCR",
    "translate_subtitles": "Subtitle translation helper",
}
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def _is_sensitive_filename(filename: str) -> bool:
    name = filename.casefold()
    return name in {"keys.txt", ".env", ".env.example"} or name.startswith(".env.")


def _finite_number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    number = float(value)
    return number if math.isfinite(number) else 0.0


def _normalize_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    drive, _ = ntpath.splitdrive(normalized)
    parts = normalized.split("/")
    if (
        drive
        or normalized.startswith("/")
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise ValueError("The requested output path is invalid.")
    return PurePosixPath(*parts).as_posix()


@dataclass
class WebRun:
    run_id: str
    task: str
    input_dir: Path
    output_dir: Path
    max_steps: int
    max_cost_inr: float
    started_monotonic: float = field(default_factory=time.monotonic)
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    status: str = "queued"
    error: str | None = None
    thread: threading.Thread | None = None
    runner: TaskRunner | None = None
    stop_requested: bool = False
    input_snapshot_dir: Path | None = None
    stop_path: Path | None = None
    finished_at: str | None = None
    input_files: list[dict[str, Any]] = field(default_factory=list)


class RunConflict(Exception):
    pass


class RunManager:
    def __init__(
        self,
        runs_dir: Path,
        *,
        runner_factory: Callable[..., TaskRunner] = TaskRunner,
        mcp_budget_inr: float = 20.0,
    ) -> None:
        self.runs_dir = runs_dir.expanduser().resolve()
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.mcp_output_dir = self.runs_dir.parent / "mcp-inspector"
        self.mcp_output_dir.mkdir(parents=True, exist_ok=True)
        self._runner_factory = runner_factory
        self._runs: dict[str, WebRun] = {}
        self._lock = threading.RLock()
        self._cleared_run_ids = self._load_cleared_run_ids()
        self._mcp_budget_limit = Decimal(str(mcp_budget_inr))
        if not self._mcp_budget_limit.is_finite() or self._mcp_budget_limit <= 0:
            raise ValueError("The MCP Inspector budget must be a finite positive INR amount.")
        self._mcp_spent = Decimal("0")
        self._mcp_lock = threading.Lock()
        self._load_persisted_runs()
        self._discover_legacy_runs()

    @property
    def _cleared_run_ids_path(self) -> Path:
        return self.runs_dir / "cleared-run-ids.json"

    def _load_cleared_run_ids(self) -> set[str]:
        path = self._cleared_run_ids_path
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return set()
        except (OSError, ValueError) as exc:
            raise ValueError("The dashboard history clear marker could not be read.") from exc
        run_ids = data.get("run_ids") if isinstance(data, dict) else None
        if not isinstance(run_ids, list) or any(
            not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{32}", run_id)
            for run_id in run_ids
        ):
            raise ValueError("The dashboard history clear marker is invalid.")
        return set(run_ids)

    def _persist_cleared_run_ids(self) -> None:
        path = self._cleared_run_ids_path
        temporary = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        temporary.write_text(
            json.dumps({"run_ids": sorted(self._cleared_run_ids)}),
            encoding="utf-8",
        )
        os.replace(temporary, path)

    def _record_path(self, run_id: str) -> Path:
        return self.runs_dir / run_id / "run.json"

    def _record_roots(self) -> list[Path]:
        roots = [self.runs_dir]
        if self.runs_dir.name == ".bodhan-dashboard" and self.runs_dir.parent.name == "out":
            previous_catalog = self.runs_dir.parent.parent / ".bodhan-dashboard"
            if previous_catalog.resolve() != self.runs_dir.resolve():
                roots.append(previous_catalog)
        return roots

    def _persist_run(self, run: WebRun) -> None:
        with self._lock:
            record_path = self._record_path(run.run_id)
            record_path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "run_id": run.run_id,
                "task": run.task,
                "input_dir": str(run.input_dir),
                "input_snapshot_dir": (
                    str(run.input_snapshot_dir) if run.input_snapshot_dir else None
                ),
                "output_dir": str(run.output_dir),
                "max_steps": run.max_steps,
                "max_cost_inr": run.max_cost_inr,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "status": run.status,
                "error": run.error,
                "stop_requested": run.stop_requested,
                "stop_path": str(run.stop_path) if run.stop_path else None,
                "input_files": run.input_files,
            }
            temporary = record_path.with_name(
                f"{record_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
            )
            temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            for attempt in range(5):
                try:
                    os.replace(temporary, record_path)
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.01 * (attempt + 1))

    @staticmethod
    def _run_from_record(record: Any) -> WebRun | None:
        if not isinstance(record, dict):
            return None
        run_id = record.get("run_id")
        task = record.get("task")
        input_dir = record.get("input_dir")
        output_dir = record.get("output_dir")
        if (
            not isinstance(run_id, str)
            or not re.fullmatch(r"[0-9a-f]{32}", run_id)
            or not isinstance(task, str)
            or not isinstance(input_dir, str)
            or not isinstance(output_dir, str)
        ):
            return None
        try:
            run = WebRun(
                run_id,
                task,
                Path(input_dir),
                Path(output_dir),
                int(record.get("max_steps", 30)),
                float(record.get("max_cost_inr", 20.0)),
                started_at=str(record.get("started_at") or datetime.now(timezone.utc).isoformat()),
                status=str(record.get("status", "queued")),
                error=record.get("error") if isinstance(record.get("error"), str) else None,
                stop_requested=bool(record.get("stop_requested", False)),
                input_snapshot_dir=(
                    Path(record["input_snapshot_dir"])
                    if isinstance(record.get("input_snapshot_dir"), str)
                    else None
                ),
                stop_path=(
                    Path(record["stop_path"])
                    if isinstance(record.get("stop_path"), str)
                    else None
                ),
                finished_at=(
                    record["finished_at"]
                    if isinstance(record.get("finished_at"), str)
                    else None
                ),
                input_files=(
                    record["input_files"]
                    if isinstance(record.get("input_files"), list)
                    else []
                ),
            )
        except (TypeError, ValueError, OSError):
            return None
        return run

    def _load_persisted_runs(self) -> None:
        for root in self._record_roots():
            for record_path in root.glob("*/run.json"):
                try:
                    record = json.loads(record_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    LOGGER.warning("Could not read persistent run record %s", record_path)
                    continue
                run = self._run_from_record(record)
                if (
                    run is not None
                    and run.run_id not in self._runs
                    and run.run_id not in self._cleared_run_ids
                ):
                    self._runs[run.run_id] = run

    def _discover_legacy_runs(self) -> None:
        if self.runs_dir.name != ".bodhan-dashboard":
            return
        search_root = self.runs_dir.parent
        catalog_root = self.runs_dir.resolve()
        for report_path in search_root.rglob("report.json"):
            try:
                resolved_report = report_path.resolve()
                if (
                    catalog_root in resolved_report.parents
                    or not (report_path.parent / "trace.jsonl").is_file()
                ):
                    continue
                report = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(report, dict):
                continue
            run_id = report.get("run_id")
            task = report.get("task")
            if (
                not isinstance(run_id, str)
                or not re.fullmatch(r"[0-9a-f]{32}", run_id)
                or not isinstance(task, str)
                or run_id in self._runs
                or run_id in self._cleared_run_ids
            ):
                continue
            output_dir = report_path.parent.resolve()
            input_candidate = (
                output_dir.parent / "inputs"
                if output_dir.name == "run"
                else None
            )
            input_snapshot = input_candidate if input_candidate and input_candidate.is_dir() else None
            try:
                started = str(report.get("started_at") or datetime.fromtimestamp(
                    report_path.stat().st_mtime, timezone.utc
                ).isoformat())
                run = WebRun(
                    run_id,
                    task,
                    input_snapshot or output_dir.parent,
                    output_dir,
                    int(report.get("max_steps", 30)),
                    float(report.get("cost_budget_inr", 20.0)),
                    started_at=started,
                    status=str(report.get("status", "failed")),
                    finished_at=(
                        report.get("finished_at")
                        if isinstance(report.get("finished_at"), str)
                        else None
                    ),
                    input_snapshot_dir=input_snapshot,
                )
                self._runs[run_id] = run
                self._persist_run(run)
            except (OSError, TypeError, ValueError):
                LOGGER.warning("Could not register legacy run report %s", report_path)

    def clear_history(self) -> dict[str, int]:
        self._refresh_all_runs()
        with self._lock:
            active_runs = [
                run for run in self._runs.values()
                if run.status in {"queued", "running"}
            ]
            cleared_runs = [
                run for run in self._runs.values()
                if run.status not in {"queued", "running"}
            ]
            cleared_ids = {run.run_id for run in cleared_runs}
            new_ids = cleared_ids - self._cleared_run_ids
            if new_ids:
                self._cleared_run_ids.update(new_ids)
                try:
                    self._persist_cleared_run_ids()
                except OSError:
                    self._cleared_run_ids.difference_update(new_ids)
                    raise
            for run in cleared_runs:
                self._runs.pop(run.run_id, None)

        cleanup_warnings = 0
        for run in cleared_runs:
            for root in self._record_roots():
                run_root = root / run.run_id
                try:
                    if root.resolve() not in run_root.resolve().parents:
                        continue
                    (run_root / "run.json").unlink(missing_ok=True)
                except OSError:
                    cleanup_warnings += 1
                    LOGGER.exception("Could not remove the history record for run %s", run.run_id)
                snapshot = run.input_snapshot_dir
                if snapshot is None:
                    continue
                try:
                    resolved_root = root.resolve()
                    resolved_run_root = run_root.resolve()
                    resolved_snapshot = snapshot.resolve()
                    if (
                        resolved_root in resolved_run_root.parents
                        and resolved_snapshot == resolved_run_root / "inputs"
                    ):
                        shutil.rmtree(resolved_snapshot, ignore_errors=False)
                except FileNotFoundError:
                    continue
                except OSError:
                    cleanup_warnings += 1
                    LOGGER.exception("Could not remove archived inputs for run %s", run.run_id)
        return {
            "cleared_count": len(cleared_runs),
            "active_count": len(active_runs),
            "cleanup_warning_count": cleanup_warnings,
        }

    @staticmethod
    def _snapshot_input_files(source: Path, destination: Path) -> list[dict[str, Any]]:
        source = source.resolve(strict=True)
        destination.mkdir(parents=True, exist_ok=True)
        if source.is_file():
            candidates = [(source, Path(source.name))]
        elif source.is_dir():
            candidates = [
                (item, item.relative_to(source))
                for item in sorted(source.rglob("*"))
                if item.is_file()
            ]
        else:
            raise ValueError("The input path must be a file or directory.")
        candidates = [
            (original, relative)
            for original, relative in candidates
            if not _is_sensitive_filename(original.name)
        ]

        manifest: list[dict[str, Any]] = []
        destination_root = destination.resolve()
        for original, relative in candidates:
            target = destination.joinpath(*relative.parts)
            resolved_target = target.resolve()
            if destination_root not in resolved_target.parents:
                raise ValueError("An input filename resolves outside its run snapshot.")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, target)
            manifest.append({
                "path": relative.as_posix(),
                "name": original.name,
                "extension": original.suffix.lower(),
                "size_bytes": target.stat().st_size,
                "available": True,
            })
        return manifest

    def _refresh_run(self, run: WebRun) -> WebRun:
        try:
            with self._lock:
                record = json.loads(
                    self._record_path(run.run_id).read_text(encoding="utf-8")
                )
                latest = self._run_from_record(record)
                if latest is None:
                    return run
                run.task = latest.task
                run.input_dir = latest.input_dir
                run.input_snapshot_dir = latest.input_snapshot_dir
                run.output_dir = latest.output_dir
                run.max_steps = latest.max_steps
                run.max_cost_inr = latest.max_cost_inr
                run.started_at = latest.started_at
                run.finished_at = latest.finished_at
                run.status = latest.status
                run.error = latest.error
                run.stop_requested = latest.stop_requested
                run.stop_path = latest.stop_path
                run.input_files = latest.input_files
        except (OSError, ValueError):
            return run
        return run

    def _refresh_all_runs(self) -> None:
        self._load_persisted_runs()
        self._discover_legacy_runs()
        with self._lock:
            runs = list(self._runs.values())
        for run in runs:
            self._refresh_run(run)

    def start_run(
        self,
        task: str,
        files: list[tuple[str, bytes]],
        max_steps: int,
        max_cost_inr: float,
    ) -> WebRun:
        if any(_is_sensitive_filename(sanitize_filename(filename)) for filename, _ in files):
            raise ValueError("Secret/configuration files such as .env and keys.txt cannot be uploaded.")
        self._refresh_all_runs()
        with self._lock:
            if any(run.status in {"queued", "running"} for run in self._runs.values()):
                raise RunConflict("A run is already in progress. Wait for it to finish before starting another.")
            run_id = uuid.uuid4().hex
            run_root = self.runs_dir / run_id
            input_dir = run_root / "inputs"
            output_dir = run_root / "run"
            run = WebRun(
                run_id,
                task,
                input_dir,
                output_dir,
                max_steps,
                max_cost_inr,
                input_snapshot_dir=input_dir,
                stop_path=run_root / "stop.requested",
            )
            self._runs[run_id] = run

        try:
            input_dir.mkdir(parents=True)
            output_dir.mkdir()
            used_names: set[str] = set()
            for filename, content in files:
                safe_name = _unique_filename(filename, used_names)
                with (input_dir / safe_name).open("xb") as target:
                    target.write(content)
                run.input_files.append({
                    "path": safe_name,
                    "name": filename,
                    "extension": Path(filename).suffix.lower(),
                    "size_bytes": len(content),
                    "available": True,
                })
            self._persist_run(run)
        except OSError:
            LOGGER.exception("Could not prepare uploaded inputs for run %s", run_id)
            with self._lock:
                run.status = "failed"
                run.error = "Could not store the uploaded inputs. Check local disk space and permissions."
                run.finished_at = datetime.now(timezone.utc).isoformat()
            self._persist_run(run)
            return run

        self._launch_run(run)
        return run

    def start_path_run(
        self,
        task: str,
        input_path: Path,
        output_path: Path,
        max_steps: int,
        max_cost_inr: float,
    ) -> WebRun:
        self._refresh_all_runs()
        output_path = output_path.resolve()
        if any(
            (output_path / name).exists()
            for name in ("report.json", "trace.jsonl", "result.md", "artifacts")
        ):
            output_path = output_path / uuid.uuid4().hex
        with self._lock:
            if any(run.status in {"queued", "running"} for run in self._runs.values()):
                raise RunConflict("A run is already in progress. Wait for it to finish before starting another.")
            run_id = uuid.uuid4().hex
            run_root = self.runs_dir / run_id
            input_snapshot_dir = run_root / "inputs"
            manifest = self._snapshot_input_files(input_path, input_snapshot_dir)
            run = WebRun(
                run_id,
                task,
                input_path.resolve(),
                output_path,
                max_steps,
                max_cost_inr,
                input_snapshot_dir=input_snapshot_dir,
                stop_path=run_root / "stop.requested",
                input_files=manifest,
            )
            self._runs[run.run_id] = run
            self._persist_run(run)
        self._launch_run(run)
        return run

    def _launch_run(self, run: WebRun) -> None:
        thread = threading.Thread(
            target=self._execute,
            args=(run,),
            name=f"bodhan-web-run-{run.run_id[:8]}",
            daemon=False,
        )
        run.thread = thread
        thread.start()

    def _watch_stop_request(
        self, run: WebRun, runner: TaskRunner, stop_watcher: threading.Event
    ) -> None:
        while not stop_watcher.wait(0.1):
            if run.stop_path and run.stop_path.is_file():
                with self._lock:
                    run.stop_requested = True
                runner.request_stop()
                try:
                    self._persist_run(run)
                except OSError:
                    LOGGER.exception("Could not persist stop request for run %s", run.run_id)
                return

    def _execute(self, run: WebRun) -> None:
        with self._lock:
            run.status = "running"
        try:
            self._persist_run(run)
        except OSError:
            LOGGER.exception("Could not persist running status for run %s", run.run_id)
        runner: TaskRunner | None = None
        stop_watcher: threading.Event | None = None
        watcher_thread: threading.Thread | None = None
        try:
            settings = Settings.from_env(run.output_dir)
            runner = self._runner_factory(
                run.task,
                run.input_dir,
                run.output_dir,
                run.max_steps,
                run.max_cost_inr,
                settings,
            )
            runner.run_id = run.run_id
            with self._lock:
                run.runner = runner
                should_stop = run.stop_requested
            stop_method = getattr(runner, "request_stop", None)
            if should_stop and callable(stop_method):
                stop_method()
            if run.stop_path:
                stop_watcher = threading.Event()
                watcher_thread = threading.Thread(
                    target=self._watch_stop_request,
                    args=(run, runner, stop_watcher),
                    name=f"bodhan-stop-watch-{run.run_id[:8]}",
                    daemon=True,
                )
                watcher_thread.start()
            report = runner.run()
            status = str(report.get("status", "failed"))
            with self._lock:
                run.status = status
                if status not in {"completed", "partial", "impossible", "needs_user_input"}:
                    run.status = "failed"
                    run.error = str(report.get("summary") or "The task agent failed. See the run report.")
                run.finished_at = datetime.now(timezone.utc).isoformat()
        except ToolError as exc:
            LOGGER.warning("Run %s stopped: %s", run.run_id, exc.code)
            with self._lock:
                run.status = "failed"
                run.error = f"{exc.code}: {exc.message}"
                run.finished_at = datetime.now(timezone.utc).isoformat()
        except Exception:
            LOGGER.exception("Unexpected error in web run %s", run.run_id)
            with self._lock:
                run.status = "failed"
                run.error = "Unexpected run error. Check the local server log and run files."
                run.finished_at = datetime.now(timezone.utc).isoformat()
        finally:
            if stop_watcher:
                stop_watcher.set()
            if watcher_thread:
                watcher_thread.join()
            if runner is not None:
                try:
                    runner.service.close()
                except Exception:
                    LOGGER.exception("Could not close the tool service for run %s", run.run_id)
            with self._lock:
                run.runner = None
            try:
                self._persist_run(run)
            except OSError:
                LOGGER.exception("Could not persist final status for run %s", run.run_id)

    def request_stop(self, run: WebRun) -> bool:
        run = self.get_run(run.run_id) or run
        with self._lock:
            if run.status not in {"queued", "running"}:
                return False
            run.stop_requested = True
            runner = run.runner
            stop_path = run.stop_path
        if stop_path:
            stop_path.parent.mkdir(parents=True, exist_ok=True)
            stop_path.touch(exist_ok=True)
        stop_method = getattr(runner, "request_stop", None)
        if callable(stop_method):
            stop_method()
        self._persist_run(run)
        return True

    def request_stop_active(self) -> None:
        with self._lock:
            active_runs = [
                run for run in self._runs.values()
                if run.status in {"queued", "running"}
            ]
        for run in active_runs:
            self.request_stop(run)

    def wait_for_runs(self) -> None:
        with self._lock:
            threads = [run.thread for run in self._runs.values() if run.thread is not None]
        for thread in threads:
            thread.join()

    async def _stdio_tools(self) -> list[dict[str, Any]]:
        environment = os.environ.copy()
        environment.pop("BODHAN_COST_BUDGET_INR", None)
        environment["BODHAN_OUTPUT_DIR"] = str(self.mcp_output_dir)
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "bodhan_mcp"],
            env=environment,
        )
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                response = await session.list_tools()
                return [
                    {
                        "name": tool.name,
                        "label": MCP_TOOL_LABELS.get(tool.name, tool.name),
                        "description": tool.description or "",
                        "input_schema": tool.inputSchema,
                    }
                    for tool in response.tools
                ]

    def list_mcp_tools(self) -> dict[str, Any]:
        tools = asyncio.run(
            asyncio.wait_for(self._stdio_tools(), timeout=30),
        )
        with self._mcp_lock:
            limit = float(self._mcp_budget_limit)
            spent = float(self._mcp_spent)
        return {
            "tools": tools,
            "budget": {
                "limit_inr": limit,
                "spent_inr": spent,
                "remaining_inr": max(0.0, limit - spent),
            },
        }

    async def _stdio_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        remaining_budget: Decimal,
    ) -> dict[str, Any]:
        environment = os.environ.copy()
        environment["BODHAN_OUTPUT_DIR"] = str(self.mcp_output_dir)
        environment["BODHAN_COST_BUDGET_INR"] = str(remaining_budget)
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "bodhan_mcp"],
            env=environment,
        )
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                return {
                    "tool": tool_name,
                    "is_error": result.isError,
                    "structured_content": result.structuredContent,
                    "content": [
                        item.text
                        for item in result.content
                        if isinstance(getattr(item, "text", None), str)
                    ],
                }

    def call_mcp_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_name not in MCP_TOOL_LABELS:
            raise ValueError("Select one of the tools exposed by the running Bodhan MCP server.")
        started = time.perf_counter()
        with self._mcp_lock:
            remaining = self._mcp_budget_limit - self._mcp_spent
            if remaining <= 0:
                raise ValueError("The MCP Inspector budget is exhausted. Restart the dashboard to reset it.")
            try:
                result = asyncio.run(
                    asyncio.wait_for(
                        self._stdio_call(tool_name, arguments, remaining),
                        timeout=MCP_CALL_TIMEOUT_SECONDS,
                    )
                )
            except TimeoutError as exc:
                self._mcp_spent = self._mcp_budget_limit
                raise TimeoutError(
                    "The MCP call timed out; its provider outcome is uncertain, so the remaining inspector budget was reserved."
                ) from exc
            except Exception as exc:
                self._mcp_spent = self._mcp_budget_limit
                LOGGER.exception("MCP Inspector call failed for tool %s", tool_name)
                raise RuntimeError(
                    "The MCP call failed with an uncertain provider outcome, so the remaining inspector budget was reserved."
                ) from exc

            structured = result.get("structured_content")
            cost = _finite_number(
                structured.get("estimated_cost_inr", structured.get("cost_inr"))
                if isinstance(structured, dict) else 0
            )
            self._mcp_spent = min(
                self._mcp_budget_limit,
                self._mcp_spent + Decimal(str(cost)),
            )
            result["latency_seconds"] = round(time.perf_counter() - started, 3)
            result["estimated_cost_inr"] = cost
            result["budget"] = {
                "limit_inr": float(self._mcp_budget_limit),
                "spent_inr": float(self._mcp_spent),
                "remaining_inr": float(max(Decimal("0"), self._mcp_budget_limit - self._mcp_spent)),
            }
            return result

    def get_run(self, run_id: str) -> WebRun | None:
        if run_id not in self._runs:
            self._load_persisted_runs()
            self._discover_legacy_runs()
        with self._lock:
            run = self._runs.get(run_id)
        return self._refresh_run(run) if run is not None else None

    @staticmethod
    def _read_run_report(run: WebRun) -> dict[str, Any]:
        try:
            report = json.loads((run.output_dir / "report.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return report if isinstance(report, dict) else {}

    @staticmethod
    def _run_duration(run: WebRun, report: dict[str, Any]) -> float:
        value = report.get("duration_seconds")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return max(0.0, float(value))
        try:
            started = datetime.fromisoformat(run.started_at)
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            return max(
                0.0,
                (datetime.now(timezone.utc) - started.astimezone(timezone.utc)).total_seconds(),
            )
        except ValueError:
            return max(0.0, time.monotonic() - run.started_monotonic)

    def list_runs(self) -> list[dict[str, Any]]:
        self._refresh_all_runs()
        with self._lock:
            runs = list(self._runs.values())
        summaries = []
        for run in runs:
            report = self._read_run_report(run)
            verification = report.get("verification")
            checks = verification if isinstance(verification, list) else []
            verification_status = (
                "passed" if checks and all(
                    isinstance(item, dict) and item.get("status") == "passed"
                    for item in checks
                )
                else "failed" if any(
                    isinstance(item, dict) and item.get("status") == "failed"
                    for item in checks
                )
                else "not_reported"
            )
            cost = report.get("estimated_cost_inr")
            summaries.append({
                "run_id": run.run_id,
                "task": run.task,
                "status": run.status,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "duration_seconds": self._run_duration(run, report),
                "estimated_cost_inr": cost if isinstance(cost, (int, float)) else None,
                "tool_invocation_count": report.get("tool_invocation_count"),
                "verification_status": verification_status,
            })
        return sorted(
            summaries,
            key=lambda item: str(item["started_at"]),
            reverse=True,
        )

    def _input_files(self, run: WebRun, report: dict[str, Any]) -> list[dict[str, Any]]:
        root = run.input_snapshot_dir
        if root is not None and root.is_dir():
            resolved_root = root.resolve()
            files: list[dict[str, Any]] = []
            for path in sorted(root.rglob("*")):
                if not path.is_file():
                    continue
                try:
                    resolved = path.resolve()
                    relative = resolved.relative_to(resolved_root).as_posix()
                    size = resolved.stat().st_size
                except (OSError, ValueError):
                    continue
                files.append({
                    "path": relative,
                    "name": path.name,
                    "extension": path.suffix.lower(),
                    "size_bytes": size,
                    "available": True,
                })
            if files:
                return files

        report_files = report.get("input_files")
        if not isinstance(report_files, list):
            return run.input_files
        return [
            {
                "path": None,
                "name": item.get("name", "input"),
                "extension": item.get("extension", ""),
                "size_bytes": item.get("size_bytes"),
                "available": False,
            }
            for item in report_files
            if isinstance(item, dict)
        ]

    def download_input_path(self, run: WebRun, relative_path: str) -> Path:
        root = run.input_snapshot_dir
        if root is None or not root.is_dir():
            raise FileNotFoundError("This run has no archived input files.")
        relative = _normalize_relative_path(relative_path)
        requested = PurePosixPath(relative)
        resolved_root = root.resolve()
        resolved_path = (resolved_root / Path(*requested.parts)).resolve()
        if resolved_root not in resolved_path.parents or not resolved_path.is_file():
            raise FileNotFoundError("The requested input file is not available.")
        allowed_paths = {
            item["path"]
            for item in self._input_files(run, {})
            if isinstance(item.get("path"), str)
        }
        if relative not in allowed_paths:
            raise ValueError("Only archived input files for this run can be downloaded.")
        return resolved_path

    @staticmethod
    def preview_text_path(path: Path) -> str:
        if path.stat().st_size > MAX_TRACE_BYTES_PER_POLL:
            raise ValueError("Preview is limited to 256 KiB; download the full file instead.")
        return path.read_text(encoding="utf-8")

    def snapshot(self, run: WebRun, cursor: int) -> dict[str, Any]:
        run = self.get_run(run.run_id) or run
        events, next_cursor = _read_trace_events(run.output_dir / "trace.jsonl", cursor)
        report: dict[str, Any] = {}
        report_path = run.output_dir / "report.json"
        if report_path.is_file():
            try:
                if run.output_dir.resolve() not in report_path.resolve().parents:
                    raise OSError("The run report resolves outside the run directory.")
                loaded = json.loads(report_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    report = loaded
            except (OSError, ValueError):
                LOGGER.warning("Could not read the report for web run %s", run.run_id)
        with self._lock:
            status = run.status
            error = run.error
        report_verification = report.get("verification")
        verification = report_verification if isinstance(report_verification, list) else []
        report_failures = report.get("failed_operations")
        failures = report_failures if isinstance(report_failures, list) else []
        report_warnings = report.get("warnings")
        warnings = report_warnings if isinstance(report_warnings, list) else []
        metrics = {
            "steps": report.get("steps"),
            "max_steps": run.max_steps,
            "estimated_cost_inr": _finite_number(
                report.get("estimated_cost_inr"),
            ) if "estimated_cost_inr" in report else None,
            "cost_budget_inr": _finite_number(
                report.get("cost_budget_inr", run.max_cost_inr),
            ),
            "duration_seconds": _finite_number(
                report.get(
                    "duration_seconds",
                    time.monotonic() - run.started_monotonic,
                )
            ),
            "retry_count": report.get("retry_count"),
        }
        for metric_name in (
            "api_call_count",
            "tool_invocation_count",
            "provider_submission_count",
            "bodhan_provider_submission_count",
            "llm_provider_submission_count",
            "llm_request_count",
            "cache_hit_count",
            "estimated_charges_inr",
            "actual_cost_inr",
            "llm_cost_inr",
            "total_retry_count",
        ):
            if metric_name in report:
                metrics[metric_name] = report[metric_name]
        artifacts = self._existing_artifacts(run, report.get("artifacts"))
        final_artifacts, intermediate_artifacts = self._categorize_artifacts(
            run, report, artifacts
        )
        input_files = self._input_files(run, report)
        resolved_root = run.output_dir.resolve()
        available_files = []
        for name in sorted(ROOT_FILES):
            file_path = (resolved_root / name).resolve()
            if resolved_root in file_path.parents and file_path.is_file():
                available_files.append(name)
        return {
            "run_id": run.run_id,
            "task": run.task,
            "status": status,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "error": error,
            "stop_requested": run.stop_requested,
            "events": events,
            "cursor": next_cursor,
            "summary": report.get("summary"),
            "artifacts": artifacts,
            "final_artifacts": final_artifacts,
            "intermediate_artifacts": intermediate_artifacts,
            "available_files": available_files,
            "metrics": metrics,
            "verification": verification,
            "failed_operations": failures,
            "warnings": warnings,
            "assumptions": report.get("assumptions", []),
            "input_files": input_files,
            "operation_costs": report.get("operation_costs", []),
            "error_codes": report.get("error_codes", []),
            "report_available": bool(report),
        }

    @staticmethod
    def _existing_artifacts(run: WebRun, artifacts: Any) -> list[str]:
        if not isinstance(artifacts, list):
            return []
        resolved_root = run.output_dir.resolve()
        found: set[str] = set()
        for item in artifacts:
            if not isinstance(item, str):
                continue
            try:
                relative = _normalize_relative_path(item)
            except ValueError:
                continue
            if not relative.startswith("artifacts/"):
                continue
            requested = PurePosixPath(relative)
            resolved_path = (resolved_root / Path(*requested.parts)).resolve()
            if resolved_root in resolved_path.parents and resolved_path.is_file():
                found.add(relative)
        return sorted(found)

    @staticmethod
    def _categorize_artifacts(
        run: WebRun, report: dict[str, Any], artifacts: list[str]
    ) -> tuple[list[str], list[str]]:
        consumed_hashes: set[str] = set()
        trace_path = run.output_dir / "trace.jsonl"

        def collect_hashes(value: Any) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if key == "path_sha256" and isinstance(child, str):
                        consumed_hashes.add(child)
                    else:
                        collect_hashes(child)
            elif isinstance(value, list):
                for child in value:
                    collect_hashes(child)

        try:
            with trace_path.open(encoding="utf-8") as trace:
                for line in trace:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(event, dict) and event.get("event") == "tool_call":
                        collect_hashes(event.get("arguments"))
        except OSError:
            pass

        verification_artifacts: set[str] = set()
        verifications = report.get("verification")
        if isinstance(verifications, list):
            for check in verifications:
                if not isinstance(check, dict):
                    continue
                path = check.get("transcript_artifact")
                if isinstance(path, str):
                    try:
                        verification_artifacts.add(_normalize_relative_path(path))
                    except ValueError:
                        continue

        final: list[str] = []
        intermediate: list[str] = []
        for relative in artifacts:
            resolved_path = (run.output_dir / Path(*PurePosixPath(relative).parts)).resolve()
            path_hash = hashlib.sha256(resolved_path.as_posix().encode()).hexdigest()
            if relative in verification_artifacts or path_hash in consumed_hashes:
                intermediate.append(relative)
            else:
                final.append(relative)
        return final, intermediate

    def download_path(self, run: WebRun, relative_path: str) -> Path:
        relative = _normalize_relative_path(relative_path)
        requested = PurePosixPath(relative)
        if relative not in ROOT_FILES:
            report_path = run.output_dir / "report.json"
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise FileNotFoundError("Run artifacts are not available yet.") from exc
            if not isinstance(report, dict):
                raise FileNotFoundError("Run artifact metadata is not available.")
            raw_artifacts = report.get("artifacts", [])
            allowed_artifacts = set()
            if isinstance(raw_artifacts, list):
                for item in raw_artifacts:
                    if not isinstance(item, str):
                        continue
                    try:
                        allowed_artifacts.add(_normalize_relative_path(item))
                    except ValueError:
                        continue
            if relative not in allowed_artifacts or not relative.startswith("artifacts/"):
                raise ValueError("Only files listed in the run report can be downloaded.")

        resolved_root = run.output_dir.resolve()
        resolved_path = (resolved_root / Path(*requested.parts)).resolve()
        if resolved_root not in resolved_path.parents or not resolved_path.is_file():
            raise FileNotFoundError("The requested run file does not exist.")
        return resolved_path

    def download_mcp_path(self, artifact_path: str) -> Path:
        normalized = artifact_path.replace("\\", "/")
        requested = Path(normalized)
        artifact_root = (self.mcp_output_dir / "artifacts").resolve()
        if requested.is_absolute():
            resolved_path = requested.resolve()
        else:
            if any(part in {"", ".", ".."} for part in requested.parts):
                raise ValueError("The requested MCP artifact path is invalid.")
            resolved_path = (self.mcp_output_dir / requested).resolve()
        if artifact_root not in resolved_path.parents or not resolved_path.is_file():
            raise FileNotFoundError("The requested MCP artifact does not exist.")
        return resolved_path


def sanitize_filename(filename: str) -> str:
    name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    name = re.sub(r"[\x00-\x1f<>:\"|?*]", "_", name).rstrip(" .")
    if name.startswith(".."):
        name = name.lstrip(".")
    if not name or name in {".", ".."}:
        raise ValueError("An uploaded file has an invalid filename.")
    if name.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
        raise ValueError("An uploaded file uses a reserved system filename.")
    return name


def _unique_filename(filename: str, used_names: set[str]) -> str:
    safe_name = sanitize_filename(filename)
    if safe_name.casefold() not in used_names:
        used_names.add(safe_name.casefold())
        return safe_name
    path = Path(safe_name)
    index = 2
    while True:
        candidate = f"{path.stem}-{index}{path.suffix}"
        if candidate.casefold() not in used_names:
            used_names.add(candidate.casefold())
            return candidate
        index += 1


def parse_multipart(content_type: str, body: bytes) -> tuple[dict[str, str], list[tuple[str, bytes]]]:
    if "\r" in content_type or "\n" in content_type:
        raise ValueError("The multipart Content-Type header is invalid.")
    header = Message()
    header["Content-Type"] = content_type
    if header.get_content_type() != "multipart/form-data" or not header.get_boundary():
        raise ValueError("Use a multipart/form-data request to start a run.")
    try:
        envelope = (
            f"MIME-Version: 1.0\r\nContent-Type: {content_type}\r\n\r\n".encode("ascii")
            + body
        )
    except UnicodeEncodeError as exc:
        raise ValueError("The multipart Content-Type header must be ASCII.") from exc
    message = BytesParser(policy=email_policy).parsebytes(envelope)
    if not message.is_multipart():
        raise ValueError("The upload form could not be parsed.")

    fields: dict[str, str] = {}
    files: list[tuple[str, bytes]] = []
    accepted_fields = {"task", "max_steps", "max_cost_inr", "files"}
    for part in message.iter_parts():
        if part.get_content_disposition() != "form-data":
            continue
        name = part.get_param("name", header="content-disposition")
        if name not in accepted_fields:
            raise ValueError("The upload form contains an unsupported field.")
        payload = part.get_payload(decode=True) or b""
        filename = part.get_filename()
        if filename is not None:
            if name != "files":
                raise ValueError("Uploaded files must use the files field.")
            if filename == "" and not payload:
                continue
            files.append((sanitize_filename(filename), payload))
            if len(files) > MAX_UPLOAD_FILES:
                raise ValueError(f"Upload at most {MAX_UPLOAD_FILES} files per run.")
            continue
        if name == "files":
            raise ValueError("A files form entry must include a filename.")
        if name in fields:
            raise ValueError(f"The upload form contains duplicate {name} fields.")
        charset = part.get_content_charset() or "utf-8"
        try:
            fields[name] = payload.decode(charset)
        except (LookupError, UnicodeDecodeError) as exc:
            raise ValueError(f"The {name} form field must be valid text.") from exc

    if sum(len(content) for _, content in files) > MAX_REQUEST_BYTES:
        raise ValueError("The total upload exceeds the size limit.")
    return fields, files


def _read_trace_events(trace_path: Path, cursor: int) -> tuple[list[dict[str, Any]], int]:
    try:
        size = trace_path.stat().st_size
        if cursor < 0 or cursor > size:
            cursor = 0
        with trace_path.open("rb") as trace:
            trace.seek(cursor)
            content = trace.read(MAX_TRACE_BYTES_PER_POLL)
    except FileNotFoundError:
        return [], 0

    last_newline = content.rfind(b"\n")
    if last_newline < 0:
        return [], cursor
    complete_lines = content[: last_newline + 1]
    events = []
    for line in complete_lines.splitlines():
        if line:
            decoded = json.loads(line.decode("utf-8"))
            if isinstance(decoded, dict):
                events.append(decoded)
    return events, cursor + len(complete_lines)


class LocalWebServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def create_server(manager: RunManager, host: str = "127.0.0.1", port: int = 8765) -> LocalWebServer:
    if host not in ALLOWED_HOSTS:
        raise ValueError("The web UI can bind only to localhost or 127.0.0.1.")

    class Handler(BaseHTTPRequestHandler):
        server_version = "BodhanLocalUI/1.0"

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.manager = manager
            super().__init__(*args, **kwargs)

        def _send_json(self, status: HTTPStatus, value: dict[str, Any]) -> None:
            content = json.dumps(value, ensure_ascii=False).encode("utf-8")
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(content)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                return

        def _send_error_json(self, status: HTTPStatus, message: str) -> None:
            self._send_json(status, {"error": message})

        def _request_is_local(self, require_origin: bool = False) -> bool:
            host_header = self.headers.get("Host", "")
            try:
                host = urlsplit(f"http://{host_header}")
                port = host.port or 80
            except ValueError:
                return False
            if host.hostname not in ALLOWED_HOSTS or port != self.server.server_port:
                return False

            origin = self.headers.get("Origin")
            if not origin:
                return not require_origin
            try:
                parsed_origin = urlsplit(origin)
                origin_port = parsed_origin.port or 80
            except ValueError:
                return False
            return (
                parsed_origin.scheme == "http"
                and parsed_origin.hostname in ALLOWED_HOSTS
                and origin_port == self.server.server_port
            )

        def do_GET(self) -> None:
            if not self._request_is_local():
                self._send_error_json(HTTPStatus.FORBIDDEN, "The local web UI accepts localhost requests only.")
                return
            parsed = urlsplit(self.path)
            if parsed.path == "/":
                try:
                    html = Path(__file__).with_name("web_ui.html").read_bytes()
                except OSError:
                    self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, "The web UI page is unavailable.")
                    return
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.send_header("Cache-Control", "no-store")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                    "script-src 'self' 'unsafe-inline'; connect-src 'self'; "
                    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
                )
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(html)
                return

            if parsed.path == "/api/health":
                self._send_json(HTTPStatus.OK, {"status": "ok"})
                return

            if parsed.path == "/api/runs":
                self._send_json(HTTPStatus.OK, {"runs": self.manager.list_runs()})
                return

            match = re.fullmatch(
                r"/api/runs/([0-9a-f]{32})/(input-preview|input-download|input-file-preview)",
                parsed.path,
            )
            if match:
                run = self.manager.get_run(match.group(1))
                if run is None:
                    self._send_error_json(HTTPStatus.NOT_FOUND, "Run not found.")
                    return
                relative_path = parse_qs(parsed.query).get("path", [""])[0]
                try:
                    path = self.manager.download_input_path(run, relative_path)
                    if match.group(2) == "input-preview":
                        content = self.manager.preview_text_path(path)
                        self._send_json(
                            HTTPStatus.OK,
                            {"path": relative_path, "content": content},
                        )
                    elif match.group(2) == "input-file-preview":
                        content_type = mimetypes.guess_type(path.name)[0] or ""
                        if content_type not in {
                            "application/pdf",
                            "image/jpeg",
                            "image/png",
                            "image/webp",
                        } and not content_type.startswith("audio/"):
                            raise ValueError("This file type is not available for inline preview.")
                        self._send_file(path, inline=True)
                    else:
                        self._send_file(path)
                except ValueError as exc:
                    self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                except (FileNotFoundError, UnicodeDecodeError):
                    self._send_error_json(HTTPStatus.NOT_FOUND, "This input file is not available as a text preview.")
                except OSError:
                    self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, "Could not read the input file.")
                return

            match = re.fullmatch(r"/api/runs/([0-9a-f]{32})", parsed.path)
            if match:
                run = self.manager.get_run(match.group(1))
                if run is None:
                    self._send_error_json(HTTPStatus.NOT_FOUND, "Run not found.")
                    return
                query = parse_qs(parsed.query)
                try:
                    cursor = int(query.get("cursor", ["0"])[0])
                except ValueError:
                    self._send_error_json(HTTPStatus.BAD_REQUEST, "Trace cursor must be an integer.")
                    return
                try:
                    self._send_json(HTTPStatus.OK, self.manager.snapshot(run, cursor))
                except (OSError, ValueError, json.JSONDecodeError):
                    self._send_error_json(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        "Could not read the run trace. Check the local output files.",
                    )
                return

            match = re.fullmatch(r"/api/runs/([0-9a-f]{32})/preview", parsed.path)
            if match:
                run = self.manager.get_run(match.group(1))
                if run is None:
                    self._send_error_json(HTTPStatus.NOT_FOUND, "Run not found.")
                    return
                relative_path = parse_qs(parsed.query).get("path", [""])[0]
                try:
                    path = self.manager.download_path(run, relative_path)
                    content = self.manager.preview_text_path(path)
                except ValueError as exc:
                    self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                    return
                except (FileNotFoundError, UnicodeDecodeError):
                    self._send_error_json(HTTPStatus.NOT_FOUND, "This file is not available as a text preview.")
                    return
                except OSError:
                    self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, "Could not read the run file.")
                    return
                self._send_json(HTTPStatus.OK, {"path": relative_path, "content": content})
                return

            match = re.fullmatch(r"/api/runs/([0-9a-f]{32})/file-preview", parsed.path)
            if match:
                run = self.manager.get_run(match.group(1))
                if run is None:
                    self._send_error_json(HTTPStatus.NOT_FOUND, "Run not found.")
                    return
                relative_path = parse_qs(parsed.query).get("path", [""])[0]
                try:
                    file_path = self.manager.download_path(run, relative_path)
                    content_type = mimetypes.guess_type(file_path.name)[0] or ""
                    if content_type not in {
                        "application/pdf",
                        "image/jpeg",
                        "image/png",
                        "image/webp",
                    } and not content_type.startswith("audio/"):
                        raise ValueError("This file type is not available for inline preview.")
                    self._send_file(file_path, inline=True)
                except ValueError as exc:
                    self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                except FileNotFoundError as exc:
                    self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
                except OSError:
                    self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, "Could not read the run file.")
                return

            if parsed.path == "/api/mcp/tools":
                try:
                    discovery = self.manager.list_mcp_tools()
                except Exception:
                    LOGGER.exception("Could not discover tools from the Bodhan MCP server.")
                    self._send_error_json(
                        HTTPStatus.BAD_GATEWAY,
                        "Could not connect to `python -m bodhan_mcp`. Check the server environment and local logs.",
                    )
                    return
                self._send_json(HTTPStatus.OK, discovery)
                return

            if parsed.path == "/api/mcp/download":
                artifact_path = parse_qs(parsed.query).get("path", [""])[0]
                try:
                    self._send_file(self.manager.download_mcp_path(artifact_path))
                except ValueError as exc:
                    self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                except FileNotFoundError as exc:
                    self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
                except OSError:
                    self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, "Could not read the MCP artifact.")
                return

            match = re.fullmatch(r"/api/runs/([0-9a-f]{32})/download", parsed.path)
            if match:
                run = self.manager.get_run(match.group(1))
                if run is None:
                    self._send_error_json(HTTPStatus.NOT_FOUND, "Run not found.")
                    return
                relative_path = parse_qs(parsed.query).get("path", [""])[0]
                try:
                    self._send_file(self.manager.download_path(run, relative_path))
                except ValueError as exc:
                    self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                except FileNotFoundError as exc:
                    self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
                except OSError:
                    self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, "Could not read the run file.")
                return

            self._send_error_json(HTTPStatus.NOT_FOUND, "Route not found.")

        def _send_file(self, path: Path, *, inline: bool = False) -> None:
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            size = path.stat().st_size
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            disposition = "inline" if inline else "attachment"
            self.send_header("Content-Disposition", f"{disposition}; filename*=UTF-8''{quote(path.name)}")
            self.end_headers()
            with path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    self.wfile.write(chunk)

        def do_POST(self) -> None:
            if not self._request_is_local(require_origin=True):
                self._send_error_json(
                    HTTPStatus.FORBIDDEN,
                    "This action requires a request from the local web UI.",
                )
                return
            path = urlsplit(self.path).path
            if path == "/api/runs/clear-history":
                try:
                    result = self.manager.clear_history()
                except OSError:
                    LOGGER.exception("Could not clear dashboard run history.")
                    self._send_error_json(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        "Could not clear run history. Check the local server log.",
                    )
                    return
                self._send_json(HTTPStatus.OK, result)
                return
            stop_match = re.fullmatch(r"/api/runs/([0-9a-f]{32})/stop", path)
            if stop_match:
                run = self.manager.get_run(stop_match.group(1))
                if run is None:
                    self._send_error_json(HTTPStatus.NOT_FOUND, "Run not found.")
                    return
                accepted = self.manager.request_stop(run)
                self._send_json(
                    HTTPStatus.OK if accepted else HTTPStatus.CONFLICT,
                    {
                        "status": run.status,
                        "stop_requested": run.stop_requested,
                        "message": (
                            "Stop requested. Any in-flight model/tool call will finish before the agent stops."
                            if accepted else "This run has already stopped."
                        ),
                    },
                )
                return
            if path == "/api/dashboard/close":
                self._send_json(HTTPStatus.OK, {"status": "closing"})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if path == "/api/mcp/call":
                try:
                    content_length = int(self.headers.get("Content-Length", ""))
                except ValueError:
                    self._send_error_json(HTTPStatus.LENGTH_REQUIRED, "A valid Content-Length header is required.")
                    return
                if content_length <= 0 or content_length > MAX_JSON_REQUEST_BYTES:
                    self._send_error_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "MCP call JSON must be between 1 byte and 1 MiB.")
                    return
                try:
                    payload = json.loads(self.rfile.read(content_length))
                    if not isinstance(payload, dict):
                        raise ValueError("MCP call input must be a JSON object.")
                    tool_name = payload.get("tool")
                    arguments = payload.get("arguments", {})
                    if not isinstance(tool_name, str) or not isinstance(arguments, dict):
                        raise ValueError("Provide a tool name and an arguments JSON object.")
                    result = self.manager.call_mcp_tool(tool_name, arguments)
                except (ValueError, json.JSONDecodeError) as exc:
                    self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                    return
                except TimeoutError as exc:
                    self._send_error_json(HTTPStatus.GATEWAY_TIMEOUT, str(exc))
                    return
                except Exception:
                    LOGGER.exception("MCP Inspector invocation failed.")
                    self._send_error_json(HTTPStatus.BAD_GATEWAY, "The MCP server call failed; inspect the server stderr for details.")
                    return
                self._send_json(HTTPStatus.OK, result)
                return
            if path != "/api/runs":
                self._send_error_json(HTTPStatus.NOT_FOUND, "Route not found.")
                return
            try:
                content_length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self._send_error_json(HTTPStatus.LENGTH_REQUIRED, "A valid Content-Length header is required.")
                return
            if content_length <= 0:
                self._send_error_json(HTTPStatus.BAD_REQUEST, "The upload request is empty.")
                return
            if content_length > MAX_REQUEST_BYTES:
                self._send_error_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "The upload exceeds the 100 MiB limit.")
                return
            body = self.rfile.read(content_length)
            try:
                fields, files = parse_multipart(self.headers.get("Content-Type", ""), body)
                task = fields.get("task", "").strip()
                if not task:
                    raise ValueError("Enter a task before starting a run.")
                if len(task) > MAX_TASK_CHARS:
                    raise ValueError(f"Task text must be at most {MAX_TASK_CHARS} characters.")
                max_steps = int(fields.get("max_steps", "30"))
                if not 1 <= max_steps <= 100:
                    raise ValueError("Maximum steps must be between 1 and 100.")
                max_cost = Decimal(fields.get("max_cost_inr", "20"))
                if not max_cost.is_finite() or max_cost <= 0:
                    raise ValueError("Maximum cost must be a finite positive INR amount.")
            except (ValueError, InvalidOperation) as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                return

            try:
                run = self.manager.start_run(task, files, max_steps, float(max_cost))
            except RunConflict as exc:
                self._send_error_json(HTTPStatus.CONFLICT, str(exc))
                return
            except ValueError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json(
                HTTPStatus.CREATED,
                {"run_id": run.run_id, "status": run.status, "error": run.error},
            )

        def log_message(self, format_string: str, *args: Any) -> None:
            LOGGER.info("web-ui %s - %s", self.address_string(), format_string % args)

    return LocalWebServer((host, port), Handler)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Bodhan task agent from a local live-trace web UI.")
    parser.add_argument("--host", default="127.0.0.1", help="Loopback host only (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8765, help="Local web UI port (default: 8765).")
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=Path("out/.bodhan-dashboard"),
        help="Directory for uploaded inputs and run outputs.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.host not in ALLOWED_HOSTS:
        parser.error("--host must be localhost or 127.0.0.1; the UI is local-only.")
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535.")
    try:
        manager = RunManager(args.runs_dir)
        server = create_server(manager, args.host, args.port)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    address, port = server.server_address[:2]
    print(f"Bodhan task UI: http://{address}:{port}")
    print(f"Run files: {manager.runs_dir}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping the local web UI.")
    finally:
        manager.wait_for_runs()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
