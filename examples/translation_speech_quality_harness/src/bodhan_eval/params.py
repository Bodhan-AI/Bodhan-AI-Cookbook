"""Check thresholds: defaults from config/thresholds.yaml, optionally overridden by calibration."""

from __future__ import annotations

import copy
from importlib import resources
from pathlib import Path
from typing import Any

import yaml


class Params:
    def __init__(self, values: dict[str, dict[str, Any]]):
        self.values = values

    @classmethod
    def defaults(cls) -> Params:
        text = resources.files("bodhan_eval").joinpath("config/thresholds.yaml").read_text("utf-8")
        return cls(yaml.safe_load(text))

    @classmethod
    def from_file(cls, path: str | Path) -> Params:
        return cls(yaml.safe_load(Path(path).read_text("utf-8")))

    def section(self, name: str) -> dict[str, Any]:
        return self.values.get(name, {})

    def get(self, section: str, key: str, default: Any = None) -> Any:
        return self.values.get(section, {}).get(key, default)

    def with_overrides(self, overrides: dict[str, dict[str, Any]]) -> Params:
        v = copy.deepcopy(self.values)
        for sec, kv in overrides.items():
            v.setdefault(sec, {}).update(kv)
        return Params(v)
