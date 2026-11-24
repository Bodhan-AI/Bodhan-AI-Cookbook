import base64
import json
import shutil

import httpx
import pytest

from app.services.bodhan.capabilities import SPEAK_OUTPUT
from tests.audio_helpers import make_wav_bytes
from tests.conftest import TEST_KEY, register
from tests.test_stories_ownership_api import STORY, make_story

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg not installed")


def wav_bytes(seconds: float) -> bytes:
    return make_wav_bytes(seconds, rate=16000)


# ---------------- uploads ----------------
@needs_ffmpeg
def test_audio_upload_stores_original_and_measures_duration(client, alice):
    r = client.post("/api/audio", files={"file": ("voice.wav", wav_bytes(3.0), "audio/wav")}, data={"language": "te"}, headers=alice)
    assert r.status_code == 201
    body = r.json()
    assert abs(body["duration"] - 3.0) < 0.05 and body["kind"] == "original" and body["has_original_audio"]
    file = client.get(f"/api/audio/{body['id']}/file", headers=alice)
    assert file.status_code == 200 and file.content == wav_bytes(3.0)


def test_upload_validation_rejections(client, alice):
    cases = [
        ("evil.exe", b"MZ\x90\x00", "application/octet-stream"),
        ("a.wav", b"<html></html>", "audio/wav"),
        ("a.wav", wav_bytes(1.0), "text/html"),
        ("a.png", wav_bytes(1.0), "audio/wav"),
    ]
    for name, data, ctype in cases:
        r = client.post("/api/audio", files={"file": (name, data, ctype)}, headers=alice)
        assert r.status_code == 415, (name, r.text)
    assert client.post("/api/audio", files={"file": ("a.wav", b"", "audio/wav")}, headers=alice).status_code == 422


def test_upload_size_limit(settings, make_app):
    from fastapi.testclient import TestClient
    settings.max_audio_bytes = 1000
    with TestClient(make_app(settings)) as c:
        h = register(c)
        r = c.post("/api/audio", files={"file": ("a.wav", wav_bytes(1.0), "audio/wav")}, headers=h)
        assert r.status_code == 413


@needs_ffmpeg
def test_corrupt_audio_rejected_and_nothing_stored(client, alice, settings):
    bad = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x01" * 100
    r = client.post("/api/audio", files={"file": ("a.wav", bad, "audio/wav")}, headers=alice)
    assert r.status_code == 422 and r.json()["error"]["code"] == "audio_processing_failed"
    assert not any(settings.storage_dir.rglob("*.wav"))


def test_other_user_cannot_fetch_audio_or_documents(client, alice, bob):
    d = client.post("/api/documents", files={"file": ("l.png", PNG, "image/png")}, headers=alice)
    assert d.status_code == 201
    did = d.json()["id"]
    for path in (f"/api/documents/{did}", f"/api/documents/{did}/file"):
        assert client.get(path, headers=bob).status_code == 404
    assert client.delete(f"/api/documents/{did}", headers=bob).status_code == 404
    assert client.get("/api/documents", headers=bob).json()["total"] == 0


def test_stored_files_live_outside_any_public_path_with_random_names(client, alice, settings):
    client.post("/api/documents", files={"file": ("../../etc/passwd.png", PNG, "image/png")}, headers=alice)
    files = [p for p in settings.storage_dir.rglob("*") if p.is_file()]
    assert len(files) == 1 and "passwd" not in files[0].name and files[0].parent.name == "documents"


# ---------------- translate ----------------
def test_translate_saves_separate_record_and_never_touches_story(client, alice, upstream):
    s = make_story(client, alice, original_text="నమస్కారం, మా గ్రామానికి స్వాగతం.")
    seen = {}

    def fn(req):
        seen["auth"], seen["body"], seen["path"] = req.headers["authorization"], json.loads(req.content), req.url.path
        return httpx.Response(200, json={"translation": "Hello, welcome to our village.", "truncated": False})

    upstream["fn"] = fn
    r = client.post("/api/ai/translate", json={"story_id": s["id"], "target_language": "en", "save": True}, headers=alice)
    assert r.status_code == 200, r.text
    assert seen["auth"] == f"Bearer {TEST_KEY}" and seen["path"] == "/translate"
    assert seen["body"] == {"text": "నమస్కారం, మా గ్రామానికి స్వాగతం.", "target_language": "en"}
    body = r.json()
    assert body["provenance"] == "live_bodhan" and body["saved"]["translated_text"] == body["translation"]
    assert client.get(f"/api/stories/{s['id']}", headers=alice).json()["original_text"] == "నమస్కారం, మా గ్రామానికి స్వాగతం."
    tid = body["saved"]["id"]
    edit = client.put(f"/api/translations/{tid}", json={"edited_text": "Welcome to our village."}, headers=alice).json()
    assert edit["edited_text"] == "Welcome to our village." and edit["translated_text"] == body["translation"]


def test_translate_validation_and_ownership(client, alice, bob, upstream):
    s = make_story(client, alice)
    assert client.post("/api/ai/translate", json={"text": "hi", "target_language": "bho"}, headers=alice).status_code == 400
    assert client.post("/api/ai/translate", json={"story_id": s["id"], "target_language": "en"}, headers=bob).status_code == 404
    assert client.post("/api/ai/translate", json={"target_language": "en"}, headers=alice).status_code == 422
    assert upstream["calls"] == []  # nothing reached Bodhan


def test_translate_reports_truncation(client, alice, upstream):
    upstream["fn"] = lambda req: httpx.Response(200, json={"translation": "part", "truncated": True})
    r = client.post("/api/ai/translate", json={"text": "Short unsplittable text here.", "target_language": "te"}, headers=alice)
    assert r.json()["truncated"] is True


def test_upstream_auth_failure_is_safe_and_not_a_user_401(client, alice, upstream):
    upstream["fn"] = lambda req: httpx.Response(401, json={"error": {"message": f"bad key {TEST_KEY}", "code": "invalid_api_key_error"}})
    r = client.post("/api/ai/translate", json={"text": "hello", "target_language": "te"}, headers=alice)
    assert r.status_code == 502 and r.json()["error"]["code"] == "ai_service_auth_failed"
    assert TEST_KEY not in r.text


def test_unconfigured_service_is_explicit_503_not_a_fake_result(settings, make_app, upstream):
    from fastapi.testclient import TestClient
    settings.bodhan_api_key = None  # no shared key and no per-service keys
    with TestClient(make_app(settings)) as c:
        h = register(c)
        r = c.post("/api/ai/translate", json={"text": "hello", "target_language": "te"}, headers=h)
        assert r.status_code == 503 and r.json()["error"]["code"] == "ai_service_not_configured"
    assert upstream["calls"] == []


# ---------------- transliterate ----------------
def test_transliterate_standalone_and_saved(client, alice, upstream):
    upstream["fn"] = lambda req: httpx.Response(200, json={"transliteration": "Namaskaram", "language": "te", "script": "roman"})
    r = client.post("/api/ai/transliterate", json={"text": "నమస్కారం", "language": "te", "save": True}, headers=alice)
    assert r.status_code == 200 and r.json()["converted_text"] == "Namaskaram" and r.json()["saved"]["story_id"] is None
    assert client.get("/api/transliterations", headers=alice).json()["total"] == 1
    assert client.post("/api/ai/transliterate", json={"text": "hi", "language": "en"}, headers=alice).status_code == 400


# ---------------- speak ----------------
def test_speak_stores_binary_wav_linked_to_story(client, alice, upstream, settings):
    s = make_story(client, alice, original_text="నమస్కారం")
    wav = make_wav_bytes(1.5, rate=SPEAK_OUTPUT["sample_rate_hz"])
    seen = {}

    def fn(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, content=wav, headers={"content-type": "audio/wav"})

    upstream["fn"] = fn
    r = client.post("/api/ai/speak", json={"story_id": s["id"], "language": "te", "voice": "Sravani"}, headers=alice)
    assert r.status_code == 201, r.text
    assert seen["body"]["instructions"] == '{"lang": "te"}'
    rec = r.json()
    assert rec["kind"] == "generated" and rec["story_id"] == s["id"] and rec["has_generated_audio"]
    got = client.get(f"/api/audio/{rec['id']}/file", headers=alice)
    assert got.headers["content-type"] == "audio/wav" and got.content[:4] == b"RIFF"


def test_speak_rejects_json_posing_as_audio(client, alice, upstream):
    upstream["fn"] = lambda req: httpx.Response(200, json={"audio": "AAAA"})
    r = client.post("/api/ai/speak/preview", json={"text": "hello", "language": "en", "voice": "Vamsi"}, headers=alice)
    assert r.status_code == 502 and r.json()["error"]["code"] == "ai_service_bad_response"


# ---------------- ocr ----------------
def test_ocr_keeps_original_and_saves_raw_and_edited_separately(client, alice, upstream, settings):
    doc = client.post("/api/documents", files={"file": ("letter.png", PNG, "image/png")}, headers=alice).json()
    seen = {}

    def fn(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"id": "x", "choices": [{"message": {"content": "# Dear friend"}}],
                                         "blocks": [{"order": 1, "text": "Dear friend"}], "usage": {}})

    upstream["fn"] = fn
    r = client.post("/api/ai/ocr", json={"document_id": doc["id"]}, headers=alice)
    assert r.status_code == 200 and r.json()["extracted_text"] == "# Dear friend" and r.json()["ocr_status"] == "completed"
    assert seen["body"]["messages"][0]["content"][0]["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(PNG).decode()
    assert "language" not in seen["body"]
    edit = client.put(f"/api/documents/{doc['id']}/text", json={"extracted_edited_text": "Dear friend,"}, headers=alice).json()
    assert edit["extracted_text"] == "# Dear friend" and edit["extracted_edited_text"] == "Dear friend,"
    assert client.get(f"/api/documents/{doc['id']}/file", headers=alice).content == PNG
    assert client.post("/api/ai/ocr", json={"document_id": doc["id"]}, headers=alice).status_code == 409


def test_ocr_total_failure_recorded(client, alice, upstream):
    doc = client.post("/api/documents", files={"file": ("l.png", PNG, "image/png")}, headers=alice).json()
    upstream["fn"] = lambda req: httpx.Response(422, json={"error": {"message": "bad", "code": "bad"}})
    r = client.post("/api/ai/ocr", json={"document_id": doc["id"]}, headers=alice)
    assert r.status_code == 502 and r.json()["error"]["code"] == "ocr_failed"
    assert client.get(f"/api/documents/{doc['id']}", headers=alice).json()["ocr_status"] == "failed"


# ---------------- transcribe ----------------
@needs_ffmpeg
def test_transcribe_long_audio_in_ordered_segments_and_keeps_original(client, alice, upstream):
    up = client.post("/api/audio", files={"file": ("a.wav", make_wav_bytes(70.0, rate=16000), "audio/wav")}, headers=alice).json()
    texts = iter(["ఒకటి", "రెండు", "మూడు"])
    sizes = []

    def fn(req):
        assert req.url.path == "/v1/audio/transcriptions"
        assert b'name="language"' in req.content and b"indic-transcribe" in req.content
        sizes.append(len(req.content))
        return httpx.Response(200, json={"text": next(texts)})

    upstream["fn"] = fn
    r = client.post("/api/ai/transcribe", json={"audio_id": up["id"], "language": "te"}, headers=alice)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["transcript"] == "ఒకటి రెండు మూడు" and len(body["transcript_segments"]) == 3
    assert all(s["end_seconds"] - s["start_seconds"] <= 29.0 + 1e-6 for s in body["transcript_segments"])
    assert client.get(f"/api/audio/{up['id']}/file", headers=alice).content == make_wav_bytes(70.0, rate=16000)
    # edit keeps the raw transcript
    e = client.put(f"/api/audio/{up['id']}/transcript", json={"transcript_edited": "ఒకటి, రెండు, మూడు"}, headers=alice).json()
    assert e["transcript"] == "ఒకటి రెండు మూడు" and e["transcript_edited"] == "ఒకటి, రెండు, మూడు"
    assert client.post("/api/ai/transcribe", json={"audio_id": up["id"], "language": "te"}, headers=alice).status_code == 409
