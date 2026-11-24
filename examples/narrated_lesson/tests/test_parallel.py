"""Unit tests for per-language parallel helper."""

from __future__ import annotations

import time

from bodhan_lesson.parallel import get_max_lang_workers, map_parallel


def test_map_parallel_preserves_order(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("LANG_WORKERS", "3")

    def slow(job: tuple[int, str]) -> str:
        index, name = job
        # Reverse delay so completion order ≠ input order.
        time.sleep(0.05 * (3 - index))
        return name

    out = map_parallel([(1, "hi"), (2, "ta"), (3, "bn")], slow)
    assert out == ["hi", "ta", "bn"]


def test_map_parallel_isolates_via_caller(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("LANG_WORKERS", "2")

    def maybe_fail(lang: str) -> dict[str, str]:
        if lang == "ta":
            return {"lang": lang, "status": "error"}
        return {"lang": lang, "status": "ok"}

    rows = map_parallel(["hi", "ta", "bn"], maybe_fail)
    assert [r["status"] for r in rows] == ["ok", "error", "ok"]


def test_get_max_lang_workers_default(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("LANG_WORKERS", raising=False)
    monkeypatch.delenv("BODHAN_LANG_WORKERS", raising=False)
    assert get_max_lang_workers() == 3


def test_get_max_lang_workers_clamped(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("LANG_WORKERS", "99")
    assert get_max_lang_workers() == 8
    monkeypatch.setenv("LANG_WORKERS", "0")
    assert get_max_lang_workers() == 1
