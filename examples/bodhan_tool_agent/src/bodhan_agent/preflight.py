"""Preflight (code only): find the input files, inspect each one for free, and build a compact manifest."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .guards import compact

SKIP_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}


@dataclass
class Manifest:
    files: list[dict] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    unreadable: list[dict] = field(default_factory=list)

    def for_llm(self) -> list[dict]:
        return self.files


def collect_inputs(paths: list[Path]) -> tuple[list[Path], list[str]]:
    """Expand directories (recursively, sorted). Returns (files, missing paths)."""
    found, missing = [], []
    for p in paths:
        if p.is_dir():
            found.extend(sorted(f for f in p.rglob("*")
                                if f.is_file() and not f.name.startswith(".") and f.name not in SKIP_NAMES))
        elif p.is_file():
            found.append(p)
        else:
            missing.append(str(p))
    seen, unique = set(), []
    for f in found:
        r = f.resolve()
        if r not in seen:
            seen.add(r)
            unique.append(r)
    return unique, missing


async def build_manifest(session, paths: list[Path]) -> Manifest:
    files, missing = collect_inputs(paths)
    manifest = Manifest(missing=missing)
    for i, f in enumerate(files, 1):
        info = await session.call("inspect_media", {"path": str(f)})
        if not info.get("ok"):
            manifest.unreadable.append({"name": f.name, "error": info.get("error")})
            continue
        entry = {"id": f"f{i}", **compact(info, preview_limit=200)}
        entry.pop("ok", None)
        manifest.files.append(entry)
    return manifest
