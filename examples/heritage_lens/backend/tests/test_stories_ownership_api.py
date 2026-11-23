from tests.conftest import register

STORY = {"title": "Memories of Bathukamma", "description": "DEMO", "country": "India", "region": "Telangana",
         "language": "te", "category": "festival", "original_text": "నమస్కారం"}


def make_story(client, headers, **over):
    r = client.post("/api/stories", json={**STORY, **over}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def test_create_get_update_delete(client, alice):
    s = make_story(client, alice)
    assert s["privacy"] == "private" and s["publication_status"] == "draft" and s["attribution"] == "alice"
    assert client.get(f"/api/stories/{s['id']}", headers=alice).json()["title"] == STORY["title"]
    r = client.put(f"/api/stories/{s['id']}", json={"title": "New title"}, headers=alice)
    assert r.json()["title"] == "New title" and r.json()["original_text"] == STORY["original_text"]
    assert client.delete(f"/api/stories/{s['id']}", headers=alice).status_code == 204
    assert client.get(f"/api/stories/{s['id']}", headers=alice).status_code == 404


def test_other_users_cannot_read_edit_or_delete_private_story(client, alice, bob):
    s = make_story(client, alice)
    assert client.get(f"/api/stories/{s['id']}", headers=bob).status_code == 404
    assert client.get(f"/api/stories/{s['id']}").status_code == 404
    assert client.put(f"/api/stories/{s['id']}", json={"title": "hacked"}, headers=bob).status_code == 404
    assert client.delete(f"/api/stories/{s['id']}", headers=bob).status_code == 404
    assert client.get(f"/api/stories/{s['id']}", headers=alice).json()["title"] == STORY["title"]


def test_publishing_requires_explicit_consent(client, alice):
    r = client.post("/api/stories", json={**STORY, "publication_status": "published", "privacy": "public"}, headers=alice)
    assert r.status_code == 422 and r.json()["error"]["code"] == "consent_required"
    s = make_story(client, alice)
    r = client.put(f"/api/stories/{s['id']}", json={"publication_status": "published"}, headers=alice)
    assert r.status_code == 422
    r = client.put(f"/api/stories/{s['id']}", json={"publication_status": "published", "privacy": "public",
                                                     "consent_confirmed": True}, headers=alice)
    assert r.status_code == 200 and r.json()["consent_at"] is not None


def test_public_archive_shows_only_published_public(client, alice, bob):
    make_story(client, alice, title="private draft")
    make_story(client, alice, title="public but draft", privacy="public")
    make_story(client, alice, title="published private", publication_status="published", consent_confirmed=True)
    pub = make_story(client, alice, title="open", privacy="public", publication_status="published", consent_confirmed=True)
    listing = client.get("/api/stories?scope=public").json()
    assert [i["title"] for i in listing["items"]] == ["open"]
    assert client.get(f"/api/stories/{pub['id']}").status_code == 200          # anonymous can read it
    assert client.get(f"/api/stories/{pub['id']}", headers=bob).status_code == 200
    assert client.put(f"/api/stories/{pub['id']}", json={"title": "x"}, headers=bob).status_code == 404


def test_mine_scope_lists_only_own_and_paginates(client, alice, bob):
    for i in range(5):
        make_story(client, alice, title=f"a{i}")
    make_story(client, bob, title="b0")
    page = client.get("/api/stories?page=1&page_size=2", headers=alice).json()
    assert page["total"] == 5 and len(page["items"]) == 2 and page["page_size"] == 2
    assert all(i["user_id"] == page["items"][0]["user_id"] for i in page["items"])
    assert client.get("/api/stories?scope=mine").status_code == 401


def test_search_escapes_wildcards(client, alice):
    make_story(client, alice, title="100% real")
    make_story(client, alice, title="other")
    items = client.get("/api/stories?q=%25", headers=alice).json()["items"]
    assert [i["title"] for i in items] == ["100% real"]


def test_coordinates_must_come_in_pairs_and_in_range(client, alice):
    assert client.post("/api/stories", json={**STORY, "latitude": 17.4}, headers=alice).status_code == 422
    assert client.post("/api/stories", json={**STORY, "latitude": 95, "longitude": 10}, headers=alice).status_code == 422
    assert client.post("/api/stories", json={**STORY, "latitude": 17.4, "longitude": 78.5}, headers=alice).status_code == 201


def test_deleting_story_keeps_original_media_unless_asked(client, alice, upstream):
    from tests.test_media_ai_api import wav_bytes
    s = make_story(client, alice)
    up = client.post("/api/audio", files={"file": ("a.wav", wav_bytes(2.0), "audio/wav")},
                     data={"story_id": s["id"]}, headers=alice)
    assert up.status_code == 201
    client.delete(f"/api/stories/{s['id']}", headers=alice)
    kept = client.get(f"/api/audio/{up.json()['id']}", headers=alice)
    assert kept.status_code == 200 and kept.json()["story_id"] is None   # detached, not destroyed
    s2 = make_story(client, alice)
    up2 = client.post("/api/audio", files={"file": ("a.wav", wav_bytes(2.0), "audio/wav")},
                      data={"story_id": s2["id"]}, headers=alice)
    client.delete(f"/api/stories/{s2['id']}?delete_media=true", headers=alice)
    assert client.get(f"/api/audio/{up2.json()['id']}", headers=alice).status_code == 404
