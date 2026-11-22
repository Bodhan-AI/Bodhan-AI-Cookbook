"""Loads config.yaml (limits and prices for each Bodhan model). BODHAN_CONFIG can point to an override file."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

# <recipe>/config/config.yaml, next to src/ (this file is <recipe>/src/bodhan_mcp/config.py)
_DEFAULT = Path(__file__).resolve().parents[2] / "config" / "config.yaml"


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


@lru_cache(maxsize=1)
def load() -> dict:
    cfg = yaml.safe_load(_DEFAULT.read_text(encoding="utf-8"))
    override = os.environ.get("BODHAN_CONFIG")
    if override:
        cfg = _merge(cfg, yaml.safe_load(Path(override).read_text(encoding="utf-8")) or {})
    return cfg


def section(name: str) -> dict:
    return load()[name]
