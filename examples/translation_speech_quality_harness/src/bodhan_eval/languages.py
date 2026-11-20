"""Language registry loaded from config/languages.yaml.

Everything language-specific lives in the YAML file, so supporting a new language is a
config change, not a code change.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Language:
    code: str
    name: str
    scripts: tuple[str, ...]
    translate_code: str
    speech_code: str
    chars_per_second: float
    tier: str = "preview"
    voice: str = "Kavya"

    @property
    def expected_script(self) -> str:
        return self.scripts[0]


def _config_text(path: str | os.PathLike | None) -> str:
    path = path or os.environ.get("BODHAN_EVAL_LANGUAGES_FILE")
    if path:
        return Path(path).read_text(encoding="utf-8")
    return resources.files("bodhan_eval").joinpath("config/languages.yaml").read_text("utf-8")


@lru_cache(maxsize=4)
def load_languages(path: str | None = None) -> dict[str, Language]:
    raw = yaml.safe_load(_config_text(path))
    defaults = raw.get("defaults", {})
    out: dict[str, Language] = {}
    for code, spec in raw["languages"].items():
        scripts = tuple(spec["scripts"])
        if not scripts:
            raise ValueError(f"language {code!r} must list at least one script")
        out[code] = Language(
            code=code,
            name=spec.get("name", code),
            scripts=scripts,
            translate_code=spec.get("translate_code", code),
            speech_code=spec.get("speech_code", code),
            tier=spec.get("tier", "preview"),
            voice=spec.get("voice", defaults.get("voice", "Kavya")),
            chars_per_second=float(
                spec.get("chars_per_second", defaults.get("chars_per_second", 13.0))
            ),
        )
    return out


def resolve_langs(arg: str | None, registry: dict[str, Language] | None = None) -> list[str]:
    """Parse a --langs value like 'hi,ta,bn'. Empty/None means 'all languages in the dataset'."""
    registry = registry or load_languages()
    if not arg:
        return []
    codes = [c.strip() for c in arg.split(",") if c.strip()]
    # Accept case-insensitive input but return the registry's canonical spelling (e.g. ks-Deva).
    lookup = {k.lower(): k for k in registry}
    unknown = [c for c in codes if c.lower() not in lookup]
    if unknown:
        raise ValueError(
            f"unknown language code(s): {', '.join(unknown)}. "
            f"Known: {', '.join(sorted(registry))}. Add new ones to config/languages.yaml."
        )
    return [lookup[c.lower()] for c in codes]
