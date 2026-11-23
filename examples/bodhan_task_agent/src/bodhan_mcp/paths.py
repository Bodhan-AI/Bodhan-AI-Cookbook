"""File references: validation of input paths and placement of output files.

Media moves between tools as file paths, never as inline base64 in a model's
context. Inputs must exist (and lie inside ``BODHAN_MCP_ALLOWED_DIRS`` when that
allowlist is set); outputs are written only under the output directory, with
deterministic names so a rerun overwrites rather than accumulates.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from . import errors


def resolve_input(raw: str, allowed: list[Path], *, what: str = "file") -> Path:
    if not raw or not str(raw).strip():
        raise errors.BodhanError(errors.INVALID_INPUT, f"No {what} path given.", hint="Pass an absolute or relative file path.")
    raw = str(raw).strip()
    if raw.startswith("file://"):
        raw = raw[len("file://"):]
    p = Path(raw).expanduser()
    try:
        p = p.resolve()
    except OSError as exc:
        raise errors.BodhanError(errors.INVALID_INPUT, f"Invalid path {raw!r}: {exc}") from exc
    if allowed and not any(p == a or a in p.parents for a in allowed):
        raise errors.BodhanError(
            errors.PATH_NOT_ALLOWED, f"{p} is outside the directories this server may read.",
            hint=f"Allowed: {', '.join(map(str, allowed))} (BODHAN_MCP_ALLOWED_DIRS).",
        )
    if not p.exists():
        raise errors.BodhanError(errors.FILE_NOT_FOUND, f"{what.capitalize()} not found: {p}",
                                 hint="Check the path; relative paths resolve against the server's working directory.")
    if not p.is_file():
        raise errors.BodhanError(errors.INVALID_INPUT, f"{p} is not a file.", hint="Pass a file, not a directory.")
    return p


def safe_stem(name: str, max_len: int = 60) -> str:
    stem = re.sub(r"[^\w.-]+", "_", name, flags=re.UNICODE).strip("._") or "output"
    return stem[:max_len]


def short_hash(*parts: str | bytes) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode() if isinstance(part, str) else part)
        h.update(b"\x00")
    return h.hexdigest()[:8]


def output_path(output_dir: Path, filename: str) -> Path:
    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    target = (output_dir / safe_stem(filename, 120)).resolve()
    if output_dir not in target.parents:
        raise errors.BodhanError(errors.INVALID_INPUT, f"Output name {filename!r} escapes the output directory.")
    return target
