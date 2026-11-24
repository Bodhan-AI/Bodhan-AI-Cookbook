import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from fakes import FakeBodhan, make_service


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    # Tests never touch real keys, the real cache, or a real LLM.
    for var in ("BODHAN_API_KEY", "BODHAN_TRANSCRIBE_KEY", "BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY", "BODHAN_OCR_KEY",
                "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY", "BODHAN_MCP_ALLOWED_DIRS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("BODHAN_CACHE_DIR", str(tmp_path / "cache"))


@pytest.fixture
def fake() -> FakeBodhan:
    return FakeBodhan()


@pytest.fixture
def service(tmp_path, fake):
    return make_service(tmp_path, fake)
