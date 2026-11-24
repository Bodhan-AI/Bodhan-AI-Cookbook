"""Turns `--files` (files or folders) into an inventory the planner can use. Plain code, no model calls."""

from __future__ import annotations

from pathlib import Path

from bodhan_core.media import details, kind_of

MAX_FILES = 200


def build_inventory(files: list[str] | None) -> list[dict]:
    """One entry per file: absolute path, kind, size and (for WAV/PDF) duration/pages. Missing paths are listed too."""
    entries: list[dict] = []
    for raw in files or []:
        p = Path(raw).expanduser()
        if not p.exists():
            entries.append({"path": str(p.absolute()), "kind": "missing", "note": "does not exist"})
        elif p.is_dir():
            found = sorted(f for f in p.rglob("*") if f.is_file())
            if not found:
                entries.append({"path": str(p.absolute()), "kind": "empty_folder"})
            entries.extend(_entry(f) for f in found[:MAX_FILES])
            if len(found) > MAX_FILES:
                entries.append({"path": str(p.absolute()), "kind": "note", "note": f"{len(found) - MAX_FILES} more files not listed"})
        else:
            entries.append(_entry(p))
    return entries


def _entry(p: Path) -> dict:
    kind = kind_of(p)
    return {"path": str(p.absolute()), "kind": kind, "size_bytes": p.stat().st_size, **details(p, kind)}


def task_message(task: str, inventory: list[dict], artifacts_dir: Path, max_steps: int, max_cost_inr: float) -> str:
    lines = [f"Task: {task}", "", f"Write every output file under: {artifacts_dir.absolute()}"]
    if inventory:
        lines.append("Input files (pass these paths to tools; never paste file contents):")
        for e in inventory:
            extra = ", ".join(f"{k}={v}" for k, v in e.items() if k not in ("path", "kind"))
            lines.append(f"- {e['path']} [{e['kind']}{', ' + extra if extra else ''}]")
    else:
        lines.append("No input files were provided.")
    lines.append(f"Budget: at most {max_steps} planning steps and Rs {max_cost_inr} in tool cost.")
    return "\n".join(lines)
