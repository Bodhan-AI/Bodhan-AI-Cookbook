"""API tests with FastAPI's TestClient and a fake Bodhan client."""

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import FakeBodhan, encode, needs_ffmpeg

pytestmark = needs_ffmpeg


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings, client=FakeBodhan())) as c:
        yield c


def upload(client, path: Path, name: str | None = None, **form):
    with path.open("rb") as fh:
        return client.post("/api/calls", files={"file": (name or path.name, fh, "audio/mpeg")}, data=form)


def wait_until_done(client, call_id: str, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/calls/{call_id}").json()
        if body["status"] in ("completed", "partial", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError("call did not finish")


def test_health_languages_and_config(client):
    assert client.get("/health").json() == {"status": "ok"}
    langs = {lang["code"]: lang for lang in client.get("/api/languages").json()["languages"]}
    assert len(langs) == 25
    assert langs["hi"]["translation"] is True and langs["bho"]["translation"] is False
    config = client.get("/api/config").json()
    assert config["max_file_mb"] == 5 and ".m4a" in config["allowed_extensions"]
    assert config["diarization_enabled"] is False


def test_upload_progress_and_export(client, call_wav, tmp_path):
    mp3 = encode(call_wav, tmp_path / "call_2026.mp3")
    resp = upload(client, mp3, language="hi", ticket_id="TKT-1", agent_name="Agent-07")
    assert resp.status_code == 202, resp.text
    call_id = resp.json()["call_id"]
    assert len(call_id) == 16

    body = wait_until_done(client, call_id)
    assert body["status"] == "completed", body
    record = body["record"]
    assert record["source_file"] == "call_2026.mp3"
    assert record["ticket_id"] == "TKT-1" and record["agent_name"] == "Agent-07"
    assert len(record["segments"]) == 4
    assert body["progress"]["stage"] == "done"

    exported = client.get(f"/api/calls/{call_id}/export", params={"format": "json"})
    assert exported.status_code == 200
    assert exported.json()["call_id"] == call_id
    assert f'filename="{call_id}.json"' in exported.headers["content-disposition"]

    csv = client.get(f"/api/calls/{call_id}/export", params={"format": "segments_csv"})
    assert csv.content.startswith(b"\xef\xbb\xbf") and "नमस्ते".encode() in csv.content
    assert client.get(f"/api/calls/{call_id}/export", params={"format": "calls_csv"}).status_code == 200
    assert client.get(f"/api/calls/{call_id}/export", params={"format": "xml"}).status_code == 422


def test_uploaded_audio_is_deleted_after_processing(client, call_wav):
    call_id = upload(client, call_wav).json()["call_id"]
    wait_until_done(client, call_id)
    job = client.app.state.store.get(call_id)
    assert not job.workdir.exists()


def test_wrong_file_type(client, tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("not audio")
    resp = upload(client, f)
    assert resp.status_code == 415
    assert resp.json()["error"]["code"] == "unsupported_format"


def test_renamed_file_is_caught_by_content_check(client, tmp_path):
    f = tmp_path / "call.mp3"
    f.write_bytes(b"%PDF-1.7" + b"\x00" * 100)
    resp = upload(client, f)
    assert resp.status_code == 415


def test_file_too_large(client, tmp_path):
    f = tmp_path / "big.wav"
    f.write_bytes(b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 5_100_000)
    resp = upload(client, f)
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "file_too_large"


def test_declared_too_large_upload_is_refused_before_reading(client):
    resp = client.post(
        "/api/calls",
        content=b"x",
        headers={"content-length": "999999999", "content-type": "multipart/form-data; boundary=x"},
    )
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "file_too_large"


def test_silent_call_completes_with_no_segments(client, tmp_path):
    from tests.conftest import synth, write_wav

    resp = upload(client, write_wav(tmp_path / "silent.wav", synth([("silence", 5.0)])))
    body = wait_until_done(client, resp.json()["call_id"])
    assert body["status"] == "completed"
    assert body["record"]["segments"] == []
    assert "No speech" in body["record"]["warnings"][0]


def test_empty_file(client, tmp_path):
    f = tmp_path / "empty.wav"
    f.write_bytes(b"")
    assert upload(client, f).json()["error"]["code"] == "empty_file"


def test_call_too_long(settings, call_wav):
    settings.max_duration_seconds = 10
    with TestClient(create_app(settings, client=FakeBodhan())) as c:
        resp = upload(c, call_wav)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "call_too_long"


def test_unknown_language(client, call_wav):
    resp = upload(client, call_wav, language="xx")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_language"


def test_unknown_call_id(client):
    resp = client.get("/api/calls/doesnotexist")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
    assert client.get("/api/calls/doesnotexist/export").status_code == 404


def test_app_key_is_required_when_configured(settings, call_wav):
    settings.app_key = "shared-app-key"
    with TestClient(create_app(settings, client=FakeBodhan())) as c:
        assert c.get("/health").status_code == 200  # health stays open for load balancers
        assert c.get("/api/languages").status_code == 401
        assert c.get("/api/languages", headers={"X-App-Key": "wrong"}).status_code == 401
        assert c.get("/api/languages", headers={"X-App-Key": "shared-app-key"}).status_code == 200


def test_results_expire(settings, call_wav):
    settings.result_ttl_seconds = 0
    with TestClient(create_app(settings, client=FakeBodhan())) as c:
        call_id = upload(c, call_wav).json()["call_id"]
        deadline = time.monotonic() + 20
        while c.get(f"/api/calls/{call_id}").status_code == 200 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert c.get(f"/api/calls/{call_id}").status_code == 404
