
from __future__ import annotations

import asyncio
import builtins
import inspect
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from bodhan_tool_agent.audio import (
    AudioProcessingError,
    audio_duration_seconds,
    chunk_count,
)
from bodhan_tool_agent.bodhan_client import DEFAULT_TRANSLATION_MAX_OUTPUT_TOKENS
from bodhan_tool_agent.config import Settings
from bodhan_tool_agent.pdf import PDFProcessingError, pdf_page_count
from bodhan_tool_agent.text import chunk_count as text_chunk_count
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from .budget import CostEstimate, UsageTracker
from .llm import LLMClient

RECIPE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAX_TIME_SECONDS = 300.0


class TaskTimeLimitExceeded(TimeoutError):
    """Raised when the task wall-clock deadline is exhausted."""


async def _wait_with_deadline(
    awaitable: Any,
    remaining_seconds: float,
    operation: str,
    time_limit: float,
) -> Any:
    try:
        return await asyncio.wait_for(awaitable, timeout=remaining_seconds)
    except asyncio.TimeoutError as exc:
        raise TaskTimeLimitExceeded(
            f"Task exceeded the {time_limit:g} second wall-clock limit "
            f"during {operation}"
        ) from exc


def _contains_time_limit(exc: BaseException) -> bool:
    if isinstance(exc, TaskTimeLimitExceeded):
        return True
    if isinstance(exc, builtins.BaseExceptionGroup):
        return any(
            _contains_time_limit(child)
            for child in exc.exceptions
        )
    return False

SYSTEM_PROMPT = """
You are a task-planning agent that can use Bodhan Indic AI tools.

Available tools:
- indic_transcribe: transcribe audio using Bodhan.
- indic_translate: translate text using Bodhan.
- indic_speak: synthesize speech using Bodhan.
- indic_ocr: extract text from page images using Bodhan.

Rules:
1. Use Bodhan tools for ALL transcription, OCR, Indic translation, and speech synthesis.
2. Never perform transcription, OCR, translation into an Indic language, or speech
   synthesis yourself.
3. Never claim to have processed a file unless a tool returned its result.
4. Treat file paths as references. Never request or include file bytes or base64
   data in your messages.
5. You may reason, plan, organize results, and write English summaries yourself.
6. Use only tools relevant to the user's task.
7. If the task cannot be completed with the available tools or inputs, explain
   the limitation honestly.
8. When finished, provide a concise, factual report in English.
"""


def _tool_definitions(mcp_tools: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or tool.name,
                "parameters": tool.input_schema,
            },
        }
        for tool in mcp_tools
    ]


def _content_to_text(result: Any) -> str:
    parts: list[str] = []

    for item in getattr(result, "content", []) or []:
        text_value = getattr(item, "text", None)
        if isinstance(text_value, str):
            parts.append(text_value)

    if getattr(result, "isError", False):
        return "TOOL_ERROR: " + "\n".join(parts)

    return "\n".join(parts)


def _safe_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Keep useful tool metadata without logging potentially sensitive text."""
    safe: dict[str, Any] = {}

    for key, value in arguments.items():
        if key in {"text", "input", "translation", "content"}:
            if isinstance(value, str):
                safe[f"{key}_characters"] = len(value)
            else:
                safe[f"{key}_provided"] = value is not None
        elif isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
        else:
            safe[key] = f"<{type(value).__name__}>"

    return safe


def _structured_content(result: Any) -> dict[str, Any] | None:
    value = getattr(result, "structured_content", None)
    if value is None:
        value = getattr(result, "structuredContent", None)
    return value if isinstance(value, dict) else None


def _is_incomplete_translation(
    tool_name: str,
    structured: dict[str, Any] | None,
) -> bool:
    if tool_name != "indic_translate" or structured is None:
        return False
    status = structured.get("status")
    return (
        status in {"failed", "truncated"}
        or structured.get("partial") is True
        or structured.get("truncated") is True
    )


def _is_incomplete_ocr(
    tool_name: str,
    structured: dict[str, Any] | None,
) -> bool:
    if tool_name != "indic_ocr" or structured is None:
        return False
    return (
        structured.get("status") == "failed"
        or structured.get("partial") is True
    )


def _merge_usage(target: dict[str, Any], usage: dict[str, Any]) -> None:
    for key, value in usage.items():
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            current = target.get(key)
            if isinstance(current, (int, float)) and not isinstance(current, bool):
                target[key] = current + value
            elif key not in target:
                target[key] = value


def _audio_duration(path: Path) -> float | None:
    try:
        return audio_duration_seconds(path)
    except (AudioProcessingError, OSError, RuntimeError):
        return None
    except Exception:
        return None


def _planned_tool_steps(tool_name: str, arguments: dict[str, Any]) -> int:
    if tool_name == "indic_ocr":
        file_value = arguments.get("image_path")
        if not isinstance(file_value, str) or not file_value.strip():
            return 1
        path = Path(file_value).expanduser()
        if not path.is_absolute():
            path = (RECIPE_ROOT / path).resolve()
        if path.suffix.lower() == ".pdf":
            try:
                return pdf_page_count(path)
            except (PDFProcessingError, OSError, RuntimeError):
                return 1
        return 1
    if tool_name == "indic_translate":
        text = arguments.get("text")
        if not isinstance(text, str):
            return 1
        try:
            return text_chunk_count(text)
        except Exception:
            return 1
    if tool_name != "indic_transcribe":
        return 1
    file_value = arguments.get("file_path")
    if not isinstance(file_value, str) or not file_value.strip():
        return 1
    path = Path(file_value).expanduser()
    if not path.is_absolute():
        path = (RECIPE_ROOT / path).resolve()
    duration = _audio_duration(path)
    if duration is None:
        return 1
    try:
        return chunk_count(duration)
    except Exception:
        return 1


def _estimate_paid_tool_cost(
    tracker: UsageTracker,
    tool_name: str,
    arguments: dict[str, Any],
) -> CostEstimate:
    if tool_name == "indic_transcribe":
        file_value = arguments.get("file_path")
        if not isinstance(file_value, str) or not file_value.strip():
            return CostEstimate(
                None,
                "Transcription cost could not be estimated because file_path is missing",
            )
        path = Path(file_value).expanduser()
        if not path.is_absolute():
            path = (RECIPE_ROOT / path).resolve()
        if path.suffix.lower() not in {".wav", ".flac", ".ogg", ".mp3"}:
            return CostEstimate(
                None,
                "Transcription cost could not be estimated because file format is unsupported",
            )
        if not path.is_file():
            return CostEstimate(
                None,
                "Transcription cost could not be estimated because the input file is missing",
            )
        duration = _audio_duration(path)
        if duration is None:
            return CostEstimate(
                None,
                "Transcription cost could not be estimated because duration is unknown",
            )
        duration_decimal = tracker._non_negative_decimal(
            duration,
            "duration_seconds",
        )
        cost = (
            duration_decimal
            * tracker.transcribe_rate_per_minute
            / Decimal("60")
        )
        return CostEstimate(cost)

    if tool_name == "indic_translate":
        text = arguments.get("text")
        target_language = arguments.get("target_language")
        if not isinstance(text, str) or not text.strip():
            return CostEstimate(
                None,
                "Translation cost could not be estimated because text is missing",
            )
        if not isinstance(target_language, str) or not target_language.strip():
            return CostEstimate(
                None,
                "Translation cost could not be estimated because target_language is missing",
            )

        max_tokens = arguments.get("max_output_tokens")
        if max_tokens is None:
            max_tokens = DEFAULT_TRANSLATION_MAX_OUTPUT_TOKENS
        if isinstance(max_tokens, bool):
            return CostEstimate(
                None,
                "Translation cost could not be estimated because max_output_tokens is invalid",
            )
        try:
            token_count = int(max_tokens)
        except (TypeError, ValueError):
            return CostEstimate(
                None,
                "Translation cost could not be estimated because max_output_tokens is invalid",
            )
        if (
            token_count <= 0
            or str(token_count) != str(max_tokens).strip()
        ):
            return CostEstimate(
                None,
                "Translation cost could not be estimated because max_output_tokens is invalid",
            )
        try:
            chunk_total = text_chunk_count(text)
        except Exception:
            return CostEstimate(
                None,
                "Translation cost could not be estimated because text could not be chunked safely",
            )
        cost = (
            Decimal(token_count)
            * tracker.translate_rate_per_10k_tokens
            / Decimal("10000")
            * Decimal(chunk_total)
        )
        return CostEstimate(cost)

    if tool_name == "indic_speak":
        text = arguments.get("text")
        if not isinstance(text, str):
            return CostEstimate(
                None,
                "Speech cost could not be estimated because text is missing",
            )
        if not text.strip():
            return CostEstimate(
                None,
                "Speech cost could not be estimated because text is empty",
            )
        if not isinstance(arguments.get("voice"), str) or not arguments["voice"].strip():
            return CostEstimate(
                None,
                "Speech cost could not be estimated because voice is missing",
            )
        if not isinstance(arguments.get("language"), str) or not arguments["language"].strip():
            return CostEstimate(
                None,
                "Speech cost could not be estimated because language is missing",
            )
        output_path = arguments.get("output_path")
        if not isinstance(output_path, str) or not output_path.strip():
            return CostEstimate(
                None,
                "Speech cost could not be estimated because output_path is missing",
            )
        cost = (
            Decimal(len(text))
            * tracker.speak_rate_per_10k_characters
            / Decimal("10000")
        )
        return CostEstimate(cost)

    if tool_name == "indic_ocr":
        file_value = arguments.get("image_path")
        if not isinstance(file_value, str) or not file_value.strip():
            return CostEstimate(
                None,
                "OCR cost could not be estimated because image_path is missing",
            )
        path = Path(file_value).expanduser()
        if not path.is_absolute():
            path = (RECIPE_ROOT / path).resolve()
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            if path.suffix.lower() != ".pdf":
                return CostEstimate(
                    None,
                    "OCR cost could not be estimated because file format is unsupported",
                )
            try:
                page_count = pdf_page_count(path)
            except (PDFProcessingError, OSError, RuntimeError) as exc:
                return CostEstimate(
                    None,
                    f"OCR cost could not be estimated because PDF is invalid: {exc}",
                )
            cost = Decimal(page_count) * tracker.ocr_rate_per_page
            return CostEstimate(cost)
        if not path.is_file():
            return CostEstimate(
                None,
                "OCR cost could not be estimated because the input file is missing",
            )
        cost = Decimal("1") * tracker.ocr_rate_per_page
        return CostEstimate(cost)

    return CostEstimate(
        Decimal("0"),
        "Tool is not a paid Bodhan capability",
    )


class TaskAgent:
    def __init__(
        self,
        settings: Settings,
        *,
        llm: LLMClient | None = None,
        max_steps: int = 30,
        max_cost_inr: float | Decimal = 20.0,
        max_time_seconds: float = DEFAULT_MAX_TIME_SECONDS,
        usage_tracker: UsageTracker | None = None,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1")

        try:
            configured_budget = Decimal(str(max_cost_inr))
        except (InvalidOperation, TypeError, ValueError):
            raise ValueError(
                "max_cost_inr must be a non-negative number"
            ) from None
        if not configured_budget.is_finite() or configured_budget <= 0:
            raise ValueError("max_cost_inr must be a finite positive number")
        try:
            configured_time = float(max_time_seconds)
        except (TypeError, ValueError):
            raise ValueError(
                "max_time_seconds must be a finite positive number"
            ) from None
        if not math.isfinite(configured_time) or configured_time <= 0:
            raise ValueError(
                "max_time_seconds must be a finite positive number"
            )

        self.settings = settings
        self.llm = llm or LLMClient(settings)
        self.max_steps = max_steps
        self.max_cost_inr = configured_budget
        self.max_time_seconds = configured_time
        self.usage_tracker = usage_tracker or UsageTracker()

    async def run(
        self,
        task: str,
        files: list[Path],
        output_dir: Path,
        *,
        max_cost_inr: float | Decimal | None = None,
        max_time_seconds: float | None = None,
    ) -> str:
        output_dir.mkdir(parents=True, exist_ok=True)
        artifacts_dir = output_dir / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        trace_path = output_dir / "trace.jsonl"
        trace_path.write_text("", encoding="utf-8")
        report_path = output_dir / "report.json"
        started_at = time.perf_counter()
        llm_usage: dict[str, Any] = {}
        retry_attempt_count = 0
        retry_succeeded_count = 0
        retry_exhausted_count = 0
        uncertain_paid_count = 0
        replan_count = 0
        replan_limit = 3
        self.usage_tracker.reset()

        time_limit = float(
            self.max_time_seconds
            if max_time_seconds is None
            else max_time_seconds
        )
        if not math.isfinite(time_limit) or time_limit <= 0:
            failure = ValueError(
                "max_time_seconds must be a finite positive number"
            )
            elapsed = time.perf_counter() - started_at
            report_path.write_text(
                json.dumps(
                    {
                        "status": "failed",
                        "failure_type": "invalid_time_budget",
                        "configured_time_limit_seconds": str(time_limit),
                        "elapsed_seconds": round(elapsed, 4),
                        "remaining_time_seconds": 0.0,
                        "tool_call_count": 0,
                        "failure_reason": str(failure),
                        "llm_usage": {},
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            raise failure

        deadline = started_at + time_limit

        def remaining_seconds() -> float:
            return max(0.0, deadline - time.perf_counter())

        def require_time() -> float:
            remaining = remaining_seconds()
            if remaining <= 0:
                raise TaskTimeLimitExceeded(
                    f"Task exceeded the {time_limit:g} second wall-clock limit"
                )
            return remaining

        def trace(event: str, **details: Any) -> None:
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": event,
                **details,
            }
            with trace_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(record, ensure_ascii=False, default=str) + "\n"
                )

        def write_report(
            status: str,
            *,
            tool_calls_used: int,
            failure_reason: str | None = None,
            failure_type: str = "none",
        ) -> None:
            report = self.usage_tracker.report()
            report.update(
                {
                    "status": status,
                    "retry_attempts": retry_attempt_count,
                    "retry_succeeded": retry_succeeded_count,
                    "retry_exhausted": retry_exhausted_count,
                    "uncertain_paid_outcomes": uncertain_paid_count,
                    "replanning_cycles": replan_count,
                    "replanning_limit": replan_limit,
                    "failure_type": failure_type,
                    "tool_call_count": tool_calls_used,
                    "configured_budget_inr": str(budget),
                    "configured_time_limit_seconds": str(time_limit),
                    "elapsed_seconds": round(
                        time.perf_counter() - started_at,
                        4,
                    ),
                    "remaining_time_seconds": round(
                        remaining_seconds(),
                        4,
                    ),
                    "llm_usage": llm_usage,
                }
            )
            if failure_reason:
                report["failure_reason"] = failure_reason
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        tool_calls_used = 0
        preflight_reservations: dict[int, Decimal] = {}
        unresolved_tool_failure = False
        permanent_tool_failure = False
        completed_tool_signatures: set[str] = set()
        budget = self.max_cost_inr
        if max_cost_inr is not None:
            try:
                budget = Decimal(str(max_cost_inr))
            except (InvalidOperation, TypeError, ValueError):
                failure = ValueError(
                    "max_cost_inr must be a finite positive number"
                )
                trace(
                    "task_failed",
                    error_type="ValueError",
                    error=str(failure),
                    tool_calls_used=0,
                )
                write_report(
                    "failed",
                    tool_calls_used=0,
                    failure_reason=str(failure),
                )
                raise failure from None
        if not budget.is_finite() or budget <= 0:
            failure = ValueError(
                "max_cost_inr must be a finite positive number"
            )
            trace(
                "task_failed",
                error_type="ValueError",
                error=str(failure),
                tool_calls_used=0,
            )
            write_report(
                "failed",
                tool_calls_used=0,
                failure_reason=str(failure),
            )
            raise failure

        if not task.strip():
            exc = ValueError("task must not be empty")
            trace(
                "task_failed",
                error_type=type(exc).__name__,
                error=str(exc),
                tool_calls_used=0,
            )
            write_report(
                "failed",
                tool_calls_used=0,
                failure_reason=str(exc),
            )
            raise exc

        valid_files: list[Path] = []
        try:
            for path in files:
                resolved = path.expanduser().resolve()
                if not resolved.is_file():
                    raise FileNotFoundError(
                        f"Input file does not exist: {path}"
                    )
                valid_files.append(resolved)
        except Exception as exc:
            for reserved in list(preflight_reservations.values()):
                if reserved <= self.usage_tracker.reserved_total_inr:
                    self.usage_tracker.release(reserved)
            trace(
                "task_failed",
                error_type=type(exc).__name__,
                error=str(exc)[:500],
                tool_calls_used=0,
            )
            write_report(
                "failed",
                tool_calls_used=0,
                failure_reason=str(exc),
            )
            raise

        file_context = [
            {
                "name": path.name,
                "path": str(path),
                "extension": path.suffix.lower(),
                "size_bytes": path.stat().st_size,
            }
            for path in valid_files
        ]

        trace(
            "task_started",
            task_characters=len(task),
            input_file_count=len(valid_files),
            max_steps=self.max_steps,
            configured_budget_inr=str(budget),
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Task:\n{task}\n\n"
                    "Available input files (paths are references; file contents "
                    "are not included):\n"
                    f"{json.dumps(file_context, ensure_ascii=False, indent=2)}\n\n"
                    f"Output directory: {output_dir.resolve()}\n"
                    f"Artifact directory: {artifacts_dir.resolve()}\n\n"
                    "Plan the task and call the appropriate Bodhan tools. "
                    "When all possible work is complete, provide the final "
                    "English report."
                ),
            },
        ]

        server_params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "bodhan_mcp"],
            cwd=str(RECIPE_ROOT),
            env={
                **os.environ,
                "BODHAN_TASK_DEADLINE_EPOCH": str(
                    time.time() + remaining_seconds()
                ),
            },
        )

        try:
            async with stdio_client(server_params) as (
                read_stream,
                write_stream,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    initialize_timeout = require_time()
                    await _wait_with_deadline(
                        session.initialize(),
                        initialize_timeout,
                        "MCP initialization",
                        time_limit,
                    )
                    discovery_timeout = require_time()
                    discovered = await _wait_with_deadline(
                        session.list_tools(),
                        discovery_timeout,
                        "MCP tool discovery",
                        time_limit,
                    )
                    tool_definitions = _tool_definitions(discovered.tools)
                    available_names = {
                        item.name for item in discovered.tools
                    }

                    trace(
                        "mcp_connected",
                        discovered_tools=sorted(available_names),
                    )

                    if not tool_definitions:
                        raise RuntimeError("The MCP server exposed no tools")

                    while True:
                        require_time()
                        llm_started = time.perf_counter()
                        try:
                            llm_remaining = require_time()
                            chat_accepts_timeout = (
                                "timeout_seconds"
                                in inspect.signature(self.llm.chat).parameters
                            )
                            if chat_accepts_timeout:
                                llm_call = asyncio.to_thread(
                                    self.llm.chat,
                                    messages,
                                    tool_definitions,
                                    timeout_seconds=llm_remaining,
                                )
                            else:
                                llm_call = asyncio.to_thread(
                                    self.llm.chat,
                                    messages,
                                    tool_definitions,
                                )
                            message = await _wait_with_deadline(
                                llm_call,
                                llm_remaining,
                                "planning LLM request",
                                time_limit,
                            )
                        except TaskTimeLimitExceeded:
                            raise
                        llm_duration = time.perf_counter() - llm_started
                        llm_last_usage = getattr(
                            self.llm,
                            "last_usage",
                            None,
                        )
                        if isinstance(llm_last_usage, dict):
                            _merge_usage(llm_usage, llm_last_usage)

                        trace(
                            "llm_response",
                            duration_seconds=round(llm_duration, 4),
                        )
                        if replan_count > 0:
                            trace(
                                "replan_completed",
                                cycle=replan_count,
                                tool_calls_requested=len(
                                    message.get("tool_calls") or []
                                ),
                            )

                        messages.append(message)
                        requested_calls = message.get("tool_calls") or []

                        if not requested_calls:
                            final_text = message.get("content")
                            if (
                                not isinstance(final_text, str)
                                or not final_text.strip()
                            ):
                                raise RuntimeError(
                                    "The planning LLM returned neither a final "
                                    "answer nor tool calls"
                                )

                            if unresolved_tool_failure:
                                trace(
                                    "recovery_failed",
                                    replan_cycles=replan_count,
                                    permanent=permanent_tool_failure,
                                )
                                raise RuntimeError(
                                    "Task is incomplete because a tool failure "
                                    "could not be recovered"
                                )

                            trace(
                                "task_completed",
                                tool_calls_used=tool_calls_used,
                            )
                            write_report(
                                "success",
                                tool_calls_used=tool_calls_used,
                            )
                            return final_text.strip()

                        planned_steps = 0
                        for tool_call in requested_calls:
                            if not isinstance(tool_call, dict):
                                planned_steps += 1
                                continue
                            function = tool_call.get("function", {})
                            tool_name = (
                                function.get("name")
                                if isinstance(function, dict)
                                else None
                            )
                            raw_arguments = (
                                function.get("arguments", "{}")
                                if isinstance(function, dict)
                                else "{}"
                            )
                            try:
                                arguments = (
                                    json.loads(raw_arguments)
                                    if isinstance(raw_arguments, str)
                                    else raw_arguments
                                )
                            except json.JSONDecodeError:
                                arguments = {}
                            if not isinstance(arguments, dict):
                                arguments = {}
                            planned_steps += _planned_tool_steps(
                                tool_name if isinstance(tool_name, str) else "",
                                arguments,
                            )

                        if (
                            tool_calls_used + planned_steps
                            > self.max_steps
                        ):
                            trace(
                                "step_limit_reached",
                                tool_calls_used=tool_calls_used,
                                requested_call_count=len(requested_calls),
                            )
                            raise RuntimeError(
                                f"Agent batch of {len(requested_calls)} tool "
                                f"calls would exceed its maximum of "
                                f"{self.max_steps} tool calls"
                            )

                        try:
                            for call_index, tool_call in enumerate(requested_calls):
                                if not isinstance(tool_call, dict):
                                    raise RuntimeError(
                                        "LLM returned an invalid tool call"
                                    )

                                function = tool_call.get("function", {})
                                tool_name = (
                                    function.get("name")
                                    if isinstance(function, dict)
                                    else None
                                )
                                raw_arguments = (
                                    function.get("arguments", "{}")
                                    if isinstance(function, dict)
                                    else "{}"
                                )

                                if not isinstance(tool_name, str):
                                    raise RuntimeError(
                                        "LLM returned a tool call without a name"
                                    )

                                try:
                                    arguments = (
                                        json.loads(raw_arguments)
                                        if isinstance(raw_arguments, str)
                                        else raw_arguments
                                    )
                                except json.JSONDecodeError as exc:
                                    raise RuntimeError(
                                        f"LLM returned invalid arguments for "
                                        f"{tool_name}"
                                    ) from exc
                                if not isinstance(arguments, dict):
                                    raise RuntimeError(
                                        f"LLM arguments for {tool_name} "
                                        "must be an object"
                                    )

                                signature_arguments = _safe_arguments(arguments)
                                signature = (
                                    f"{tool_name}:"
                                    f"{json.dumps(signature_arguments, sort_keys=True)}"
                                )
                                if (
                                    signature in completed_tool_signatures
                                    and replan_count > 0
                                ):
                                    unresolved_tool_failure = True
                                    trace(
                                        "recovery_failed",
                                        reason="successful_tool_result_would_be_repeated",
                                        replan_cycles=replan_count,
                                    )
                                    raise RuntimeError(
                                        "Replanning stopped: successful paid tool "
                                        "result would be repeated"
                                    )

                                if tool_name not in {
                                    "indic_transcribe",
                                    "indic_translate",
                                    "indic_speak",
                                    "indic_ocr",
                                }:
                                    continue

                                estimate = _estimate_paid_tool_cost(
                                    self.usage_tracker,
                                    tool_name,
                                    arguments,
                                )
                                if not estimate.known:
                                    reason = (
                                        estimate.reason
                                        or (
                                            f"{tool_name} cost could not be "
                                            "safely estimated"
                                        )
                                    )
                                    service_map = {
                                        "indic_transcribe": "transcribe",
                                        "indic_translate": "translate",
                                        "indic_speak": "speak",
                                        "indic_ocr": "ocr",
                                    }
                                    self.usage_tracker.mark_unknown(
                                        reason,
                                        service=service_map[tool_name],
                                    )
                                    raise RuntimeError(
                                        f"Tool call blocked: {reason}"
                                    )

                                assert estimate.amount_inr is not None
                                if not self.usage_tracker.can_spend(
                                    estimate.amount_inr,
                                    budget,
                                ):
                                    raise RuntimeError(
                                        f"Tool call blocked: {tool_name} "
                                        f"would exceed the {budget} INR "
                                        "Bodhan budget"
                                    )

                                reserved = self.usage_tracker.reserve(
                                    estimate.amount_inr
                                )
                                preflight_reservations[call_index] = reserved
                        except Exception as exc:
                            for reserved in preflight_reservations.values():
                                self.usage_tracker.release(reserved)
                            trace(
                                "batch_preflight_blocked",
                                requested_call_count=len(requested_calls),
                                reason=str(exc),
                            )
                            raise

                        for call_index, tool_call in enumerate(requested_calls):
                            if tool_calls_used >= self.max_steps:
                                trace(
                                    "step_limit_reached",
                                    tool_calls_used=tool_calls_used,
                                )
                                raise RuntimeError(
                                    f"Agent reached its maximum of "
                                    f"{self.max_steps} tool calls"
                                )

                            function = tool_call.get("function", {})
                            tool_name = function.get("name")
                            raw_arguments = function.get("arguments", "{}")
                            call_id = tool_call.get("id", "")

                            if not isinstance(tool_name, str):
                                raise RuntimeError(
                                    "LLM returned a tool call without a name"
                                )

                            try:
                                arguments = (
                                    json.loads(raw_arguments)
                                    if isinstance(raw_arguments, str)
                                    else raw_arguments
                                )
                            except json.JSONDecodeError as exc:
                                raise RuntimeError(
                                    f"LLM returned invalid arguments for "
                                    f"{tool_name}"
                                ) from exc

                            if not isinstance(arguments, dict):
                                raise RuntimeError(
                                    f"LLM arguments for {tool_name} "
                                    "must be an object"
                                )

                            planned_steps = _planned_tool_steps(
                                tool_name,
                                arguments,
                            )
                            if tool_calls_used + planned_steps > self.max_steps:
                                trace(
                                    "step_limit_reached",
                                    tool_calls_used=tool_calls_used,
                                    requested_steps=planned_steps,
                                )
                                raise RuntimeError(
                                    f"Agent would exceed its maximum of "
                                    f"{self.max_steps} tool calls"
                                )
                            tool_calls_used += planned_steps

                            reserved_cost = preflight_reservations.get(call_index)

                            try:
                                trace(
                                    "tool_call_started",
                                    step=tool_calls_used,
                                    tool=tool_name,
                                    arguments=_safe_arguments(arguments),
                                    reserved_cost_inr=(
                                        str(reserved_cost)
                                        if reserved_cost is not None
                                        else None
                                    ),
                                )
                            except Exception:
                                if reserved_cost is not None:
                                    self.usage_tracker.release(reserved_cost)
                                    preflight_reservations.pop(call_index, None)
                                    reserved_cost = None
                                raise

                            tool_started = time.perf_counter()
                            structured_available = False

                            if tool_name not in available_names:
                                tool_output = f"Unknown MCP tool: {tool_name}"
                                is_error = True
                            else:
                                try:
                                    mcp_timeout = require_time()
                                    result = await _wait_with_deadline(
                                        session.call_tool(
                                            tool_name,
                                            arguments=arguments,
                                        ),
                                        mcp_timeout,
                                        "MCP tool call",
                                        time_limit,
                                    )
                                    tool_output = _content_to_text(result)
                                    if "outcome is uncertain" in tool_output.lower():
                                        uncertain_paid_count += 1
                                        service_map = {
                                            "indic_transcribe": "transcribe",
                                            "indic_translate": "translate",
                                            "indic_speak": "speak",
                                            "indic_ocr": "ocr",
                                        }
                                        self.usage_tracker.mark_unknown(
                                            "Paid Bodhan request outcome is uncertain after timeout",
                                            service=service_map.get(tool_name),
                                        )
                                    structured_available = (
                                        _structured_content(result) is not None
                                    )
                                    structured = _structured_content(result)
                                    retry_attempts = (
                                        structured.get("retry_attempts", 0)
                                        if structured
                                        else 0
                                    )
                                    if isinstance(retry_attempts, int) and retry_attempts > 0:
                                        retry_attempt_count += retry_attempts
                                        trace(
                                            "retry_started",
                                            tool=tool_name,
                                            attempts=retry_attempts,
                                        )
                                        if structured.get("retry_succeeded"):
                                            retry_succeeded_count += 1
                                            trace(
                                                "retry_succeeded",
                                                tool=tool_name,
                                                attempts=retry_attempts,
                                            )
                                        if structured.get("retry_exhausted"):
                                            retry_exhausted_count += 1
                                            trace(
                                                "retry_exhausted",
                                                tool=tool_name,
                                                attempts=retry_attempts,
                                            )
                                    if structured and structured.get(
                                        "uncertain_paid_outcome"
                                    ):
                                        uncertain_paid_count += 1
                                        self.usage_tracker.mark_unknown(
                                            "Paid Bodhan request outcome is uncertain after timeout",
                                            service={
                                                "indic_transcribe": "transcribe",
                                                "indic_translate": "translate",
                                                "indic_speak": "speak",
                                                "indic_ocr": "ocr",
                                            }.get(tool_name),
                                        )
                                    is_error = bool(
                                        getattr(result, "isError", False)
                                    ) or (
                                        tool_output.startswith(
                                            "Error executing tool "
                                        )
                                        or tool_output.startswith("TOOL_ERROR:")
                                    )
                                    structured = _structured_content(result)
                                    if not is_error and _is_incomplete_translation(
                                        tool_name,
                                        structured,
                                    ):
                                        status = structured.get("status")
                                        failed_chunk = structured.get(
                                            "failed_chunk_index"
                                        )
                                        completed_chunks = structured.get(
                                            "completed_chunk_count"
                                        )
                                        total_chunks = structured.get(
                                            "chunk_count"
                                        )
                                        trace(
                                            "translation_incomplete",
                                            status=status,
                                            failed_chunk_index=failed_chunk,
                                            completed_chunk_count=completed_chunks,
                                            total_chunk_count=total_chunks,
                                            output_characters=0,
                                        )
                                        if reserved_cost is not None:
                                            self.usage_tracker.release(
                                                reserved_cost
                                            )
                                            preflight_reservations.pop(
                                                call_index,
                                                None,
                                            )
                                            reserved_cost = None
                                        raise RuntimeError(
                                            "Translation task failed: the "
                                            f"result was {status or 'partial'} "
                                            "and is not a complete translation"
                                        )
                                except TaskTimeLimitExceeded:
                                    raise
                                except Exception:
                                    if reserved_cost is not None:
                                        self.usage_tracker.release(
                                            reserved_cost
                                        )
                                        preflight_reservations.pop(
                                            call_index,
                                            None,
                                        )
                                        reserved_cost = None
                                    raise

                            structured = _structured_content(result)
                            if (
                                not is_error
                                and _is_incomplete_ocr(tool_name, structured)
                            ):
                                if reserved_cost is not None:
                                    self.usage_tracker.release(reserved_cost)
                                    preflight_reservations.pop(
                                        call_index,
                                        None,
                                    )
                                    reserved_cost = None
                                trace(
                                    "ocr_incomplete",
                                    failed_page=structured.get("failed_page"),
                                    completed_pages=structured.get("completed_pages"),
                                    total_pages=structured.get("total_pages"),
                                    page_usage_available=bool(
                                        structured.get("usage")
                                    ),
                                )
                                raise RuntimeError(
                                    "OCR task failed: PDF OCR is incomplete"
                                )

                            if reserved_cost is not None:
                                if is_error:
                                    self.usage_tracker.release(reserved_cost)
                                    preflight_reservations.pop(
                                        call_index,
                                        None,
                                    )
                                    reserved_cost = None
                                else:
                                    structured = _structured_content(result)

                                    if tool_name == "indic_transcribe":
                                        duration = (
                                            structured.get("duration_seconds")
                                            if structured
                                            else None
                                        )
                                        chunk_durations = (
                                            structured.get("chunk_durations_seconds")
                                            if structured
                                            else None
                                        )
                                        chunk_count_value = (
                                            structured.get("chunk_count")
                                            if structured
                                            else None
                                        )
                                        valid_chunks = (
                                            isinstance(chunk_durations, list)
                                            and isinstance(chunk_count_value, int)
                                            and not isinstance(chunk_count_value, bool)
                                            and chunk_count_value == len(chunk_durations)
                                            and chunk_count_value > 0
                                            and all(
                                                isinstance(item, (int, float))
                                                and not isinstance(item, bool)
                                                and math.isfinite(float(item))
                                                and float(item) > 0
                                                for item in chunk_durations
                                            )
                                        )
                                        if valid_chunks:
                                            actual = CostEstimate(Decimal("0"))
                                            for chunk_duration in chunk_durations:
                                                chunk_actual = self.usage_tracker.record_transcription(
                                                    float(chunk_duration)
                                                )
                                                if chunk_actual.amount_inr is not None:
                                                    actual.amount_inr = (
                                                        (actual.amount_inr or Decimal("0"))
                                                        + chunk_actual.amount_inr
                                                    )
                                        else:
                                            actual = CostEstimate(
                                                None,
                                                "Transcription chunk duration was missing or malformed",
                                            )
                                            self.usage_tracker.mark_unknown(
                                                actual.reason or "Unknown transcription usage",
                                                service="transcribe",
                                            )

                                    elif tool_name == "indic_translate":
                                        usage = (
                                            structured.get("usage")
                                            if structured
                                            else None
                                        )
                                        chunk_usage = (
                                            structured.get("chunk_usage")
                                            if structured
                                            else None
                                        )
                                        if isinstance(chunk_usage, list) and chunk_usage:
                                            actual = CostEstimate(Decimal("0"))
                                            for usage_item in chunk_usage:
                                                chunk_actual = self.usage_tracker.record_translation(
                                                    usage_item
                                                    if isinstance(usage_item, dict)
                                                    else {}
                                                )
                                                if chunk_actual.amount_inr is not None:
                                                    actual.amount_inr = (
                                                        (actual.amount_inr or Decimal("0"))
                                                        + chunk_actual.amount_inr
                                                    )
                                        else:
                                            actual = self.usage_tracker.record_translation(
                                                usage
                                                if isinstance(usage, dict)
                                                else {}
                                            )

                                    elif tool_name == "indic_speak":
                                        text = arguments.get("text")
                                        if isinstance(text, str):
                                            actual = self.usage_tracker.record_speech(
                                                text
                                            )
                                        else:
                                            actual = CostEstimate(
                                                None,
                                                "Speech cost could not be reconciled because text is missing",
                                            )
                                            self.usage_tracker.mark_unknown(
                                                actual.reason or "Unknown speech usage",
                                                service="speak",
                                            )

                                    elif tool_name == "indic_ocr":
                                        structured = (
                                            structured
                                            if "structured" in locals()
                                            else _structured_content(result)
                                        )
                                        if structured and structured.get("status") == "success":
                                            total_pages = structured.get("total_pages")
                                            if (
                                                isinstance(total_pages, int)
                                                and not isinstance(total_pages, bool)
                                                and total_pages > 0
                                            ):
                                                actual = self.usage_tracker.record_ocr(
                                                    total_pages
                                                )
                                                page_usage = structured.get("page_usage")
                                                if not isinstance(page_usage, list):
                                                    self.usage_tracker.mark_unknown(
                                                        "OCR page usage metadata was missing",
                                                    )
                                                else:
                                                    for usage in page_usage:
                                                        if not isinstance(usage, dict):
                                                            self.usage_tracker.mark_unknown(
                                                                "OCR page usage metadata was malformed",
                                                            )
                                                        elif not usage:
                                                            self.usage_tracker.mark_unknown(
                                                                "OCR page usage metadata was missing",
                                                            )
                                            else:
                                                actual = CostEstimate(
                                                    None,
                                                    "OCR page count was missing or malformed",
                                                )
                                                self.usage_tracker.mark_unknown(
                                                    actual.reason or "Unknown OCR usage",
                                                    service="ocr",
                                                )
                                        else:
                                            actual = CostEstimate(
                                                None,
                                                "OCR operation was incomplete",
                                            )
                                    else:
                                        actual = CostEstimate(None)

                                    if actual.known:
                                        assert actual.amount_inr is not None
                                        self.usage_tracker.reconcile(
                                            reserved_cost,
                                            actual,
                                        )
                                    else:
                                        self.usage_tracker.release(
                                            reserved_cost
                                        )
                                    preflight_reservations.pop(
                                        call_index,
                                        None,
                                    )
                                    reserved_cost = None

                            duration = time.perf_counter() - tool_started

                            trace(
                                "tool_call_finished",
                                step=tool_calls_used,
                                tool=tool_name,
                                duration_seconds=round(duration, 4),
                                is_error=is_error,
                                output_characters=len(tool_output),
                                structured_output_available=structured_available,
                            )

                            messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": call_id,
                                    "content": tool_output,
                                }
                            )

                            if not is_error:
                                completed_tool_signatures.add(signature)
                                unresolved_tool_failure = False
                                permanent_tool_failure = False

                            if is_error:
                                unresolved_tool_failure = True
                                lower_output = tool_output.lower()
                                permanent_tool_failure = (
                                    "unknown mcp tool" in lower_output
                                    or "http 400" in lower_output
                                    or "http 401" in lower_output
                                    or "http 403" in lower_output
                                    or "unsupported" in lower_output
                                    or "invalid" in lower_output
                                    or "does not exist" in lower_output
                                )
                                if permanent_tool_failure:
                                    trace(
                                        "recovery_failed",
                                        reason="permanent_tool_error",
                                        replan_cycles=replan_count,
                                    )
                                elif replan_count < replan_limit:
                                    replan_count += 1
                                    trace(
                                        "replan_started",
                                        cycle=replan_count,
                                        tool=tool_name,
                                    )
                                else:
                                    trace(
                                        "recovery_failed",
                                        reason="replanning_limit_reached",
                                        replan_cycles=replan_count,
                                    )

        except Exception as exc:
            for reserved in list(preflight_reservations.values()):
                if reserved <= self.usage_tracker.reserved_total_inr:
                    self.usage_tracker.release(reserved)
            is_time_limit = _contains_time_limit(exc)
            trace(
                "task_failed",
                error_type=type(exc).__name__,
                error=str(exc)[:500],
                tool_calls_used=tool_calls_used,
                failure_type=(
                    "time_budget_exceeded"
                    if is_time_limit
                    else "other_failure"
                ),
            )
            write_report(
                "failed",
                tool_calls_used=tool_calls_used,
                failure_reason=str(exc),
                failure_type=(
                    "time_budget_exceeded"
                    if is_time_limit
                    else "other_failure"
                ),
            )
            raise

        write_report(
            "failed",
            tool_calls_used=tool_calls_used,
            failure_reason="Agent exited without producing a final answer",
        )
        raise RuntimeError("Agent exited without producing a final answer")


def run_task(
    task: str,
    files: list[Path],
    output_dir: Path,
    *,
    max_steps: int = 30,
    max_cost_inr: float | Decimal = 20.0,
    max_time_seconds: float = DEFAULT_MAX_TIME_SECONDS,
) -> str:
    settings = Settings.from_env()
    agent = TaskAgent(
        settings,
        max_steps=max_steps,
        max_cost_inr=max_cost_inr,
        max_time_seconds=max_time_seconds,
    )
    return asyncio.run(
        agent.run(
            task,
            files,
            output_dir,
            max_cost_inr=max_cost_inr,
            max_time_seconds=max_time_seconds,
        )
    )
