"""Content-addressed SQLite cache (TESTING.md §1)."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from bodhan_eval.cache import Cache, bytes_hash, stable_hash


@pytest.fixture
def cache(tmp_path: Path) -> Iterator[Cache]:
    c = Cache(tmp_path / "cache")
    yield c
    c.close()


BASE = {"kind": "translate", "model": "indic-translate", "params": {"src": "en", "tgt": "hi"}, "payload": "Hello"}


def test_same_input_same_key_regardless_of_dict_order() -> None:
    k1 = Cache.key("translate", "indic-translate", {"src": "en", "tgt": "hi"}, "Hello")
    k2 = Cache.key("translate", "indic-translate", {"tgt": "hi", "src": "en"}, "Hello")
    assert k1 == k2
    assert stable_hash({"a": 1, "b": 2}) == stable_hash({"b": 2, "a": 1})


@pytest.mark.parametrize(
    "change",
    [
        pytest.param({"kind": "transliterate"}, id="kind"),
        pytest.param({"model": "indic-speak"}, id="model"),
        pytest.param({"params": {"src": "en", "tgt": "ta"}}, id="param-value"),
        pytest.param({"params": {"src": "en", "tgt": "hi", "voice": "Kavya"}}, id="extra-param"),
        pytest.param({"payload": "Hello."}, id="payload"),
    ],
)
def test_any_change_gives_a_new_key(change: dict) -> None:
    changed = {**BASE, **change}
    assert Cache.key(**BASE) != Cache.key(**changed)


def test_put_get_round_trip_with_cost(cache: Cache) -> None:
    key = Cache.key(**BASE)
    assert cache.get(key) is None
    cache.put(key, "translate", {"text": "नमस्ते", "tokens": 3}, 0.0006)
    assert cache.get(key) == ({"text": "नमस्ते", "tokens": 3}, 0.0006)


def test_entries_persist_across_instances(tmp_path: Path) -> None:
    c1 = Cache(tmp_path / "c")
    c1.put("k", "llm", {"text": "{}"}, 0.5)
    c1.close()
    c2 = Cache(tmp_path / "c")
    try:
        assert c2.get("k") == ({"text": "{}"}, 0.5)
    finally:
        c2.close()


def test_blob_round_trip_and_idempotence(cache: Cache) -> None:
    data = b"RIFF\x00\x01fake-wav-bytes"
    digest = cache.put_blob(data)
    assert digest == bytes_hash(data)
    assert cache.blob_path(digest).read_bytes() == data
    assert cache.put_blob(data) == digest
    assert len(list((cache.root / "blobs").iterdir())) == 1


def test_parallel_puts_are_thread_safe(cache: Cache) -> None:
    errors: list[BaseException] = []

    def writer(t: int) -> None:
        try:
            for i in range(50):
                cache.put(f"t{t}-{i}", "translate", {"t": t, "i": i}, 0.01)
        except BaseException as e:  # collected and asserted below
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(t,)) for t in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert errors == []
    assert all(cache.get(f"t{t}-{i}") == ({"t": t, "i": i}, 0.01) for t in range(8) for i in range(50))
