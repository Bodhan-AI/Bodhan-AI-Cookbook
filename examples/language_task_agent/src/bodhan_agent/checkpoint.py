"""Private JSON continuation. No credentials, executable serialization or budget reset."""

import hmac
import hashlib
import secrets
import json
import os
import time
from decimal import Decimal
from pathlib import Path

from bodhan_core.runtime import Run, ToolError, digest, decimal


def config_fingerprint():
    return digest(
        {
            k: os.environ.get(k, "")
            for k in (
                "BODHAN_CACHE_ONLY",
                "LLM_BASE_URL",
                "LLM_MODEL",
                "LLM_INPUT_INR_PER_MILLION",
                "LLM_OUTPUT_INR_PER_MILLION",
                "LLM_TOKEN_LIMIT_FIELD",
            )
        }
    )


def inputs_fingerprint(run):
    files = getattr(run, "input_files", None)
    if files is None:
        files = sorted(run.input_root.rglob("*"))
    result = []
    for p in files:
        if p.is_file():
            resolved = p.resolve(strict=True)
            if not resolved.is_relative_to(run.input_root):
                raise ToolError("invalid_checkpoint", "Input escaped its root.")
            result.append([str(resolved), digest(resolved.read_bytes())])
    return result


def save(run, task, messages):
    state = {
        "version": 1,
        "task": task,
        "input_root": str(run.input_root),
        "config": config_fingerprint(),
        "inputs": inputs_fingerprint(run),
        "limits": {
            "max_cost": str(run.limits.max_cost),
            "max_steps": run.limits.max_steps,
            "call_timeout": run.limits.call_timeout,
            "run_timeout": run.limits.run_timeout,
        },
        "elapsed": run.elapsed(),
        "cost": str(run.cost),
        "steps": run.steps,
        "refs": {ref: str(path) for ref, path in run.refs.items()},
        "provenance": run.provenance,
        "checks": run.checks,
        "calls": run.calls,
        "assumptions": run.assumptions,
        "operations": run.operations,
        "messages": messages,
        "resume_count": run.resume_count,
        "planner_state": getattr(run, "planner_state", {}),
        "lineage": getattr(run, "lineage", None),
        "file_hashes": {
            ref: digest(path.read_bytes()) for ref, path in run.refs.items() if path.is_file()
        },
    }
    path = run.output / ".continuation.json"
    temporary = path.with_suffix(".tmp")
    if any(
        p.is_symlink()
        for p in (
            path,
            temporary,
            run.output / ".continuation.key",
            run.output / ".continuation.mac",
        )
    ):
        raise ToolError("invalid_checkpoint", "Continuation metadata must not be symlinks.")
    temporary.write_text(json.dumps(state, ensure_ascii=False))
    temporary.chmod(0o600)
    temporary.replace(path)
    key_path = run.output / ".continuation.key"
    if not key_path.exists():
        with key_path.open("xb") as stream:
            stream.write(secrets.token_bytes(32))
        key_path.chmod(0o600)
    signature = hmac.new(key_path.read_bytes(), path.read_bytes(), hashlib.sha256).hexdigest()
    (run.output / ".continuation.mac").write_text(signature)
    return path


def restore(root: Path, output: Path, limits, task):
    root, output = root.resolve(strict=True), output.resolve(strict=True)
    path = output / ".continuation.json"
    if path.stat().st_size > 2_000_000:
        raise ToolError("invalid_checkpoint", "Continuation exceeds its safety limit.")
    key_path, mac_path = output / ".continuation.key", output / ".continuation.mac"
    if any(p.is_symlink() for p in (path, key_path, mac_path)):
        raise ToolError("invalid_checkpoint", "Continuation metadata must not be symlinks.")
    signature = hmac.new(key_path.read_bytes(), path.read_bytes(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, mac_path.read_text()):
        raise ToolError("invalid_checkpoint", "Continuation integrity check failed.")
    s = json.loads(path.read_text())
    expected = {
        "version",
        "task",
        "input_root",
        "config",
        "inputs",
        "limits",
        "elapsed",
        "cost",
        "steps",
        "refs",
        "provenance",
        "checks",
        "calls",
        "assumptions",
        "operations",
        "messages",
        "resume_count",
        "planner_state",
        "lineage",
        "file_hashes",
    }
    current_limits = {
        "max_cost": str(limits.max_cost),
        "max_steps": limits.max_steps,
        "call_timeout": limits.call_timeout,
        "run_timeout": limits.run_timeout,
    }
    if (
        set(s) != expected
        or s["version"] != 1
        or s["task"] != task
        or s["input_root"] != str(root)
        or s["config"] != config_fingerprint()
        or s["limits"] != current_limits
    ):
        raise ToolError(
            "invalid_checkpoint",
            "Task, provider configuration, roots or cumulative limits changed.",
        )
    if (
        not isinstance(s["messages"], list)
        or not isinstance(s["checks"], list)
        or not isinstance(s["calls"], list)
        or not isinstance(s["steps"], int)
        or not 0 <= s["steps"] <= limits.max_steps
        or not 0 <= s["elapsed"] <= limits.run_timeout
        or not Decimal(0) <= decimal(s["cost"]) <= limits.max_cost
    ):
        raise ToolError("invalid_checkpoint", "Invalid continuation budget or messages.")
    run = Run.__new__(Run)
    run.input_root, run.output, run.limits = root, output, limits
    run.artifacts, run.work, run.trace = (
        output / "artifacts",
        output / ".work",
        output / "trace.jsonl",
    )
    if any(
        p.is_symlink() or not p.resolve().is_relative_to(output)
        for p in (run.artifacts, run.work, run.trace)
    ):
        raise ToolError("invalid_checkpoint", "Continuation output path is unsafe.")
    run.started, run.elapsed_before, run.execution_window = time.monotonic(), s["elapsed"], None
    run.cost, run.steps = Decimal(s["cost"]), s["steps"]
    run.refs, run.provenance = {}, s["provenance"]
    for ref, value in s["refs"].items():
        p = Path(value).resolve(strict=True)
        if (
            not (p.is_relative_to(root) or p.is_relative_to(output))
            or ref != "file_" + digest(str(p))[:16]
        ):
            raise ToolError(
                "invalid_checkpoint", "Continuation file reference escaped allowed roots."
            )
        if digest(p.read_bytes()) != s["file_hashes"].get(ref):
            raise ToolError("invalid_checkpoint", "Referenced file changed since pause.")
        run.refs[ref] = p
    run.input_files = [Path(item[0]) for item in s["inputs"]]
    if inputs_fingerprint(run) != s["inputs"]:
        raise ToolError("invalid_checkpoint", "Source files changed since pause.")
    run.checks, run.calls, run.assumptions = s["checks"], s["calls"], s["assumptions"]
    run.operations = s["operations"]
    run.planner_state = s["planner_state"]
    run.lineage = s["lineage"]
    if not isinstance(run.operations, dict) or any(
        not isinstance(v, int) or not 0 <= v < len(run.checks) for v in run.operations.values()
    ):
        raise ToolError("invalid_checkpoint", "Invalid pending verification references.")
    run.cache, run.uncertain = {}, set()
    run.resume_count = s["resume_count"] + 1
    run.continuation_messages = s["messages"]
    # One-use metadata prevents replaying an older budget snapshot after a later
    # window completed or failed. A new checkpoint is issued only on another pause.
    mac_path.unlink()
    run.event("resume", resume_count=run.resume_count, elapsed_before=run.elapsed_before)
    return run


def carry_budget(run, prior_path):
    """Explicit developer correction lineage, not a replay of stale process state."""
    import math

    prior = json.loads(prior_path.read_text())
    context = json.loads((prior_path.parent / "run_context.json").read_text())
    if context != run_context(run):
        raise ToolError(
            "invalid_continuation",
            "Prior input hashes, provider configuration or identical limits are unverified or changed.",
        )
    if prior.get("lineage"):
        raise ToolError("invalid_continuation", "Only one post-terminal budget carry is allowed.")
    cost = decimal(prior["total_cost_inr"])
    elapsed, steps = prior["total_seconds"], prior["steps"]
    if (
        not isinstance(elapsed, (float, int))
        or not math.isfinite(elapsed)
        or not 0 <= elapsed < run.limits.run_timeout
        or not isinstance(steps, int)
        or not 0 <= steps < run.limits.max_steps
        or cost > run.limits.max_cost
        or not isinstance(prior["calls"], list)
    ):
        raise ToolError(
            "invalid_continuation", "Prior report exhausts or violates cumulative budgets."
        )
    run.elapsed_before, run.cost, run.steps = elapsed, cost, steps
    run.calls = prior["calls"]
    run.lineage = {
        "kind": "budget_carried_correction",
        "source_report": str(prior_path.resolve()),
        "source_sha256": digest(prior_path.read_bytes()),
        "prior_cost_inr": str(cost),
        "prior_seconds": elapsed,
        "prior_steps": steps,
    }
    run.assumptions.append(
        "Post-terminal correction, not a process-state resume: prior active time, cost and steps carried from report SHA256 "
        + digest(prior_path.read_bytes())
        + ". Earlier selected output remains preserved in its original folder."
    )
    run.event(
        "budget_carried",
        prior_report=str(prior_path.resolve()),
        prior_sha256=digest(prior_path.read_bytes()),
        prior_seconds=elapsed,
        prior_steps=steps,
        prior_cost_inr=str(cost),
    )


def run_context(run):
    return {
        "config": config_fingerprint(),
        "inputs": inputs_fingerprint(run),
        "limits": {
            "max_cost": str(run.limits.max_cost),
            "max_steps": run.limits.max_steps,
            "call_timeout": run.limits.call_timeout,
            "run_timeout": run.limits.run_timeout,
        },
    }
