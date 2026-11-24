"""Shared fixtures: a real ``RunContext`` (clients optionally pointed at the stub server) and a one-check runner.

Checks are exercised through ``pipeline.run_check`` (the same wrapper the CLI uses), so a test sees exactly
what a run would: status, reason and the signals the check emitted.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from bodhan_eval.budget import Budget
from bodhan_eval.cache import Cache
from bodhan_eval.checks.base import Check, DatasetStats
from bodhan_eval.clients import BodhanClient, LLMClient
from bodhan_eval.config import Settings, defaults
from bodhan_eval.models import Item, ItemResult
from bodhan_eval.pipeline import EventLog, RunContext, run_check
from bodhan_eval.testing.stub_server import StubServer


@pytest.fixture
def start_stub() -> Iterator[Callable[..., StubServer]]:
    """Start a stub server from a fixture dict; stopped at teardown."""
    servers: list[StubServer] = []

    def start(fixture: dict | None = None) -> StubServer:
        s = StubServer(fixture or {}).start()
        servers.append(s)
        return s

    yield start
    for s in servers:
        s.stop()


@pytest.fixture
def make_ctx(tmp_path: Path) -> Iterator[Callable[..., RunContext]]:
    """Build a RunContext. Without a stub, no keys are set: tier-1 checks skip, tier-0 checks run normally."""
    made: list[RunContext] = []

    def build(stub: StubServer | None = None, *, judge: bool = False, **params: Any) -> RunContext:
        k = len(made)
        settings = Settings(
            bodhan_base_url=stub.url if stub else "http://127.0.0.1:9",
            translate_key="stub-key" if stub else None,
            transliterate_key="stub-key" if stub else None,
            speak_key="stub-key" if stub else None,
            transcribe_key="stub-key" if stub else None,
            llm_base_url=f"{stub.url}/v1" if stub and judge else None,
            llm_model="stub-judge" if stub and judge else None,
            llm_api_key="stub-key" if stub and judge else None,
            max_retries=1,
            request_timeout_s=10.0,
        )
        cache, budget = Cache(tmp_path / f"cache{k}"), Budget(None)
        ctx = RunContext(
            params={**defaults()["check_params"], **params},
            judge_cfg=dict(defaults()["judge"]),
            options={"judge": judge, "voices": {}},
            bodhan=BodhanClient(settings, cache, budget),
            llm=LLMClient(settings, cache, budget),
            budget=budget,
            cache=cache,
            events=EventLog(tmp_path / f"events{k}.jsonl"),
            stats=DatasetStats(),
        )
        made.append(ctx)
        return ctx

    yield build
    for ctx in made:
        ctx.events.close()
        ctx.cache.close()


@pytest.fixture
def run_one() -> Callable[..., ItemResult]:
    """Run one check class on one item: ``run_one(ScriptCheck, ctx, source, translation, lang="hi")``."""

    def run(
        check_cls: type[Check], ctx: RunContext, source: str, translation: str | None, lang: str = "hi", **kw: Any
    ) -> ItemResult:
        r = ItemResult(item=Item(id=kw.pop("id", "t"), lang=lang, source=source, translation=translation, **kw))
        r.checks[check_cls.name] = run_check(check_cls(), r, ctx)
        return r

    return run


@pytest.fixture
def run_chain() -> Callable[..., ItemResult]:
    """Run several checks in order on one item (later checks see earlier state, e.g. the back-translation)."""

    def run(checks: list[type[Check]], ctx: RunContext, source: str, translation: str, lang: str = "hi") -> ItemResult:
        r = ItemResult(item=Item(id="t", lang=lang, source=source, translation=translation))
        for cls in checks:
            r.checks[cls.name] = run_check(cls(), r, ctx)
        return r

    return run


def fused(r: ItemResult) -> tuple[list, list]:
    """(flags, review queue) for an item's signals under the default thresholds."""
    from bodhan_eval.fusion import fuse

    d = defaults()
    flags, uncertain, _ = fuse(r.item, r.signals, d["flag_threshold"], d["uncertain_margin"])
    return flags, uncertain


@pytest.fixture
def fuse_item() -> Callable[[ItemResult], tuple[list, list]]:
    return fused
