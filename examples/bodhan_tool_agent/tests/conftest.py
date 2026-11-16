from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_bodhan import FakeBodhan  # noqa: E402

from bodhan_mcp import costs  # noqa: E402
from bodhan_mcp.bodhan_api import BodhanAPI  # noqa: E402
from bodhan_mcp.config import Settings  # noqa: E402
from bodhan_mcp.tools import Runtime  # noqa: E402

FAKE_KEYS = {m: "test-key-not-real" for m in ("indic-transcribe", "indic-translate", "indic-speak", "indic-ocr")}


@pytest.fixture
def fake() -> FakeBodhan:
    return FakeBodhan()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        base_url="https://api.bodhan.test",
        keys=dict(FAKE_KEYS),
        cache_dir=tmp_path / "cache",
        output_dir=tmp_path / "out",
        backoff_base_s=0.01,
        backoff_cap_s=0.05,
    )


@pytest.fixture
def rt(settings: Settings, fake: FakeBodhan) -> Runtime:
    api = BodhanAPI(settings, costs.Ledger(), httpx.MockTransport(fake.handler))
    return Runtime(settings=settings, api=api)
