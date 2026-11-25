"""In-process result cache: the same input is never sent to the same Bodhan model twice per run."""

from __future__ import annotations

from typing import Generic, Hashable, TypeVar

V = TypeVar("V")


class ResultCache(Generic[V]):
    """A dict that counts hits, so a run can report how much it saved. Behaves like a dict for get/set/in."""

    def __init__(self) -> None:
        self._data: dict[Hashable, V] = {}
        self.hits = 0

    def __contains__(self, key: Hashable) -> bool:
        return key in self._data

    def __getitem__(self, key: Hashable) -> V:
        self.hits += 1
        return self._data[key]

    def __setitem__(self, key: Hashable, value: V) -> None:
        self._data[key] = value

    def __len__(self) -> int:
        return len(self._data)

    def clear(self) -> None:
        self._data.clear()
        self.hits = 0
