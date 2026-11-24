"""Shared fixtures: a fake Bodhan API and an in-process tool context pointed at it."""

from __future__ import annotations

import pytest

from bodhan_mcp.client import BodhanClient
from bodhan_mcp.config import Settings
from bodhan_mcp.tools.common import ToolContext, set_context
from tests.fakes.fake_bodhan import FakeBodhan

KEYS = {m: "test-key" for m in ("indic-transcribe", "indic-translate", "indic-speak", "indic-ocr")}


@pytest.fixture
def fake():
    with FakeBodhan() as server:
        yield server


@pytest.fixture
async def ctx(fake, tmp_path):
    settings = Settings(
        base_url=fake.url, keys=KEYS, cache_dir=tmp_path / "cache", output_dir=tmp_path / "mcp_out", max_retries=3
    )
    client = BodhanClient(settings)

    async def no_sleep(_s: float) -> None:
        return None

    client.sleep = no_sleep
    context = ToolContext(settings, client)
    set_context(context)
    yield context
    await client.aclose()
    set_context(None)
