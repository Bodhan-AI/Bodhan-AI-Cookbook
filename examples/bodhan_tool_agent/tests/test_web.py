"""The local web UI: page, samples, replay of a saved run, artifact serving, path safety."""

from __future__ import annotations

import json
from pathlib import Path

from starlette.testclient import TestClient

from bodhan_agent.web import build_app


def make_root(tmp_path: Path) -> Path:
    (tmp_path / "sample_data").mkdir()
    (tmp_path / "sample_data" / "note.txt").write_text("hello", encoding="utf-8")
    run = tmp_path / "demo_runs" / "example"
    (run / "artifacts").mkdir(parents=True)
    events = [
        {"step": 1, "type": "plan", "tool": "submit_plan", "arguments": {"goal": "Say hello", "steps": ["speak"]}},
        {"step": 2, "type": "tool", "tool": "synthesize_speech", "ok": True, "cost_inr": 0.01,
         "cumulative_cost_inr": 0.01, "arguments": {"text_path": "note.txt"}, "result_summary": {}},
    ]
    (run / "trace.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    (run / "report.json").write_text(json.dumps({"task": "Say hello", "status": "completed",
                                                 "answer": "Done.", "total_cost_inr": 0.01}), encoding="utf-8")
    (run / "artifacts" / "hello.txt").write_text("namaste", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("do not serve", encoding="utf-8")
    return tmp_path


def test_page_and_samples(tmp_path: Path) -> None:
    client = TestClient(build_app(make_root(tmp_path)))
    assert "Bodhan Task Agent" in client.get("/").text
    data = client.get("/api/samples").json()
    assert "sample_data/note.txt" in data["files"]
    assert data["past_runs"][0]["id"] == "demo_runs/example"
    assert data["past_runs"][0]["task"] == "Say hello"


def test_replay_streams_trace_then_final_result(tmp_path: Path) -> None:
    client = TestClient(build_app(make_root(tmp_path)))
    body = client.get("/api/events", params={"id": "demo_runs/example"}).text
    assert body.count("event: trace") == 2
    done = body.split("event: done\ndata: ", 1)[1].strip()
    final = json.loads(done)
    assert final["status"] == "completed"
    assert final["artifacts"][0]["name"] == "artifacts/hello.txt"


def test_artifacts_are_served_only_from_the_run_folder(tmp_path: Path) -> None:
    client = TestClient(build_app(make_root(tmp_path)))
    ok = client.get("/api/artifact", params={"id": "demo_runs/example", "name": "artifacts/hello.txt"})
    assert ok.status_code == 200 and ok.text == "namaste"
    escape = client.get("/api/artifact", params={"id": "demo_runs/example", "name": "../../secret.txt"})
    assert escape.status_code == 404
    assert client.get("/api/events", params={"id": "../secret"}).status_code == 404


def test_start_requires_a_task_and_uploads_get_safe_names(tmp_path: Path) -> None:
    client = TestClient(build_app(make_root(tmp_path)))
    assert client.post("/api/runs", json={"task": " "}).status_code == 400
    up = client.post("/api/upload", params={"name": "../../evil name.wav"}, content=b"RIFF").json()
    assert up["path"].startswith("out/web/uploads/") and ".." not in up["path"]
    assert (tmp_path / up["path"]).read_bytes() == b"RIFF"
