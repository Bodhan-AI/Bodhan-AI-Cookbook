"""Run checkpoints for --resume.

A checkpoint is one JSON file in <out>/.state/, written atomically between planner
turns and just before a turn's tool calls run. It holds everything needed to continue
the *same* run: task, input fingerprints, settings, conversation, plan, step and spend
counters, artifact records (with their SHA-256) and the provenance corpus. It never
holds credentials. Paid results live in the run's own cache (<out>/.state/cache), so
work completed before an interruption is not billed again on resume.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

CHECKPOINT_VERSION = 1
STATE_DIR = ".state"


class ResumeError(Exception):
    pass


def state_dir(out: Path) -> Path:
    return out / STATE_DIR


def checkpoint_path(out: Path) -> Path:
    return state_dir(out) / "checkpoint.json"


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fingerprint_inputs(files: list[Path]) -> list[dict[str, Any]]:
    return [{"path": str(f), "sha256": file_sha256(f), "bytes": f.stat().st_size} for f in files]


def save(out: Path, state: dict[str, Any]) -> None:
    path = checkpoint_path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps({"version": CHECKPOINT_VERSION, **state}, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(tmp, path)  # a reader sees either the old or the new checkpoint, never half of one


def load(out: Path) -> dict[str, Any]:
    path = checkpoint_path(out)
    if not path.is_file():
        raise ResumeError(f"No checkpoint in {out}; nothing to resume.")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ResumeError(f"The checkpoint in {out} is unreadable: {exc}") from exc
    if state.get("version") != CHECKPOINT_VERSION:
        raise ResumeError(f"Checkpoint version {state.get('version')} is not supported by this version of the agent.")
    if state.get("finished"):
        raise ResumeError(f"The run in {out} already finished (status {state.get('status')}); start a new run instead.")
    return state


def check_inputs(state: dict[str, Any]) -> list[Path]:
    """Inputs must be byte-for-byte what the run started with; otherwise resuming would
    mix results from two different inputs."""
    files = []
    for item in state.get("inputs", []):
        p = Path(item["path"])
        if not p.is_file():
            raise ResumeError(f"Input {p} is missing; start a new run.")
        if file_sha256(p) != item["sha256"]:
            raise ResumeError(f"Input {p} changed since the run started; start a new run.")
        files.append(p)
    return files
