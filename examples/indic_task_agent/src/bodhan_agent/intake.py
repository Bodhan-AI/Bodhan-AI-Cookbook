"""Intake: resolve --files, probe once, detect text scripts. Spends nothing."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from bodhan_mcp.core import cache
from bodhan_mcp.tools.probe import probe_media

_INJECTION = re.compile(
    r"\b(ignore\s+(all\s+|any\s+)?(previous|prior|above)|disregard\s+(the\s+)?(previous|above|system)|system\s+prompt|"
    r"you\s+are\s+now|new\s+instructions|do\s+not\s+(tell|reveal)|reveal\s+(the\s+)?(api|secret|key)|"
    r"instead\s+(output|print|send)|exfiltrate)\b",
    re.I,
)


def _injection_warning(path: Path) -> bool:
    """Text inside a file is data; this flags the files that try to give the agent orders."""
    try:
        return bool(_INJECTION.search(path.read_text(encoding="utf-8", errors="ignore")[:20_000]))
    except OSError:
        return False


def resolve_files(spec: str | None) -> list[Path]:
    if not spec:
        return []
    candidates: list[Path] = []
    for chunk in spec.split(","):
        for part in chunk.split():
            path = Path(part).expanduser()
            if path.is_dir():
                candidates.extend(sorted(p for p in path.iterdir() if p.is_file() and not p.name.startswith(".")))
            else:
                candidates.append(path)
    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if str(resolved) in seen:
            continue
        seen.add(str(resolved))
        unique.append(resolved)
    return unique


def inventory(files: list[Path]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """One probe call for the whole set. Returns (rows for the planner, lookup by path)."""
    if not files:
        return [], {}
    result = probe_media([str(f) for f in files])
    rows: list[dict[str, Any]] = []
    lookup: dict[str, dict[str, Any]] = {}
    for item in result.items:
        if item.kind == "text" and _injection_warning(Path(item.path)):
            item.warnings.append("possible_prompt_injection")
        row = {
            "path": item.path,
            "name": Path(item.path).name,
            "kind": item.kind,
            "duration_s": item.duration_s,
            "pages": item.pages,
            "script": item.script,
            "language_hint": item.language_hint,
            "est_asr_cost_inr": item.est_asr_cost_inr,
            "warnings": item.warnings,
        }
        rows.append(row)
        lookup[item.path] = row
        lookup[str(Path(item.path).name)] = row
    return rows, lookup


def file_hashes(files: list[Path]) -> dict[str, str]:
    out = {}
    for path in files:
        try:
            out[str(path)] = cache.file_digest(path)
        except OSError:
            continue
    return out


def dedupe(files: list[Path]) -> list[Path]:
    """Drop byte-identical duplicates so the same input is never paid for twice."""
    seen: set[str] = set()
    out = []
    for path in files:
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            out.append(path)
            continue
        if digest in seen:
            continue
        seen.add(digest)
        out.append(path)
    return out
