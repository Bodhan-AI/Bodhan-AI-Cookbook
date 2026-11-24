"""Fixtures for tests that need the full stack (FastAPI, SQLAlchemy, httpx)."""
import httpx
import pytest

from app.core.config import Settings
from app.core.logging import clear_secrets
from app.main import create_app
from app.services.bodhan.client import BodhanClient

TEST_KEY = "test-key-0123456789"


async def _no_sleep(_seconds: float) -> None:
    return None


@pytest.fixture
def settings(tmp_path):
    return Settings(app_env="test", database_url="sqlite://", secret_key="k" * 48,
                    storage_dir=tmp_path / "storage", bodhan_api_key=TEST_KEY,
                    bodhan_base_url="https://api.bodhan.test", _env_file=None)


@pytest.fixture
def upstream():
    """Mutable holder: set upstream['fn'] = lambda request: httpx.Response(...) in a test."""
    holder = {"calls": []}

    def default(request):
        raise AssertionError(f"Unexpected upstream call: {request.method} {request.url}")

    holder["fn"] = default
    return holder


@pytest.fixture
def make_app(upstream):
    def _make(settings):
        def handler(request: httpx.Request):
            upstream["calls"].append(request)
            return upstream["fn"](request)

        client = BodhanClient.from_settings(settings, transport=httpx.MockTransport(handler), sleep=_no_sleep)
        return create_app(settings, bodhan_client=client)
    return _make


@pytest.fixture
def client(settings, make_app):
    from fastapi.testclient import TestClient

    clear_secrets()
    with TestClient(make_app(settings)) as c:
        yield c


def register(client, name="alice", password="correct-horse-battery"):
    r = client.post("/api/auth/register", json={"username": name, "email": f"{name}@example.com",
                                                "password": password})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def alice(client):
    return register(client, "alice")


@pytest.fixture
def bob(client):
    return register(client, "bob")
