import pytest

import bodhan_eval.clients as clients


@pytest.fixture(autouse=True)
def no_rate_pacing(monkeypatch):
    """Tests talk to a fake server: keep the parallel cap, drop the requests-per-minute spacing."""
    monkeypatch.setattr(clients, "LIMITS", {m: (parallel, None) for m, (parallel, _) in clients.LIMITS.items()})
