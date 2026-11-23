import httpx

from tests.conftest import TEST_KEY, register
from tests.test_stories_ownership_api import STORY, make_story

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PUBLIC = {"privacy": "public", "publication_status": "published", "consent_confirmed": True}


def test_config_status_reports_presence_only(client):
    r = client.get("/api/config/status")
    assert r.status_code == 200
    body = r.json()
    assert body["all_configured"] is True and body["services"]["indic-ocr"] == {"configured": True, "key_source": "shared"}
    assert TEST_KEY not in r.text and TEST_KEY[:6] not in r.text


def test_placeholder_values_count_as_unconfigured(settings, make_app):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    settings.bodhan_api_key = SecretStr("your_bodhan_api_key_here")
    settings.bodhan_speak_api_key = SecretStr("real-looking-speak-key-123")
    with TestClient(make_app(settings)) as c:
        s = c.get("/api/config/status").json()["services"]
        assert s["indic-translate"]["configured"] is False
        assert s["indic-speak"] == {"configured": True, "key_source": "service"}
        assert "real-looking" not in c.get("/api/config/status").text


def test_explorer_filters_sort_and_facets(client, alice):
    make_story(client, alice, title="B story", region="Telangana", language="te", historical_period="1950s", **PUBLIC)
    make_story(client, alice, title="A story", region="Kyoto", country="Japan", language="ja", category="traditional_art", **PUBLIC)
    make_story(client, alice, title="hidden", **{"privacy": "private"})
    items = client.get("/api/stories?scope=public&sort=title").json()["items"]
    assert [i["title"] for i in items] == ["A story", "B story"]
    assert [i["title"] for i in client.get("/api/stories?scope=public&region=kyoto").json()["items"]] == ["A story"]
    assert [i["title"] for i in client.get("/api/stories?scope=public&period=1950s").json()["items"]] == ["B story"]
    f = client.get("/api/discover/facets").json()
    assert f["total"] == 2 and {x["value"] for x in f["languages"]} == {"te", "ja"}


def test_bundle_hides_private_documents_and_private_stories(client, alice, bob, upstream):
    s = make_story(client, alice, **PUBLIC)
    client.post("/api/documents", files={"file": ("l.png", PNG, "image/png")}, data={"story_id": s["id"]}, headers=alice)
    owner = client.get(f"/api/stories/{s['id']}/bundle", headers=alice).json()
    other = client.get(f"/api/stories/{s['id']}/bundle", headers=bob).json()
    assert len(owner["documents"]) == 1 and other["documents"] == [] and other["is_owner"] is False
    private = make_story(client, alice)
    assert client.get(f"/api/stories/{private['id']}/bundle", headers=bob).status_code == 404
    assert client.get(f"/api/stories/{private['id']}/bundle").status_code == 404


def test_public_story_images_visible_to_anonymous_private_not(client, alice):
    pub, priv = make_story(client, alice, **PUBLIC), make_story(client, alice)
    a = client.post("/api/images", files={"file": ("p.png", PNG, "image/png")}, data={"story_id": pub["id"], "caption": "c"}, headers=alice).json()
    b = client.post("/api/images", files={"file": ("p.png", PNG, "image/png")}, data={"story_id": priv["id"]}, headers=alice).json()
    assert client.get(f"/api/images/{a['id']}/file").content == PNG
    assert client.get(f"/api/images/{b['id']}/file").status_code == 404
    assert client.get(f"/api/images/{b['id']}/file", headers=alice).status_code == 200


def test_locations_only_return_supplied_coordinates(client, alice):
    make_story(client, alice, title="with", latitude=17.38, longitude=78.48, **PUBLIC)
    make_story(client, alice, title="without", **PUBLIC)
    make_story(client, alice, title="private", latitude=1.0, longitude=1.0)
    items = client.get("/api/locations").json()["items"]
    assert [i["title"] for i in items] == ["with"]


def test_timeline_never_invents_dates(client, alice):
    make_story(client, alice, title="dated", event_year=1952, event_year_approximate=False, **PUBLIC)
    make_story(client, alice, title="undated", **PUBLIC)
    t = client.get("/api/timeline").json()
    assert [s["title"] for s in t["dated"]] == ["dated"] and [s["title"] for s in t["undated"]] == ["undated"]
    assert client.post("/api/stories", json={**STORY, "event_year": 5000}, headers=alice).status_code == 422


def test_time_capsule_crud_ownership_and_honest_note(client, alice, bob):
    r = client.post("/api/time-capsules", json={"title": "For Mia", "message": "hello", "intended_release_date": "2036-05-01", "status": "sealed"}, headers=alice)
    assert r.status_code == 201 and "does not yet send" in r.json()["delivery_note"]
    cid = r.json()["id"]
    assert client.get(f"/api/time-capsules/{cid}", headers=bob).status_code == 404
    assert client.put(f"/api/time-capsules/{cid}", json={"message": "x"}, headers=bob).status_code == 404
    assert client.put(f"/api/time-capsules/{cid}", json={"message": "updated"}, headers=alice).json()["message"] == "updated"
    assert client.post("/api/time-capsules", json={"title": "x", "status": "sealed"}, headers=alice).status_code == 422
    assert client.delete(f"/api/time-capsules/{cid}", headers=alice).status_code == 204


def test_saved_stories_respect_visibility(client, alice, bob):
    pub, priv = make_story(client, alice, **PUBLIC), make_story(client, alice)
    assert client.put(f"/api/saved/{pub['id']}", headers=bob).status_code == 204
    assert client.put(f"/api/saved/{pub['id']}", headers=bob).status_code == 204   # idempotent
    assert client.put(f"/api/saved/{priv['id']}", headers=bob).status_code == 404
    assert client.get("/api/saved", headers=bob).json()["total"] == 1
    client.put(f"/api/stories/{pub['id']}", json={"privacy": "private"}, headers=alice)
    assert client.get("/api/saved", headers=bob).json()["total"] == 0


def test_dashboard_counts_are_real_and_per_user(client, alice, bob):
    make_story(client, alice, title="d1")
    make_story(client, alice, title="p1", **PUBLIC)
    d = client.get("/api/dashboard", headers=alice).json()
    assert d["stories"]["total"] == 2 and d["stories"]["drafts"] == 1 and d["stories"]["published"] == 1
    assert all(a["type"] in {"story", "document", "audio_original", "audio_generated", "translation", "time_capsule"} for a in d["recent_activity"])
    e = client.get("/api/dashboard", headers=bob).json()
    assert e["stories"]["total"] == 0 and e["recent_activity"] == [] and e["translations"] == 0
