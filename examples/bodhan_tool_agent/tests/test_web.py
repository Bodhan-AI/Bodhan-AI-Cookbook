from __future__ import annotations

import hashlib
import json
import re
import threading
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

from bodhan_agent import cli
from bodhan_agent.web import RunManager, WebRun, create_server


class FakeRunner:
    def __init__(
        self,
        task: str,
        input_path: Path,
        output_path: Path,
        max_steps: int,
        max_cost_inr: float,
        settings: object,
    ) -> None:
        self.task = task
        self.input_path = input_path
        self.output_path = output_path
        self.max_steps = max_steps
        self.max_cost_inr = max_cost_inr
        self.settings = settings
        self.service = SimpleNamespace(close=lambda: None)

    def run(self) -> dict[str, object]:
        assert self.task == "Translate this document"
        assert self.max_steps == 7
        assert self.max_cost_inr == 3.5
        assert (self.input_path / "clip.txt").read_text(encoding="utf-8") == "source text"
        artifact_dir = self.output_path / "artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact = artifact_dir / "translation.md"
        artifact.write_text("translated text", encoding="utf-8")
        events = [
            {"event": "plan_requested", "step": 0},
            {"event": "plan_decision", "step": 0, "selected_tools": ["translate_text"]},
            {
                "event": "tool_call",
                "step": 0,
                "tool": "translate_text",
                "arguments": {
                    "target_language": "mr",
                    "text_file_path": {"path_sha256": "input-hash", "extension": ".txt"},
                },
            },
            {
                "event": "tool_result",
                "step": 1,
                "tool": "translate_text",
                "ok": True,
                "estimated_cost_inr": 0.25,
                "latency_ms": 250,
                "summary": {
                    "artifact_path": "artifacts\\translation.md",
                    "verification": {
                        "name": "translation_coverage",
                        "status": "passed",
                        "checks": {"all_chunks": True},
                    },
                },
            },
            {"event": "llm_retry", "reason": "malformed_tool_call", "attempt": 1},
            {"event": "run_finished", "status": "completed"},
        ]
        (self.output_path / "trace.jsonl").write_text(
            "".join(json.dumps(event) + "\n" for event in events),
            encoding="utf-8",
        )
        report = {
            "status": "completed",
            "summary": "Translation complete.",
            "artifacts": ["artifacts\\translation.md"],
            "steps": 1,
            "estimated_cost_inr": 0.25,
            "cost_budget_inr": 3.5,
            "duration_seconds": 1.25,
            "retry_count": 2,
            "verification": [
                {
                    "tool": "translate_text",
                    "name": "translation_coverage",
                    "status": "passed",
                    "checks": {"all_chunks": True},
                }
            ],
            "failed_operations": [],
            "warnings": [],
        }
        (self.output_path / "report.json").write_text(json.dumps(report), encoding="utf-8")
        (self.output_path / "result.md").write_text("Translation complete.", encoding="utf-8")
        return report


def _multipart_request(
    boundary: str,
    task: str,
    filename: str,
    file_content: bytes,
) -> tuple[bytes, str]:
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="task"\r\n\r\n'
        f"{task}\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="max_steps"\r\n\r\n'
        "7\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="max_cost_inr"\r\n\r\n'
        "3.5\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="files"; filename="{filename}"\r\n'
        "Content-Type: text/plain\r\n\r\n"
    ).encode("utf-8") + file_content + f"\r\n--{boundary}--\r\n".encode("ascii")
    return body, f"multipart/form-data; boundary={boundary}"


def test_local_web_ui_uploads_runs_and_streams_trace(tmp_path: Path) -> None:
    manager = RunManager(tmp_path / "web-runs", runner_factory=FakeRunner)
    server = create_server(manager, port=0)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    boundary = "bodhan-test-boundary"
    body, content_type = _multipart_request(boundary, "Translate this document", "..\\clip.txt", b"source text")

    try:
        with urlopen(f"{base_url}/") as response:
            page = response.read().decode("utf-8")
            assert "Current plan" in page
            assert "Tool calls and trace" in page
            assert "Estimated Bodhan cost" in page
            assert "Execution time" in page
            assert "Verification" in page
            assert "Errors and recovery" in page
            assert "MCP Inspector" in page
            assert "Overview" in page
            assert "Invoke real MCP tool" in page
            assert "type=\"file\"" in page

        blocked_request = Request(
            f"{base_url}/api/runs",
            data=body,
            headers={"Content-Type": content_type, "Origin": "http://evil.example"},
            method="POST",
        )
        try:
            urlopen(blocked_request)
        except urllib.error.HTTPError as error:
            assert error.code == 403
        else:
            raise AssertionError("A cross-origin run request should be rejected.")

        request = Request(
            f"{base_url}/api/runs",
            data=body,
            headers={"Content-Type": content_type, "Origin": base_url},
            method="POST",
        )
        with urlopen(request) as response:
            created = json.loads(response.read())
            assert response.status == 201
        run = manager.get_run(created["run_id"])
        assert run is not None and run.thread is not None
        run.thread.join(timeout=3)
        assert not run.thread.is_alive()

        with urlopen(f"{base_url}/api/runs/{run.run_id}?cursor=0") as response:
            snapshot = json.loads(response.read())
        assert snapshot["status"] == "completed"
        assert [event["event"] for event in snapshot["events"]] == [
            "plan_requested",
            "plan_decision",
            "tool_call",
            "tool_result",
            "llm_retry",
            "run_finished",
        ]
        assert snapshot["task"] == "Translate this document"
        assert snapshot["artifacts"] == ["artifacts/translation.md"]
        assert snapshot["available_files"] == ["report.json", "result.md", "trace.jsonl"]
        assert snapshot["metrics"] == {
            "steps": 1,
            "max_steps": 7,
            "estimated_cost_inr": 0.25,
            "cost_budget_inr": 3.5,
            "duration_seconds": 1.25,
            "retry_count": 2,
        }
        assert snapshot["verification"][0]["status"] == "passed"
        assert snapshot["failed_operations"] == []
        assert snapshot["assumptions"] == []
        assert snapshot["input_files"] == [{
            "path": "clip.txt",
            "name": "clip.txt",
            "extension": ".txt",
            "size_bytes": 11,
            "available": True,
        }]
        with urlopen(f"{base_url}/api/runs") as response:
            history = json.loads(response.read())
        assert [item["run_id"] for item in history["runs"]] == [run.run_id]

        for path in ("artifacts/translation.md", "artifacts\\translation.md"):
            query = urlencode({"path": path})
            download_url = f"{base_url}/api/runs/{run.run_id}/download?{query}"
            with urlopen(download_url) as response:
                assert response.read() == b"translated text"
            preview_url = f"{base_url}/api/runs/{run.run_id}/preview?{query}"
            with urlopen(preview_url) as response:
                preview = json.loads(response.read())
                assert preview["content"] == "translated text"
                assert preview["path"] == path

        traversal_url = f"{base_url}/api/runs/{run.run_id}/download?{urlencode({'path': '../.env'})}"
        try:
            urlopen(traversal_url)
        except urllib.error.HTTPError as error:
            assert error.code == 400
        else:
            raise AssertionError("A path traversal download should be rejected.")

        for path in (r"artifacts\..\..\.env", r"C:\outside\secret.txt"):
            invalid_url = f"{base_url}/api/runs/{run.run_id}/download?{urlencode({'path': path})}"
            try:
                urlopen(invalid_url)
            except urllib.error.HTTPError as error:
                assert error.code == 400
            else:
                raise AssertionError("An unsafe Windows-style path should be rejected.")
    finally:
        server.shutdown()
        server_thread.join(timeout=3)
        server.server_close()


def test_local_web_server_refuses_non_loopback_bind(tmp_path: Path) -> None:
    manager = RunManager(tmp_path / "web-runs", runner_factory=FakeRunner)

    try:
        create_server(manager, host="0.0.0.0", port=0)
    except ValueError as error:
        assert "localhost" in str(error)
    else:
        raise AssertionError("The web UI must not bind to a non-loopback interface.")


def test_live_snapshot_exposes_trace_and_elapsed_metrics(tmp_path: Path) -> None:
    manager = RunManager(tmp_path / "web-runs", runner_factory=FakeRunner)
    run = WebRun(
        "a" * 32,
        "Transcribe this Hindi audio",
        tmp_path / "inputs",
        tmp_path / "run",
        max_steps=9,
        max_cost_inr=4.0,
    )
    run.output_dir.mkdir()
    events = [
        {"event": "plan_decision", "step": 0, "selected_tools": ["transcribe_audio"]},
        {
            "event": "tool_call",
            "step": 0,
            "tool": "transcribe_audio",
            "arguments": {"file_path": {"path_sha256": "audio-hash", "extension": ".wav"}},
        },
        {
            "event": "tool_result",
            "step": 1,
            "tool": "transcribe_audio",
            "ok": True,
            "estimated_cost_inr": 0.4,
            "summary": {
                "retries": 1,
                "verification": {"name": "transcription_saved", "status": "passed"},
            },
        },
        {"event": "llm_retry", "reason": "malformed_tool_call"},
    ]
    (run.output_dir / "trace.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )

    snapshot = manager.snapshot(run, 0)

    assert snapshot["task"] == "Transcribe this Hindi audio"
    assert [event["event"] for event in snapshot["events"]] == [
        "plan_decision",
        "tool_call",
        "tool_result",
        "llm_retry",
    ]
    assert snapshot["metrics"]["max_steps"] == 9
    assert snapshot["metrics"]["cost_budget_inr"] == 4.0
    assert snapshot["metrics"]["duration_seconds"] >= 0
    assert snapshot["metrics"]["estimated_cost_inr"] is None
    assert snapshot["report_available"] is False


def test_snapshot_discovers_existing_ocr_artifact_and_normalizes_paths(tmp_path: Path) -> None:
    manager = RunManager(tmp_path / "web-runs", runner_factory=FakeRunner)
    run = WebRun(
        "c" * 32,
        "Extract text from this scanned PDF",
        tmp_path / "inputs",
        tmp_path / "run",
        max_steps=9,
        max_cost_inr=4.0,
    )
    run.status = "completed"
    artifact = run.output_dir / "artifacts" / "ocr-fa61d47d.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("Recognized page text", encoding="utf-8")
    (run.output_dir / "report.json").write_text(json.dumps({
        "status": "completed",
        "artifacts": [
            r"artifacts\ocr-fa61d47d.md",
            "artifacts/does-not-exist.md",
            "../outside.md",
        ],
        "verification": [{
            "tool": "ocr_document",
            "name": "ocr_page_coverage",
            "status": "passed",
        }],
    }), encoding="utf-8")

    snapshot = manager.snapshot(run, 0)

    assert snapshot["status"] == "completed"
    assert snapshot["artifacts"] == ["artifacts/ocr-fa61d47d.md"]
    assert snapshot["available_files"] == ["report.json"]
    assert snapshot["verification"][0]["name"] == "ocr_page_coverage"
    assert "llm_request_count" not in snapshot["metrics"]
    assert manager.download_path(run, r"artifacts\ocr-fa61d47d.md").read_text(
        encoding="utf-8"
    ) == "Recognized page text"


def test_finalized_ocr_artifact_can_be_previewed_and_downloaded(tmp_path: Path) -> None:
    manager = RunManager(tmp_path / "web-runs", runner_factory=FakeRunner)
    run = WebRun(
        "d" * 32,
        "Extract text from this scanned PDF",
        tmp_path / "inputs",
        tmp_path / "web-runs" / ("d" * 32) / "run",
        max_steps=9,
        max_cost_inr=4.0,
    )
    run.status = "completed"
    run.output_dir.mkdir(parents=True)
    artifact = run.output_dir / "artifacts" / "ocr-fa61d47d.md"
    artifact.parent.mkdir()
    artifact.write_text("# Extracted text\n", encoding="utf-8")
    expected_artifact = artifact.read_bytes()
    (run.output_dir / "report.json").write_text(json.dumps({
        "status": "completed",
        "artifacts": [r"artifacts\ocr-fa61d47d.md"],
        "verification": [{
            "tool": "ocr_document",
            "name": "ocr_page_coverage",
            "status": "passed",
        }],
    }), encoding="utf-8")
    manager._runs[run.run_id] = run
    server = create_server(manager, port=0)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"

    try:
        for path in ("artifacts/ocr-fa61d47d.md", r"artifacts\ocr-fa61d47d.md"):
            query = urlencode({"path": path})
            with urlopen(f"{base_url}/api/runs/{run.run_id}/preview?{query}") as response:
                preview = json.loads(response.read())
            with urlopen(f"{base_url}/api/runs/{run.run_id}/download?{query}") as response:
                download = response.read()
            assert preview["content"] == "# Extracted text\n"
            assert download == expected_artifact
    finally:
        server.shutdown()
        server_thread.join(timeout=3)
        server.server_close()


def test_dashboard_accounting_renderer_is_top_level_and_handles_optional_data() -> None:
    html_path = Path(__file__).parents[1] / "src" / "bodhan_agent" / "web_ui.html"
    html = html_path.read_text(encoding="utf-8")
    renderer = re.search(
        r"^  function renderProviderAccounting\(metrics = \{\}, events = \[\]\)",
        html,
        re.MULTILINE,
    )
    breakdown = re.search(r"^  function renderBreakdown\(", html, re.MULTILINE)

    assert renderer is not None
    assert html.count("function renderProviderAccounting(") == 1
    assert breakdown is not None and renderer.start() < breakdown.start()
    assert "metrics = metrics && typeof metrics === \"object\" ? metrics : {};" in html
    assert "events = Array.isArray(events) ? events : [];" in html
    assert "renderProviderAccounting(data.metrics || {}, liveEvents);" in html
    assert 'id="run-history"' in html
    assert "function refreshHistory()" in html
    assert "data.final_artifacts" in html
    assert "data.intermediate_artifacts" in html
    assert "input-file-preview" in html
    assert "const runFiles = [...new Set" in html
    assert "page coverage only; not character-accuracy verification" in html
    assert 'id="clear-history"' in html
    assert 'fetch("/api/runs/clear-history", { method: "POST" })' in html


def test_mcp_inspector_discovers_and_invokes_the_standalone_server(tmp_path: Path) -> None:
    manager = RunManager(tmp_path / "web-runs")
    server = create_server(manager, port=0)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"

    try:
        with urlopen(f"{base_url}/api/mcp/tools") as response:
            discovery = json.loads(response.read())
        tool_names = {tool["name"] for tool in discovery["tools"]}
        assert {
            "transcribe_audio",
            "translate_text",
            "generate_speech",
            "ocr_document",
        } <= tool_names

        payload = json.dumps({
            "tool": "ocr_document",
            "arguments": {
                "file_path": str(manager.mcp_output_dir / "missing-input.pdf"),
            },
        }).encode("utf-8")
        request = Request(
            f"{base_url}/api/mcp/call",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Origin": base_url,
            },
            method="POST",
        )
        with urlopen(request) as response:
            result = json.loads(response.read())
        structured = result["structured_content"]
        assert structured["ok"] is False
        assert structured["error"]["code"]
        assert result["budget"]["spent_inr"] == 0
    finally:
        server.shutdown()
        server_thread.join(timeout=3)
        server.server_close()


def test_json_response_ignores_browser_disconnect(tmp_path: Path) -> None:
    server = create_server(RunManager(tmp_path / "web-runs"), port=0)
    handler = object.__new__(server.RequestHandlerClass)
    handler.send_response = lambda status: None
    handler.send_header = lambda name, value: None
    handler.end_headers = lambda: None

    class DisconnectedClient:
        def write(self, content: bytes) -> None:
            raise ConnectionAbortedError(10053, "client disconnected")

    handler.wfile = DisconnectedClient()
    try:
        handler._send_json(200, {"tools": []})
    finally:
        server.server_close()


def test_stop_request_is_recorded_even_before_runner_is_ready(tmp_path: Path) -> None:
    manager = RunManager(tmp_path / "web-runs", runner_factory=FakeRunner)
    run = WebRun(
        "b" * 32,
        "Transcribe this Hindi audio",
        tmp_path / "inputs",
        tmp_path / "run",
        max_steps=9,
        max_cost_inr=4.0,
    )

    assert manager.request_stop(run) is True
    assert run.stop_requested is True


def test_cli_dashboard_flag_opens_the_same_task_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import bodhan_agent.web as web_module
    from bodhan_mcp.config import Settings

    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "clip.txt").write_text("source text", encoding="utf-8")
    output = tmp_path / "cli-output"
    browser_urls: list[str] = []

    class StubServer:
        server_address = ("127.0.0.1", 8765)

        def serve_forever(self) -> None:
            return None

        def server_close(self) -> None:
            return None

    def fake_manager(runs_dir: Path) -> RunManager:
        return RunManager(runs_dir, runner_factory=FakeRunner)

    def fake_settings(output_dir: Path | None = None) -> Settings:
        return Settings(llm_api_key="test-key", output_dir=output_dir or Path("out"))

    monkeypatch.setattr(Settings, "from_env", staticmethod(fake_settings))
    monkeypatch.setattr(web_module, "RunManager", fake_manager)
    monkeypatch.setattr(web_module, "create_server", lambda *_args, **_kwargs: StubServer())
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: browser_urls.append(url) or True)

    status = cli.main([
        "--task", "Translate this document",
        "--files", str(inputs),
        "--out", str(output),
        "--max-steps", "7",
        "--max-cost-inr", "3.5",
        "--dashboard",
    ])

    assert status == 0
    assert len(browser_urls) == 1
    assert browser_urls[0].startswith("http://127.0.0.1:8765/?run=")
    assert (output / "report.json").is_file()


def test_persisted_history_archives_inputs_and_supports_restart(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "clip.txt").write_text("source text", encoding="utf-8")
    output = tmp_path / "run"
    catalog = tmp_path / "web-runs"
    manager = RunManager(catalog, runner_factory=FakeRunner)

    run = manager.start_path_run("Translate this document", inputs, output, 7, 3.5)
    assert run.thread is not None
    run.thread.join(timeout=3)
    assert not run.thread.is_alive()

    restarted_manager = RunManager(catalog, runner_factory=FakeRunner)
    history = restarted_manager.list_runs()
    assert len(history) == 1
    assert history[0]["run_id"] == run.run_id
    assert history[0]["status"] == "completed"
    saved_run = restarted_manager.get_run(run.run_id)
    assert saved_run is not None
    archived = restarted_manager.download_input_path(saved_run, "clip.txt")
    assert archived.read_text(encoding="utf-8") == "source text"


def test_clear_history_api_removes_records_but_keeps_task_outputs(tmp_path: Path) -> None:
    catalog = tmp_path / "web-runs"
    manager = RunManager(catalog, runner_factory=FakeRunner)
    run_id = "5" * 32
    run_root = catalog / run_id
    inputs = run_root / "inputs"
    output = run_root / "run"
    inputs.mkdir(parents=True)
    output.mkdir()
    (inputs / "clip.txt").write_text("archived input", encoding="utf-8")
    (output / "result.md").write_text("task output", encoding="utf-8")
    run = WebRun(
        run_id,
        "Translate this document",
        inputs,
        output,
        max_steps=7,
        max_cost_inr=3.5,
        status="completed",
        finished_at="2026-11-18T12:00:00+00:00",
        input_snapshot_dir=inputs,
    )
    manager._runs[run_id] = run
    manager._persist_run(run)
    server = create_server(manager, port=0)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"

    try:
        request = Request(
            f"{base_url}/api/runs/clear-history",
            data=b"",
            headers={"Origin": base_url},
            method="POST",
        )
        with urlopen(request) as response:
            result = json.loads(response.read())
        assert result == {
            "cleared_count": 1,
            "active_count": 0,
            "cleanup_warning_count": 0,
        }
        with urlopen(f"{base_url}/api/runs") as response:
            assert json.loads(response.read())["runs"] == []

        assert not (run_root / "run.json").exists()
        assert not inputs.exists()
        assert (output / "result.md").read_text(encoding="utf-8") == "task output"
        restarted_manager = RunManager(catalog, runner_factory=FakeRunner)
        assert restarted_manager.list_runs() == []
    finally:
        server.shutdown()
        server_thread.join(timeout=3)
        server.server_close()


def test_clear_history_keeps_active_runs(tmp_path: Path) -> None:
    catalog = tmp_path / "web-runs"
    manager = RunManager(catalog, runner_factory=FakeRunner)
    active = WebRun(
        "6" * 32,
        "Still running",
        tmp_path / "inputs",
        tmp_path / "active-output",
        max_steps=7,
        max_cost_inr=3.5,
        status="running",
    )
    manager._runs[active.run_id] = active
    manager._persist_run(active)

    result = manager.clear_history()

    assert result == {
        "cleared_count": 0,
        "active_count": 1,
        "cleanup_warning_count": 0,
    }
    assert manager.get_run(active.run_id) is not None
    assert manager._record_path(active.run_id).is_file()


def test_archived_inputs_never_include_secret_configuration_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "clip.txt").write_text("source text", encoding="utf-8")
    (source / ".env").write_text("LLM_API_KEY=not-for-dashboard", encoding="utf-8")
    manager = RunManager(tmp_path / "catalog", runner_factory=FakeRunner)
    archived = tmp_path / "archive"

    manifest = manager._snapshot_input_files(source, archived)

    assert [item["name"] for item in manifest] == ["clip.txt"]
    assert not (archived / ".env").exists()
    with pytest.raises(ValueError, match="Secret/configuration"):
        manager.start_run(
            "Translate this document",
            [(".env", b"secret")],
            7,
            3.5,
        )


def test_default_catalog_discovers_legacy_runs_only_in_its_output_root(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "out"
    legacy_output = output_root / "legacy"
    legacy_output.mkdir(parents=True)
    run_id = "2" * 32
    (legacy_output / "trace.jsonl").write_text("", encoding="utf-8")
    (legacy_output / "report.json").write_text(json.dumps({
        "run_id": run_id,
        "task": "Legacy task",
        "status": "completed",
        "artifacts": [],
    }), encoding="utf-8")

    custom_manager = RunManager(tmp_path / "custom-catalog", runner_factory=FakeRunner)
    assert custom_manager.list_runs() == []

    default_manager = RunManager(
        output_root / ".bodhan-dashboard",
        runner_factory=FakeRunner,
    )
    assert [run["run_id"] for run in default_manager.list_runs()] == [run_id]
    assert default_manager.clear_history()["cleared_count"] == 1
    assert default_manager.list_runs() == []
    assert (legacy_output / "report.json").is_file()
    restarted_manager = RunManager(
        output_root / ".bodhan-dashboard",
        runner_factory=FakeRunner,
    )
    assert restarted_manager.list_runs() == []


def test_default_dashboard_catalog_loads_previous_cli_catalog(tmp_path: Path) -> None:
    legacy_catalog = tmp_path / ".bodhan-dashboard"
    legacy_manager = RunManager(legacy_catalog, runner_factory=FakeRunner)
    run = WebRun(
        "4" * 32,
        "Translate this document",
        tmp_path / "inputs",
        tmp_path / "out-t" / "first-run",
        max_steps=30,
        max_cost_inr=20,
        status="partial",
        finished_at="2026-11-18T12:00:00+00:00",
    )
    legacy_manager._runs[run.run_id] = run
    legacy_manager._persist_run(run)

    dashboard_manager = RunManager(
        tmp_path / "out" / ".bodhan-dashboard",
        runner_factory=FakeRunner,
    )

    history = dashboard_manager.list_runs()
    assert len(history) == 1
    assert history[0]["run_id"] == run.run_id
    assert history[0]["status"] == "partial"


def test_cli_default_catalog_matches_dashboard_default() -> None:
    assert cli._default_runs_dir() == Path("out/.bodhan-dashboard").resolve()


def test_sequential_runs_preserve_previous_output_and_history(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "clip.txt").write_text("source text", encoding="utf-8")
    output = tmp_path / "output"
    manager = RunManager(tmp_path / "web-runs", runner_factory=FakeRunner)

    first = manager.start_path_run("Translate this document", inputs, output, 7, 3.5)
    assert first.thread is not None
    first.thread.join(timeout=3)
    original_result = (first.output_dir / "result.md").read_text(encoding="utf-8")

    second = manager.start_path_run("Translate this document", inputs, output, 7, 3.5)
    assert second.output_dir != first.output_dir
    assert second.thread is not None
    second.thread.join(timeout=3)

    assert (first.output_dir / "result.md").read_text(encoding="utf-8") == original_result
    assert len(manager.list_runs()) == 2


def test_archived_input_preview_and_download_endpoints(tmp_path: Path) -> None:
    manager = RunManager(tmp_path / "web-runs", runner_factory=FakeRunner)
    input_dir = manager.runs_dir / ("e" * 32) / "inputs"
    input_dir.mkdir(parents=True)
    source = input_dir / "clip.txt"
    source.write_text("original source", encoding="utf-8")
    image = input_dir / "scan.png"
    image.write_bytes(b"local image bytes")
    run = WebRun(
        "e" * 32,
        "Translate this document",
        input_dir,
        tmp_path / "output",
        max_steps=7,
        max_cost_inr=3.5,
        input_snapshot_dir=input_dir,
        input_files=[{
            "path": "clip.txt",
            "name": "clip.txt",
            "extension": ".txt",
            "size_bytes": source.stat().st_size,
            "available": True,
        }],
    )
    manager._runs[run.run_id] = run
    server = create_server(manager, port=0)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"

    try:
        query = urlencode({"path": r"clip.txt"})
        with urlopen(f"{base_url}/api/runs/{run.run_id}/input-preview?{query}") as response:
            assert json.loads(response.read())["content"] == "original source"
        with urlopen(f"{base_url}/api/runs/{run.run_id}/input-download?{query}") as response:
            assert response.read() == b"original source"
        image_query = urlencode({"path": "scan.png"})
        with urlopen(f"{base_url}/api/runs/{run.run_id}/input-file-preview?{image_query}") as response:
            assert response.headers["Content-Type"] == "image/png"
            assert response.headers["Content-Disposition"].startswith("inline;")
            assert response.read() == b"local image bytes"
        unsafe = urlencode({"path": r"..\outside.txt"})
        with pytest.raises(urllib.error.HTTPError) as error:
            urlopen(f"{base_url}/api/runs/{run.run_id}/input-download?{unsafe}")
        assert error.value.code == 400
    finally:
        server.shutdown()
        server_thread.join(timeout=3)
        server.server_close()


def test_dashboard_close_does_not_request_task_stop(tmp_path: Path) -> None:
    manager = RunManager(tmp_path / "web-runs", runner_factory=FakeRunner)
    run = WebRun(
        "f" * 32,
        "Translate this document",
        tmp_path / "inputs",
        tmp_path / "output",
        max_steps=7,
        max_cost_inr=3.5,
    )
    run.status = "running"
    manager._runs[run.run_id] = run
    server = create_server(manager, port=0)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    request = Request(
        f"{base_url}/api/dashboard/close",
        data=b"",
        headers={"Origin": base_url},
        method="POST",
    )

    try:
        with urlopen(request) as response:
            assert json.loads(response.read())["status"] == "closing"
        server_thread.join(timeout=3)
        assert not server_thread.is_alive()
        assert run.status == "running"
        assert run.stop_requested is False
    finally:
        server.server_close()


def test_snapshot_classifies_artifacts_consumed_by_later_tools_as_intermediate(
    tmp_path: Path,
) -> None:
    manager = RunManager(tmp_path / "web-runs", runner_factory=FakeRunner)
    run = WebRun(
        "1" * 32,
        "Translate this document",
        tmp_path / "inputs",
        tmp_path / "run",
        max_steps=7,
        max_cost_inr=3.5,
    )
    run.output_dir.mkdir()
    artifact = run.output_dir / "artifacts" / "ocr.md"
    artifact.parent.mkdir()
    artifact.write_text("recognized text", encoding="utf-8")
    path_hash = hashlib.sha256(
        artifact.resolve().as_posix().encode()
    ).hexdigest()
    (run.output_dir / "trace.jsonl").write_text(
        json.dumps({
            "event": "tool_call",
            "tool": "translate_text",
            "arguments": {"text_file_path": {"path_sha256": path_hash}},
        }) + "\n",
        encoding="utf-8",
    )
    (run.output_dir / "report.json").write_text(json.dumps({
        "artifacts": ["artifacts/ocr.md"],
    }), encoding="utf-8")

    snapshot = manager.snapshot(run, 0)

    assert snapshot["final_artifacts"] == []
    assert snapshot["intermediate_artifacts"] == ["artifacts/ocr.md"]


def test_run_subcommand_records_two_cli_runs_without_overwriting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import bodhan_agent.web as web_module
    from bodhan_mcp.config import Settings

    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "clip.txt").write_text("source text", encoding="utf-8")
    output = tmp_path / "output"
    catalog = tmp_path / "catalog"

    class TestRunManager(RunManager):
        def __init__(self, runs_dir: Path) -> None:
            super().__init__(runs_dir, runner_factory=FakeRunner)

    def fake_settings(output_dir: Path | None = None) -> Settings:
        return Settings(llm_api_key="test-key", output_dir=output_dir or Path("out"))

    monkeypatch.setattr(Settings, "from_env", staticmethod(fake_settings))
    monkeypatch.setattr(web_module, "RunManager", TestRunManager)
    command = [
        "run",
        "--task", "Translate this document",
        "--files", str(inputs),
        "--out", str(output),
        "--runs-dir", str(catalog),
        "--max-steps", "7",
        "--max-cost-inr", "3.5",
    ]

    assert cli.main(command) == 0
    first_result = (output / "result.md").read_text(encoding="utf-8")
    assert cli.main(command[1:]) == 0

    assert (output / "result.md").read_text(encoding="utf-8") == first_result
    history = TestRunManager(catalog).list_runs()
    assert len(history) == 2
    assert all(item["status"] == "completed" for item in history)
