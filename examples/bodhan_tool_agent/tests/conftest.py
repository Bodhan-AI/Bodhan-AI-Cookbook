"""Global pytest configuration ensuring tests never make live paid Bodhan API calls."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _block_live_bodhan_calls_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forces BODHAN_ALLOW_LIVE=0 for every test so no test can incur real API spend."""
    monkeypatch.setenv("BODHAN_ALLOW_LIVE", "0")
