"""Web UI backend: validation, upload -> dub -> edit one line -> re-run, using the offline fake API."""
import importlib
import time

import pytest
from fake_bodhan import FakeBodhan
from fastapi.testclient import TestClient

from bodhan_dub import config
from bodhan_dub.bodhan import BodhanClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BODHAN_DUB_WEB_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("BODHAN_API_KEY", "x")
    for k in ("BODHAN_TRANSCRIBE_KEY", "BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY", "LLM_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    from bodhan_dub.web import server
    server = importlib.reload(server)
    fake = FakeBodhan()
    monkeypatch.setattr(server, "BodhanClient", lambda keys, cache, max_concurrency=4: BodhanClient(
        keys, cache, max_concurrency=1, http=fake.client, base_url="https://api.bodhan.ai", backoff=0.01))
    return TestClient(server.app), server


def wait(c, pid, timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = c.get(f"/api/projects/{pid}").json()
        if j["job"]["state"] not in ("queued", "running"):
            return j
        time.sleep(0.4)
    raise TimeoutError


def test_validation(client, video):
    c, _ = client
    assert c.get("/api/config").json()["missing_keys"] == []
    assert c.get("/api/projects/..%2f..").status_code == 404
    with open(video, "rb") as f:
        assert c.post("/api/projects", files={"file": ("a.mp4", f)}, data={"langs": "xx"}).status_code == 400
    assert c.post("/api/projects", files={"file": ("a.txt", b"hi")}, data={"langs": "hi"}).status_code == 400


def test_upload_edit_rerun(client, video):
    c, _ = client
    with open(video, "rb") as f:
        r = c.post("/api/projects", files={"file": ("lesson.mp4", f)}, data={"langs": "hi,ta", "name": "Lesson"})
    pid = r.json()["id"]
    j = wait(c, pid)
    assert j["job"]["state"] == "done" and set(j["report"]["languages"]) == {"hi", "ta"}
    d = c.get(f"/api/projects/{pid}/lang/hi").json()
    assert len(d["segments"]) == 3 and d["ai_dubbed"]
    assert c.get(f"/api/projects/{pid}/file/video", params={"lang": "hi"}).status_code == 200
    assert c.get(f"/api/projects/{pid}/file/clip", params={"lang": "hi", "idx": 0}).status_code == 200
    assert c.get(f"/api/projects/{pid}/file/video", params={"lang": "zz"}).status_code == 404

    cost_before = j["report"]["languages"]["ta"]["cost"]["total_cost_inr"]
    assert c.patch(f"/api/projects/{pid}/lang/hi/segments/1",
                   json={"text": "मेरा अनुवाद", "speed": 5, "voice": "Amit", "evil": "x"}).status_code == 200
    j2 = wait(c, pid)
    s1 = c.get(f"/api/projects/{pid}/lang/hi").json()["segments"][1]
    assert s1["translation"] == "मेरा अनुवाद" and s1["voice"] == "Amit"
    assert s1["stretch"] == config.STRETCH_MAX           # speed 5 was clamped to the allowed range
    assert c.get(f"/api/projects/{pid}/lang/hi").json()["overrides"]["1"].keys() == {"text", "speed", "voice"}
    assert j2["report"]["languages"]["ta"]["cost"]["total_cost_inr"] == cost_before   # other language untouched, still reported

    assert c.put(f"/api/projects/{pid}/glossary", json={"terms": [{"term": "sentence", "translations": {"hi": "वाक्य"}}]}).status_code == 200
    assert wait(c, pid)["user_glossary"][0]["translations"] == {"hi": "वाक्य"}
    assert c.delete(f"/api/projects/{pid}").status_code == 200


def test_token_and_cors(tmp_path, monkeypatch):
    import importlib

    from fastapi.testclient import TestClient

    from bodhan_dub.web import server
    monkeypatch.setenv("DUB_ACCESS_TOKEN", "s3cret")
    monkeypatch.setenv("DUB_CORS_ORIGINS", "https://app.vercel.app")
    monkeypatch.setenv("BODHAN_DUB_WEB_DATA", str(tmp_path))
    s = importlib.reload(server)
    try:
        c = TestClient(s.app)
        assert c.get("/api/health").json()["auth"] is True
        assert c.get("/api/projects").status_code == 401
        assert c.get("/api/projects", headers={"X-Access-Token": "s3cret"}).status_code == 200
        assert c.post("/api/projects/abc/rerun").status_code == 401
        pre = c.options("/api/projects", headers={"Origin": "https://app.vercel.app", "Access-Control-Request-Method": "POST",
                                                  "Access-Control-Request-Headers": "x-access-token"})
        assert pre.status_code == 200 and pre.headers["access-control-allow-origin"] == "https://app.vercel.app"
    finally:
        monkeypatch.delenv("DUB_ACCESS_TOKEN")
        monkeypatch.delenv("DUB_CORS_ORIGINS")
        importlib.reload(server)


def test_diagnose_reports_keys_and_statuses(client, monkeypatch):
    import httpx
    client, server = client
    monkeypatch.setenv("BODHAN_TRANSLATE_KEY", "same-key-1234")
    monkeypatch.setenv("BODHAN_TRANSCRIBE_KEY", "same-key-1234")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(404, json={"detail": "Not Found"}, headers={"x-request-id": "r1"}))
    monkeypatch.setattr(server, "BodhanClient", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("speak: HTTP 404")))
    j = client.post("/api/diagnose").json()
    assert j["translate_key_same_as_transcribe"] is True and j["keys"]["translate"]["last4"] == "1234"
    assert [t["status"] for t in j["tests"][:3]] == [404, 404, 404] and j["tests"][2]["call"].startswith("transcribe")
    assert j["tests"][3]["call"].startswith("ocr") and j["tests"][3]["status"] == 404 and j["ocr_key_set"] is True
    assert "error" in j["tests"][-1]


def test_diagnose_get_needs_query_token_and_post_needs_header(tmp_path, monkeypatch):
    import importlib

    from fastapi.testclient import TestClient

    from bodhan_dub.web import server
    monkeypatch.setenv("DUB_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("BODHAN_DUB_WEB_DATA", str(tmp_path))
    s = importlib.reload(server)
    try:
        c = TestClient(s.app)
        assert c.get("/api/diagnose").status_code == 401
        assert c.post("/api/diagnose").status_code == 401
        assert c.get("/api/diagnose?token=bad").status_code == 401
    finally:
        monkeypatch.delenv("DUB_ACCESS_TOKEN")
        importlib.reload(server)


def _tone_bytes(seconds=2.0, amp=0.3, sr=24000):
    import io

    import numpy as np
    import soundfile as sf
    buf = io.BytesIO()
    sf.write(buf, (amp * np.sin(2 * np.pi * 300 * np.arange(int(sr * seconds)) / sr)).astype("float32"), sr, format="WAV")
    return buf.getvalue()


def test_edits_merge_and_unset_and_neutral_style(client, video):
    c, _ = client
    with open(video, "rb") as f:
        pid = c.post("/api/projects", files={"file": ("a.mp4", f)}, data={"langs": "hi", "style": "anger"}).json()["id"]
    wait(c, pid)
    ep = f"/api/projects/{pid}/lang/hi/segments/1"
    assert c.patch(ep, json={"text": "मेरा पाठ", "speed": 1.1}).status_code == 200
    wait(c, pid)
    assert c.patch(ep, json={"style": "sad"}).status_code == 200        # style-only edit must not lose the hand-typed text
    wait(c, pid)
    d = c.get(f"/api/projects/{pid}/lang/hi").json()
    assert d["overrides"]["1"] == {"text": "मेरा पाठ", "speed": 1.1, "style": "sad"}
    assert d["segments"][1]["style"] == "sad" and d["segments"][1]["translation"] == "मेरा पाठ"
    assert c.patch(ep, json={"style": "none", "unset": ["speed"]}).status_code == 200
    wait(c, pid)
    d = c.get(f"/api/projects/{pid}/lang/hi").json()
    assert d["overrides"]["1"] == {"text": "मेरा पाठ", "style": "none"} and d["segments"][1]["style"] is None
    assert c.patch(ep, json={"english": "New English line."}).status_code == 200   # new English and no typed text: translate again
    wait(c, pid)
    d = c.get(f"/api/projects/{pid}/lang/hi").json()
    assert "text" not in d["overrides"]["1"] and "New English line." in d["segments"][1]["translation"]
    assert c.patch(ep, json={"style": "shouting"}).status_code == 400


def test_own_voice_upload_use_and_remove(client, video):
    c, _ = client
    with open(video, "rb") as f:
        pid = c.post("/api/projects", files={"file": ("a.mp4", f)}, data={"langs": "hi"}).json()["id"]
    wait(c, pid)
    up = f"/api/projects/{pid}/lang/hi/segments/1/voice"
    assert c.post(up, files={"file": ("x.wav", b"not audio")}).status_code == 400
    assert c.post(up, files={"file": ("s.wav", _tone_bytes(amp=0.0))}).status_code == 400          # silent
    assert c.post(up, files={"file": ("l.wav", _tone_bytes(seconds=61))}).status_code == 400       # too long
    assert c.post(f"/api/projects/{pid}/lang/hi/segments/99/voice", files={"file": ("a.wav", _tone_bytes())}).status_code == 404
    r = c.post(up, files={"file": ("mine.wav", _tone_bytes())})
    assert r.status_code == 200 and abs(r.json()["seconds"] - 2.0) < 0.1
    wait(c, pid)
    d = c.get(f"/api/projects/{pid}/lang/hi").json()
    s1 = d["segments"][1]
    assert d["overrides"]["1"]["custom_audio"] == "custom/seg1.wav" and s1["custom_voice"] and s1["voice"] == "Your voice"
    assert c.get(f"/api/projects/{pid}/file/custom", params={"lang": "hi", "idx": 1}).status_code == 200
    assert c.patch(f"/api/projects/{pid}/lang/hi/segments/1", json={"style": "happy"}).status_code == 200   # other edits keep the recording
    wait(c, pid)
    assert c.get(f"/api/projects/{pid}/lang/hi").json()["segments"][1]["custom_voice"] is True
    assert c.patch(f"/api/projects/{pid}/lang/hi/segments/1", json={"unset": ["custom_audio"]}).status_code == 200
    wait(c, pid)
    d = c.get(f"/api/projects/{pid}/lang/hi").json()
    assert not d["segments"][1].get("custom_voice") and d["segments"][1]["voice"] != "Your voice"
    assert c.get(f"/api/projects/{pid}/file/custom", params={"lang": "hi", "idx": 1}).status_code == 404


def test_preview_voice_and_style(client, video):
    c, _ = client
    with open(video, "rb") as f:
        pid = c.post("/api/projects", files={"file": ("a.mp4", f)}, data={"langs": "hi"}).json()["id"]
    wait(c, pid)
    p = f"/api/projects/{pid}/lang/hi/preview"
    r = c.post(p, json={"text": "नमस्ते दोस्तों", "voice": "Kavya", "style": "anger"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav" and r.content[:4] == b"RIFF"
    assert c.post(p, json={"text": "x", "voice": "Nobody"}).status_code == 400
    assert c.post(p, json={"text": "x", "voice": "Kavya", "style": "yelling"}).status_code == 400
    assert c.post(p, json={"text": "", "voice": "Kavya"}).status_code == 400


def test_job_uses_todays_concurrency_not_the_one_saved_with_the_project(client, video, monkeypatch):
    c, server = client
    seen = []
    real = server.BodhanClient
    monkeypatch.setattr(server, "BodhanClient", lambda keys, cache, max_concurrency=4, **k: (seen.append(max_concurrency), real(keys, cache, max_concurrency=max_concurrency, **k))[1])
    monkeypatch.setenv("DUB_CONCURRENCY", "4")
    with open(video, "rb") as f:
        pid = c.post("/api/projects", files={"file": ("a.mp4", f)}, data={"langs": "hi"}).json()["id"]
    wait(c, pid)
    monkeypatch.setenv("DUB_CONCURRENCY", "1")          # lowered on Render after the project was made
    assert c.post(f"/api/projects/{pid}/rerun").status_code == 200
    wait(c, pid)
    assert seen[0] == 4 and seen[-1] == 1


def test_onscreen_text_project_edit_and_hide(client, video_text):
    c, _ = client
    assert c.get("/api/config").json()["ocr_key"] is True
    with open(video_text, "rb") as f:
        pid = c.post("/api/projects", files={"file": ("t.mp4", f)}, data={"langs": "hi", "ocr": "true"}).json()["id"]
    j = wait(c, pid)
    assert j["job"]["state"] == "done" and j["report"]["onscreen"]["items"] == 2
    d = c.get(f"/api/projects/{pid}/lang/hi").json()
    assert [i["original"] for i in d["onscreen"]["items"]] == ["Revenue grew", "Open source"]
    ep = f"/api/projects/{pid}/lang/hi/onscreen"
    assert c.patch(f"{ep}/9", json={"hide": True}).status_code == 404
    assert c.patch(f"{ep}/0", json={"text": "राजस्व बढ़ा"}).status_code == 200
    wait(c, pid)
    assert c.patch(f"{ep}/1", json={"hide": True}).status_code == 200
    wait(c, pid)
    d = c.get(f"/api/projects/{pid}/lang/hi").json()
    rows = {i["id"]: i for i in d["onscreen"]["items"]}
    assert rows[0]["translation"] == "राजस्व बढ़ा" and rows[1]["status"] == "hidden"      # the text edit survived the hide
    assert d["overrides"]["o0"] == {"text": "राजस्व बढ़ा"} and d["overrides"]["o1"] == {"hide": True}
    assert c.patch(f"{ep}/1", json={"hide": False}).status_code == 200
    wait(c, pid)
    assert {i["id"]: i for i in c.get(f"/api/projects/{pid}/lang/hi").json()["onscreen"]["items"]}[1]["status"] in ("ok", "tight")
    assert c.post(f"/api/projects/{pid}/lang/hi/preview", json={"text": "x", "voice": "Kavya"}).status_code == 200


def test_config_json_is_public_and_points_at_same_origin(tmp_path):
    from fastapi.testclient import TestClient

    from bodhan_dub.web import server
    c = TestClient(server.app)
    r = c.get("/config.json")                                  # no access token: the sign-in page must be able to read it
    assert r.status_code == 200 and r.json() == {"api": ""}
