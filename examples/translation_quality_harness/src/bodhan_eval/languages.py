"""Language registry loaded from languages.json, so adding a language is a data change."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cache
from importlib import resources

PRODUCTION_TIER = ("en", "hi", "bn", "mr", "te", "ta", "gu", "kn", "ml", "or", "pa", "as", "ur")


@dataclass(frozen=True)
class Language:
    code: str
    name: str
    script: str
    tier: str
    voices: tuple[str, ...] = ()
    markers: tuple[str, ...] = ()
    forbidden_chars: tuple[str, ...] = ()
    required_chars: tuple[str, ...] = ()
    known: bool = True
    extra: dict = field(default_factory=dict, compare=False)

    @property
    def default_voice(self) -> str | None:
        return self.voices[0] if self.voices else None


@cache
def _raw() -> dict:
    text = resources.files("bodhan_eval").joinpath("languages.json").read_text(encoding="utf-8")
    return json.loads(text)


@cache
def script_ranges() -> dict[str, tuple[tuple[int, int], ...]]:
    return {name: tuple((lo, hi) for lo, hi in ranges) for name, ranges in _raw()["scripts"].items()}


def script_of(ch: str) -> str | None:
    cp = ord(ch)
    for name, ranges in script_ranges().items():
        for lo, hi in ranges:
            if lo <= cp <= hi:
                return name
    return None


@cache
def get_language(code: str) -> Language:
    entry = _raw()["languages"].get(code)
    if entry is None:
        return Language(code=code, name=code, script="", tier="unknown", known=False)
    return Language(
        code=code,
        name=entry["name"],
        script=entry["script"],
        tier=entry.get("tier", "unknown"),
        voices=tuple(entry.get("voices", [])),
        markers=tuple(entry.get("markers", [])),
        forbidden_chars=tuple(entry.get("forbidden_chars", [])),
        required_chars=tuple(entry.get("required_chars", [])),
    )


def all_codes() -> list[str]:
    return list(_raw()["languages"])


def same_script_siblings(code: str) -> list[Language]:
    lang = get_language(code)
    if not lang.script:
        return []
    return [get_language(c) for c in all_codes() if c != code and get_language(c).script == lang.script]
