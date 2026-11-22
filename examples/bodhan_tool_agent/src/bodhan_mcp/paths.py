from __future__ import annotations

import hashlib
from pathlib import Path

from .config import get_settings
from .errors import ToolFailure


def resolve_input(path: str) -> Path:
    if not path or not str(path).strip():
        raise ToolFailure("missing_path", "A file path is required.")
    p = Path(str(path)).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    if not p.exists():
        raise ToolFailure("file_not_found", f"No such file: {path}",
                          hint="Use the exact paths given in the task context or returned by earlier tools. "
                               "If the task refers to a file that was not provided, report that it is missing.")
    return p


def resolve_output(path: str | None, stem: str, ext: str, key: str = "") -> Path:
    s = get_settings()
    if path:
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = s.work_dir / p
        p = p.resolve()
        try:
            p.relative_to(s.work_dir)
        except ValueError:
            raise ToolFailure("output_outside_workdir",
                              f"output_path must be inside the work directory ({s.work_dir}).",
                              hint="Pass a relative path such as 'summary.ta.mp3'.")
        if not p.suffix and ext:
            p = p.with_suffix(ext)
    else:
        tag = hashlib.sha256(key.encode()).hexdigest()[:8] if key else "out"
        p = s.work_dir / "intermediate" / f"{stem}-{tag}{ext}"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def read_text(path: str) -> str:
    p = resolve_input(path)
    if p.is_dir():
        raise ToolFailure("not_a_file", f"{path} is a directory.")
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise ToolFailure("read_error", f"Could not read {path}: {e}")
