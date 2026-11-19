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
import time
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
    for attempt in range(20):  # a reader sees either the old or the new checkpoint, never half of one
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # Windows: another process has the old file open for a moment
            if attempt == 19:
                raise
            time.sleep(0.05)


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


def ledger_from_trace(trace: Path) -> dict[str, Any] | None:
    """Rebuild settled spend from trace.jsonl, which is appended as each request settles
    (unlike the checkpoint, which is written between turns). A request that was dispatched
    but never settled before the process stopped is counted as uncertain spend."""
    if not trace.is_file():
        return None
    st: dict[str, Any] = {"billed": 0.0, "uncertain": 0.0, "by_model": {}, "calls": 0, "cache_hits": 0, "llm_prompt_tokens": 0,
                          "llm_completion_tokens": 0, "llm_calls": 0, "llm_cost": 0.0, "llm_priced": False}
    pending: dict[Any, tuple[str, float]] = {}
    llm_pending = 0.0

    def settle_unresolved() -> None:
        for model, amount in pending.values():
            st["uncertain"] += amount
            st["by_model"][model] = st["by_model"].get(model, 0.0) + amount
        pending.clear()

    for line in trace.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue  # a line cut off by the interruption
        kind = e.get("type")
        if kind in ("run_start", "run_resumed"):
            settle_unresolved()  # reservation ids restart with each process
            if llm_pending:  # the previous process died during a priced planner call
                st["llm_cost"] += llm_pending
                st["llm_priced"] = True
                llm_pending = 0.0
        elif kind == "api_dispatch":
            pending[e.get("reservation_id")] = (e.get("model", "?"), float(e.get("amount_inr") or 0))
        elif kind == "api_call":
            if e.get("cache") in ("this_run", "previous_run", "in_flight"):
                st["cache_hits"] += 1
                continue
            pending.pop(e.get("reservation_id"), None)
            cost, model = float(e.get("cost_inr") or 0), e.get("model", "?")
            if e.get("billing") == "billed":
                st["billed"] += cost
                st["calls"] += 1
                if e.get("cost_basis") == "bounded_estimate":
                    st["estimated"] = st.get("estimated", 0.0) + cost
            elif e.get("billing") in ("uncertain", "billed_unusable"):
                st["uncertain"] += cost
            else:
                continue
            st["by_model"][model] = st["by_model"].get(model, 0.0) + cost
        elif kind == "llm_dispatch":
            llm_pending = float(e.get("estimate_inr") or 0) * int(e.get("max_attempts") or 1)
        elif kind == "llm_call":
            llm_pending = 0.0
            st["llm_calls"] += 1
            st["llm_prompt_tokens"] += int(e.get("prompt_tokens") or 0)
            st["llm_completion_tokens"] += int(e.get("completion_tokens") or 0)
            if e.get("cost_inr") is not None:
                st["llm_cost"] += float(e["cost_inr"])
                st["llm_priced"] = True
        elif kind == "llm_estimate_exceeded":
            st["llm_over_bound"] = st.get("llm_over_bound", 0.0) + max(0.0, float(e["cost_inr"]) - float(e["estimate_inr"]))
        elif kind in ("llm_uncertain", "llm_error"):
            llm_pending = 0.0
        if kind in ("llm_uncertain", "llm_error") and e.get("cost_inr") is not None:  # failed attempts charged conservatively
            st["llm_cost"] += float(e["cost_inr"])
            st["llm_priced"] = True
    settle_unresolved()
    if llm_pending:  # a priced planner call was sent and never settled (process killed): count every attempt it could make
        st["llm_cost"] += llm_pending
        st["llm_priced"] = True
    return st
