"""Async tool executor: resolves dynamic arguments, enforces timeouts, concurrency & traces."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Any, Callable, Optional

from bodhan_agent.budget import BudgetExceeded, BudgetTracker
from bodhan_agent.state import ArtifactInfo, PlanStep, StepStatus, TaskState
from bodhan_agent.trace import TraceWriter
from bodhan_mcp.config import settings
from bodhan_mcp.core.errors import BodhanError

logger = logging.getLogger(__name__)

# Concurrency semaphore
_SEMAPHORE = asyncio.Semaphore(settings.max_concurrent_api_calls)


def _resolve_arg_value(val: Any, state: TaskState) -> Any:
    """Resolve ${step_id.field} variable references from state.

    If the referenced step failed or has no result, the placeholder resolves to
    an empty string (for string substitution) or None (for a whole-value ref)
    rather than the raw template text.  Returning the literal template string
    would cause downstream tools to receive garbage input.
    """
    if isinstance(val, str):
        # Match pattern ${step_id.key}
        pattern = re.compile(r"\$\{([a-zA-Z0-9_\-]+)\.([a-zA-Z0-9_]+)\}")

        # If the whole string is exactly ${s.field}, return the actual typed value (e.g. list, dict)
        full_match = pattern.fullmatch(val)
        if full_match:
            step_id, field = full_match.groups()
            step = state.get_step(step_id)
            if step and step.result:
                resolved = step.result.get(field)
                if resolved is not None:
                    return resolved
            # Step failed or field missing — return None so callers can skip gracefully
            logger.warning(
                "Reference ${%s.%s} could not be resolved (step failed or field absent); "
                "resolving to None",
                step_id, field,
            )
            return None

        # Otherwise do string substitution; unresolvable refs become empty string
        def replace_match(m: re.Match) -> str:
            step_id, field = m.groups()
            step = state.get_step(step_id)
            if step and step.result:
                v = step.result.get(field)
                if v is not None:
                    return str(v)
            logger.warning(
                "Reference ${%s.%s} in string arg could not be resolved; substituting ''",
                step_id, field,
            )
            return ""

        return pattern.sub(replace_match, val)

    elif isinstance(val, dict):
        return {k: _resolve_arg_value(v, state) for k, v in val.items()}
    elif isinstance(val, list):
        return [_resolve_arg_value(item, state) for item in val]
    return val


def _resolve_args(step: PlanStep, state: TaskState) -> dict[str, Any]:
    """Resolve all args in a plan step."""
    resolved = {}
    for k, v in step.args.items():
        resolved[k] = _resolve_arg_value(v, state)
    return resolved


async def _dispatch_tool(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Call the direct MCP tool function."""
    from bodhan_mcp.tools.stt import transcribe_audio
    from bodhan_mcp.tools.translate import translate_text
    from bodhan_mcp.tools.tts import synthesize_speech
    from bodhan_mcp.tools.ocr import ocr_document
    from bodhan_mcp.tools.media import (
        inspect_media,
        detect_script,
        build_srt,
        split_audio,
        concat_audio,
    )

    tools_map: dict[str, Callable] = {
        "transcribe_audio": transcribe_audio,
        "translate_text": translate_text,
        "synthesize_speech": synthesize_speech,
        "ocr_document": ocr_document,
        "inspect_media": inspect_media,
        "detect_script": detect_script,
        "build_srt": build_srt,
        "split_audio": split_audio,
        "concat_audio": concat_audio,
    }

    if tool_name not in tools_map:
        raise BodhanError("INVALID_INPUT", f"Tool '{tool_name}' not found")

    func = tools_map[tool_name]
    if asyncio.iscoroutinefunction(func):
        res = await func(**args)
    else:
        res = func(**args)

    if hasattr(res, "model_dump"):
        return res.model_dump()
    elif isinstance(res, dict):
        return res
    else:
        return {"result": res}


def _record_artifacts_from_result(step: PlanStep, result: dict[str, Any], state: TaskState) -> None:
    """Inspect tool result and record any created artifact files into TaskState."""
    out_dir = Path(state.out_dir) / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Audio artifacts
    output_audio = result.get("output_file_path")
    if output_audio and Path(output_audio).suffix.lower() in (".wav", ".mp3", ".ogg", ".m4a"):
        state.artifacts.append(
            ArtifactInfo(
                name=Path(output_audio).name,
                path=str(output_audio),
                kind="audio",
                produced_by=step.id,
            )
        )

    # 2. SRT subtitle artifacts
    srt_path = result.get("srt_file_path")
    if srt_path and Path(srt_path).suffix.lower() == ".srt":
        state.artifacts.append(
            ArtifactInfo(
                name=Path(srt_path).name,
                path=str(srt_path),
                kind="subtitle",
                produced_by=step.id,
            )
        )

    # 3. OCR text files
    ocr_file = result.get("full_text_file_path")
    if ocr_file and Path(ocr_file).exists():
        state.artifacts.append(
            ArtifactInfo(
                name=Path(ocr_file).name,
                path=str(ocr_file),
                kind="text",
                produced_by=step.id,
            )
        )

    # 4. Translation text file
    trans_file = result.get("output_file_path")
    if trans_file and Path(trans_file).suffix.lower() in (".txt", ".md"):
        trans_p = Path(trans_file)
        try:
            if trans_p.exists() and not trans_p.resolve().is_relative_to(out_dir.resolve()):
                dest = out_dir / trans_p.name
                dest.write_text(trans_p.read_text(encoding="utf-8"), encoding="utf-8")
                trans_file = str(dest.resolve())
                result["output_file_path"] = trans_file
        except Exception:
            pass
        state.artifacts.append(
            ArtifactInfo(
                name=Path(trans_file).name,
                path=str(trans_file),
                kind="markdown" if str(trans_file).endswith(".md") else "text",
                produced_by=step.id,
            )
        )
    elif result.get("translated_text") and not trans_file:
        # Save translated text as an artifact if not already written to file
        art_path = out_dir / f"translated_{step.id}.md"
        art_path.write_text(result["translated_text"], encoding="utf-8")
        result["output_file_path"] = str(art_path)
        state.artifacts.append(
            ArtifactInfo(
                name=art_path.name,
                path=str(art_path),
                kind="markdown",
                produced_by=step.id,
            )
        )


async def execute_step(
    step: PlanStep,
    state: TaskState,
    budget: BudgetTracker,
    trace: TraceWriter,
) -> dict[str, Any]:
    """Execute a single PlanStep with concurrency limiting, timeouts, and tracing."""
    step.status = StepStatus.RUNNING
    step.attempts += 1
    start_time = time.time()

    resolved_args = _resolve_args(step, state)

    # Ensure all output artifacts are written strictly into the run's out/artifacts directory
    artifacts_dir = Path(state.out_dir) / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    def _ensure_in_artifacts(path_str: Any, default_name: str) -> str:
        if not path_str or not isinstance(path_str, str):
            return str(artifacts_dir / default_name)
        try:
            p = Path(path_str).resolve()
            if p.is_relative_to(artifacts_dir.resolve()):
                return str(p)
        except Exception:
            pass
        return str(artifacts_dir / Path(path_str).name)

    if step.tool == "synthesize_speech":
        resolved_args["output_path"] = _ensure_in_artifacts(
            resolved_args.get("output_path"), f"speech_{step.id}.wav"
        )
    elif step.tool == "build_srt":
        resolved_args["output_path"] = _ensure_in_artifacts(
            resolved_args.get("output_path"), f"subtitles_{step.id}.srt"
        )
    elif step.tool == "ocr_document":
        resolved_args["output_dir"] = _ensure_in_artifacts(
            resolved_args.get("output_dir"), f"ocr_{step.id}"
        )

    step.resolved_args = resolved_args
    trace.record_step_start(step.id, step.tool, resolved_args)
    logger.info("Executing step %s (%s)...", step.id, step.tool)

    # Guard: fail fast if a critical resolved arg is None (means its source step failed)
    _CRITICAL_ARGS: dict[str, str] = {
        "translate_text": "text",
        "synthesize_speech": "text",
        "transcribe_audio": "file_path",
        "ocr_document": "file_path",
    }
    critical_key = _CRITICAL_ARGS.get(step.tool)
    if critical_key and resolved_args.get(critical_key) is None:
        err_msg = (
            f"Step {step.id} ({step.tool}): required arg '{critical_key}' resolved to None — "
            "its source step likely failed. Skipping to avoid passing garbage to the API."
        )
        logger.error(err_msg)
        step.status = StepStatus.FAILED
        step.error = err_msg
        step.latency_ms = 0.0
        trace.record_step_failed(step.id, step.tool, resolved_args, err_msg, 0.0, step.attempts)
        return

    async with _SEMAPHORE:
        try:
            # Enforce per-call timeout
            timeout_s = settings.api_timeout_seconds
            result = await asyncio.wait_for(
                _dispatch_tool(step.tool, resolved_args),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            latency_ms = (time.time() - start_time) * 1000
            err_msg = f"Step {step.id} timed out after {timeout_s}s"
            step.status = StepStatus.FAILED
            step.error = err_msg
            step.latency_ms = latency_ms
            trace.record_step_failed(step.id, step.tool, resolved_args, err_msg, latency_ms, step.attempts)
            logger.error(err_msg)
            return {"ok": False, "error": {"code": "TIMEOUT", "message": err_msg}}
        except Exception as exc:
            latency_ms = (time.time() - start_time) * 1000
            err_msg = str(exc)
            step.status = StepStatus.FAILED
            step.error = err_msg
            step.latency_ms = latency_ms
            trace.record_step_failed(step.id, step.tool, resolved_args, err_msg, latency_ms, step.attempts)
            logger.error("Step %s failed: %s", step.id, err_msg)
            return {"ok": False, "error": {"code": "EXECUTION_ERROR", "message": err_msg}}

    latency_ms = (time.time() - start_time) * 1000
    step.latency_ms = latency_ms

    # Check if tool returned error
    if isinstance(result, dict) and result.get("ok") is False:
        err_dict = result.get("error", {})
        err_msg = err_dict.get("message", "Tool returned error")
        step.status = StepStatus.FAILED
        step.error = err_msg
        trace.record_step_failed(step.id, step.tool, resolved_args, err_msg, latency_ms, step.attempts)
        return result

    # Success
    cost_inr = float(result.get("cost_inr", 0.0) or result.get("total_cost_inr", 0.0))
    step.cost_inr = cost_inr
    step.result = result
    step.status = StepStatus.DONE

    # Update budget & state
    budget.record_step(cost_inr)
    state.spent_inr = budget.spent_inr
    state.steps_used = budget.steps_used
    state.elapsed_s = budget.elapsed_s

    _record_artifacts_from_result(step, result, state)
    trace.record_step_complete(
        step.id,
        step.tool,
        resolved_args,
        result,
        latency_ms,
        cost_inr,
        cached=bool(result.get("cached", False)),
    )
    logger.info("Step %s completed in %.1fms (cost: ₹%.4f)", step.id, latency_ms, cost_inr)
    return result


async def execute_parallel(
    steps: list[PlanStep],
    state: TaskState,
    budget: BudgetTracker,
    trace: TraceWriter,
) -> list[dict[str, Any]]:
    """Execute multiple independent steps concurrently."""
    logger.info("Executing %d steps in parallel: %s", len(steps), [s.id for s in steps])
    # Pre-check budget for steps
    budget.check()
    tasks = [execute_step(s, state, budget, trace) for s in steps]
    results = await asyncio.gather(*tasks, return_exceptions=False)
    return results
