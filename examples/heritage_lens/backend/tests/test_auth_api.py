from tests.conftest import register


def test_register_login_me_roundtrip(client):
    r = client.post("/api/auth/register", json={"username": "Asha", "email": "Asha@Example.com", "password": "pass-word-123"})
    assert r.status_code == 201
    body = r.json()
    assert body["user"]["email"] == "asha@example.com" and "password" not in str(body).lower().replace("password_hash", "")
    assert "password_hash" not in body["user"]
    login = client.post("/api/auth/login", json={"email": "ASHA@example.com", "password": "pass-word-123"})
    assert login.status_code == 200
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {login.json()['access_token']}"})
    assert me.status_code == 200 and me.json()["username"] == "Asha"


def test_password_is_hashed_in_db(client):
    register(client, "carol", "plain-secret-1")
    from app.models import User
    with client.app.state.session_factory() as db:
        u = db.query(User).filter_by(username="carol").one()
        assert u.password_hash != "plain-secret-1" and u.password_hash.startswith("$2")


def test_duplicate_registration_rejected(client):
    register(client, "dave")
    r = client.post("/api/auth/register", json={"username": "DAVE", "email": "other@example.com", "password": "pass-word-123"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_registered"


def test_wrong_password_and_unknown_user_look_identical(client):
    register(client, "erin")
    a = client.post("/api/auth/login", json={"email": "erin@example.com", "password": "wrong-password"})
    b = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "wrong-password"})
    assert a.status_code == b.status_code == 401 and a.json()["error"]["message"] == b.json()["error"]["message"]


def test_validation_errors_do_not_echo_the_password(client):
    r = client.post("/api/auth/register", json={"username": "x", "email": "not-an-email", "password": "short-pw"})
    assert r.status_code == 422
    assert "short-pw" not in r.text


def test_password_over_72_bytes_rejected(client):
    r = client.post("/api/auth/register", json={"username": "frank", "email": "f@example.com", "password": "é" * 40})
    assert r.status_code == 422


def test_protected_routes_need_a_valid_token(client):
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401
    assert client.get("/api/stories").status_code == 401
    assert client.post("/api/stories", json={"title": "x"}).status_code == 401
    assert client.get("/api/audio").status_code == 401
    assert client.post("/api/ai/translate", json={"text": "x", "target_language": "te"}).status_code == 401


def test_error_envelope_has_request_id(client):
    r = client.get("/api/auth/me")
    assert r.json()["error"]["request_id"] == r.headers["x-request-id"]


def test_health_is_public_and_leaks_no_secrets(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["database"] == "ok"
    assert "test-key-0123456789" not in r.text and r.json()["bodhan"]["keys_configured"]["translate"] is True


def test_capabilities_public_and_per_service(client):
    caps = client.get("/api/ai/capabilities").json()["services"]
    assert "bhb" not in caps["indic-translate"]["target_languages"]
    assert "bhb" in caps["indic-transcribe"]["languages"]
    assert "en" not in caps["indic-transliterate"]["languages"]
