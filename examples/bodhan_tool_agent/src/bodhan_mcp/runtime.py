"""Process-wide runtime: settings, cache and the Bodhan client.

Tools fetch it with get_runtime(); tests and the task agent install their own
with set_runtime() (for example with a fake HTTP transport or a budget).
"""

from __future__ import annotations

import httpx

from .cache import DiskCache
from .client import BodhanClient
from .config import Settings


class Runtime:
    def __init__(self, settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings or Settings.from_env()
        self.cache = DiskCache(self.settings.cache_dir, self.settings.cache_enabled)
        self.client = BodhanClient(self.settings, transport=transport, cache=self.cache)

    @property
    def workdir(self):
        self.settings.workdir.mkdir(parents=True, exist_ok=True)
        return self.settings.workdir


_runtime: Runtime | None = None


def get_runtime() -> Runtime:
    global _runtime
    if _runtime is None:
        _runtime = Runtime()
    return _runtime


def set_runtime(runtime: Runtime | None) -> None:
    global _runtime
    _runtime = runtime
