"""Configuration loading: language metadata, Bodhan API wiring, thresholds, env keys.

Everything tunable lives in config/ files; nothing is special-cased per demo language.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

# config/ sits two levels up from this file: src/bodhan_eval/config.py -> <recipe>/config
_PKG_DIR = Path(__file__).resolve().parent
_DEFAULT_CONFIG_DIR = _PKG_DIR.parent.parent / "config"


def config_dir() -> Path:
    override = os.environ.get("BODHAN_EVAL_CONFIG_DIR")
    if override:
        return Path(override)
    if all((_DEFAULT_CONFIG_DIR / name).is_file()
           for name in ("languages.yaml", "bodhan_api.yaml", "thresholds.json")):
        return _DEFAULT_CONFIG_DIR
    return _PKG_DIR / "default_config"


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


# ---- Languages ------------------------------------------------------------------------


@dataclass(frozen=True)
class ScriptSpec:
    name: str
    blocks: tuple[tuple[int, int], ...]

    def contains(self, cp: int) -> bool:
        return any(lo <= cp <= hi for lo, hi in self.blocks)


@dataclass(frozen=True)
class LanguageSpec:
    code: str
    name: str
    script: str


@dataclass
class LanguageConfig:
    scripts: dict[str, ScriptSpec]
    languages: dict[str, LanguageSpec]

    def get(self, code: str) -> LanguageSpec | None:
        return self.languages.get(code)

    def script_for(self, code: str) -> ScriptSpec | None:
        lang = self.languages.get(code)
        if lang is None:
            return None
        return self.scripts.get(lang.script)

    def all_indic_scripts(self) -> list[ScriptSpec]:
        return list(self.scripts.values())


@lru_cache(maxsize=1)
def load_languages() -> LanguageConfig:
    data = _load_yaml(config_dir() / "languages.yaml")
    scripts: dict[str, ScriptSpec] = {}
    for name, spec in (data.get("scripts") or {}).items():
        blocks = tuple((int(lo, 16), int(hi, 16)) for lo, hi in spec["blocks"])
        scripts[name] = ScriptSpec(name=name, blocks=blocks)
    languages: dict[str, LanguageSpec] = {}
    for entry in data.get("languages") or []:
        languages[entry["code"]] = LanguageSpec(
            code=entry["code"], name=entry["name"], script=entry["script"]
        )
    return LanguageConfig(scripts=scripts, languages=languages)


# ---- Bodhan API wiring ----------------------------------------------------------------


@lru_cache(maxsize=1)
def load_api_config() -> dict[str, Any]:
    return _load_yaml(config_dir() / "bodhan_api.yaml")


# ---- Thresholds -----------------------------------------------------------------------


@dataclass
class Thresholds:
    defaults: dict[str, float]
    per_language: dict[str, dict[str, float]] = field(default_factory=dict)
    judge_confidence_multiplier: dict[str, float] = field(default_factory=dict)
    low_support: list[str] = field(default_factory=list)

    def get(self, key: str, lang: str | None = None) -> float:
        if lang and lang in self.per_language and key in self.per_language[lang]:
            return self.per_language[lang][key]
        return self.defaults[key]

    def judge_multiplier(self, lang: str) -> float:
        return self.judge_confidence_multiplier.get(lang, 1.0)


def load_thresholds(path: Path | None = None) -> Thresholds:
    p = path or (config_dir() / "thresholds.json")
    data = _load_json(p)
    return Thresholds(
        defaults={k: float(v) for k, v in data["defaults"].items() if not k.startswith("_")},
        per_language=data.get("per_language", {}),
        judge_confidence_multiplier=data.get("judge_confidence_multiplier", {}),
        low_support=data.get("low_support", []),
    )


# ---- Environment ----------------------------------------------------------------------


def env(name: str, default: str | None = None) -> str | None:
    val = os.environ.get(name, default)
    return val if val else default
