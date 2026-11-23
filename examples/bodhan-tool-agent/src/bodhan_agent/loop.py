from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from bodhan_mcp.cache import (
    CostLedger,
    DiskCache,
    cost_ocr,
    cost_speak,
    cost_transcribe,
    cost_translate,
    estimate_output_tokens,
    hash_text,
)
from bodhan_mcp.client import BodhanClient
from bodhan_mcp.languages import contains_non_english_script, detect_script_language
from bodhan_mcp.models import ErrorCode, ToolError
from bodhan_mcp.tools.detect_language import detect_language
from bodhan_mcp.tools.inspect import audio_duration_seconds
from bodhan_mcp.tools.ocr import ocr_document
from bodhan_mcp.tools.speak import speak_text
from bodhan_mcp.tools.transcribe import transcribe_audio
from bodhan_mcp.tools.translate import translate_text
from bodhan_mcp.workspace import Workspace

from .tool_catalog import AGENT_TOOLS

log = logging.getLogger(__name__)


class AgentExecutionResult:
    def __init__(
        self,
        success: bool,
        steps: list[dict[str, Any]],
        final_output: Any,
        total_cost_inr: float,
        stopped_reason: str | None = None,
    ) -> None:
        self.success = success
        self.steps = steps
        self.final_output = final_output
        self.total_cost_inr = total_cost_inr
        self.stopped_reason = stopped_reason
        self.trace: list[dict[str, Any]] = []
        self.assumptions: list[str] = []
        self.verification: dict[str, Any] = {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "steps": self.steps,
            "final_output": self.final_output,
            "total_cost_inr": self.total_cost_inr,
            "stopped_reason": self.stopped_reason,
            "assumptions": self.assumptions,
            "verification": self.verification,
            "trace": self.trace,
        }


class BodhanAgentLoop:
    """Run a bounded pipeline and retain a content-safe execution trace."""

    def __init__(
        self,
        workspace_dir: str | Path | Workspace = "./workspace",
        client: BodhanClient | None = None,
        ledger: CostLedger | None = None,
        cache: DiskCache | None = None,
        *,
        max_steps: int = 30,
        max_cost_inr: float = 20.0,
        english_summarizer: Callable[[str, int | None], str] | None = None,
    ) -> None:
        if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        if not math.isfinite(max_cost_inr) or max_cost_inr < 0:
            raise ValueError("max_cost_inr must be a finite non-negative number")
        self.workspace = (
            workspace_dir if isinstance(workspace_dir, Workspace) else Workspace(workspace_dir)
        )
        self.client = client or BodhanClient()
        self.ledger = ledger or CostLedger.from_env()
        if cache is not None:
            self.cache = cache
        elif type(self.client) is BodhanClient:
            self.cache = DiskCache.from_env()
        else:
            self.cache = DiskCache(self.workspace.out_dir / ".cache")
        self.max_steps = max_steps
        self.max_cost_inr = max_cost_inr
        self.english_summarizer = english_summarizer

    def execute_pipeline(
        self,
        steps_plan: list[dict[str, Any]],
        *,
        deliverables: list[dict[str, Any]] | None = None,
        input_items: list[dict[str, Any]] | None = None,
    ) -> AgentExecutionResult:
        executed_steps: list[dict[str, Any]] = []
        context: dict[str, Any] = {}
        step_outputs: dict[str, dict[str, Any]] = {}
        step_artifacts: dict[str, list[dict[str, Any]]] = {}
        assumptions: list[str] = []
        verification_summary: dict[str, Any] = {
            "audio_checks": [],
            "translation_checks": [],
            "ocr_checks": [],
        }
        total_cost = 0.0
        invocation_count = 0
        plan = steps_plan
        trace: list[dict[str, Any]] = [
            {
                "event": "plan",
                "step_count": len(plan),
                "steps": [
                    {"step_index": index, **self._trace_arguments(step)}
                    for index, step in enumerate(plan)
                ],
            }
        ]
        stopped_reason: str | None = None

        for plan_index, planned_step in enumerate(plan):
            step_id = str(planned_step.get("id", f"legacy_{plan_index + 1}"))
            tool_name = planned_step.get("tool")
            standardized = "arguments" in planned_step
            raw_arguments = (
                planned_step.get("arguments", {})
                if standardized
                else {key: value for key, value in planned_step.items() if key != "tool"}
            )
            if not isinstance(raw_arguments, dict):
                stopped_reason = f"invalid arguments for step {step_id}"
                break

            when = planned_step.get("when")
            try:
                condition_matches = not when or self._condition_is_true(when, step_outputs)
            except Exception as exc:
                stopped_reason = f"condition failed: {type(exc).__name__}"
                error = (
                    exc.to_dict()
                    if isinstance(exc, ToolError)
                    else {"code": ErrorCode.INVALID_ARGUMENT.value, "message": str(exc)}
                )
                trace.append(
                    {
                        "event": "condition_error",
                        "step_id": step_id,
                        "result_summary": {"ok": False, "error": error},
                    }
                )
                break
            if not condition_matches:
                step_outputs[step_id] = {}
                step_artifacts[step_id] = []
                trace.append(
                    {
                        "event": "step_skipped",
                        "step_id": step_id,
                        "reason": "condition evaluated to false",
                        "condition": self._trace_arguments(when),
                    }
                )
                continue

            for_each = planned_step.get("for_each")
            if for_each:
                try:
                    source_spec = for_each["source"]
                    if source_spec == {"input": "files"}:
                        items = list(input_items or [])
                    else:
                        items = step_outputs[source_spec["step_id"]][source_spec["output"]]
                except (KeyError, TypeError) as exc:
                    stopped_reason = f"for_each source resolution failed for step {step_id}"
                    trace.append(
                        {
                            "event": "binding_error",
                            "step_id": step_id,
                            "result_summary": {
                                "ok": False,
                                "error": {
                                    "code": ErrorCode.INVALID_ARGUMENT.value,
                                    "message": "The selected iteration source was not produced.",
                                },
                            },
                        }
                    )
                    log.debug("Could not resolve batch source for step %s: %s", step_id, exc)
                    break
                if not isinstance(items, list):
                    stopped_reason = f"for_each source for step {step_id} is not an array"
                    break
            else:
                items = [None]

            collected: dict[str, list[Any]] = {}
            collected_artifacts: list[dict[str, Any]] = []
            for iteration, item in enumerate(items, start=1):
                if invocation_count >= self.max_steps:
                    stopped_reason = "maximum step count reached"
                    trace.append(
                        {
                            "event": "budget_stop",
                            "reason": stopped_reason,
                            "completed_steps": len(executed_steps),
                            "max_steps": self.max_steps,
                            "total_cost_inr": total_cost,
                        }
                    )
                    break
                try:
                    arguments = self._resolve_plan_value(
                        raw_arguments,
                        item=item,
                        step_outputs=step_outputs,
                    )
                except Exception as exc:
                    stopped_reason = f"argument binding failed: {type(exc).__name__}"
                    error = (
                        exc.to_dict()
                        if isinstance(exc, ToolError)
                        else {
                            "code": ErrorCode.INVALID_ARGUMENT.value,
                            "message": f"Could not resolve a plan binding: {exc}",
                        }
                    )
                    trace.append(
                        {
                            "event": "binding_error",
                            "step_id": step_id,
                            "result_summary": {"ok": False, "error": error},
                        }
                    )
                    break
                if not isinstance(arguments, dict):
                    stopped_reason = f"could not resolve arguments for step {step_id}"
                    break
                runtime_step = {"tool": tool_name, **arguments}
                started = time.monotonic()
                invocation_count += 1
                try:
                    estimated_cost = self._estimate_step_cost(runtime_step, context)
                    if total_cost + estimated_cost > self.max_cost_inr + 1e-9:
                        stopped_reason = (
                            f"estimated cost ₹{estimated_cost:.6f} exceeds the "
                            f"remaining ₹{max(0.0, self.max_cost_inr - total_cost):.6f} budget"
                        )
                        trace.append(
                            {
                                "event": "budget_stop",
                                "step_id": step_id,
                                "tool": tool_name,
                                "reason": stopped_reason,
                                "estimated_cost_inr": estimated_cost,
                                "total_cost_inr": total_cost,
                                "max_cost_inr": self.max_cost_inr,
                            }
                        )
                        break

                    res = self._run_step(runtime_step, context)
                    if tool_name == "speak" and res.get("ok"):
                        res = self._back_transcribe_speech(res)
                        if res.get("ok"):
                            context["speech_verification"] = res["data"]["verification"]
                            verification_summary["audio_checks"].append(
                                {
                                    "step_id": step_id,
                                    "voice_id": res["data"].get("voice_id"),
                                    "duration_seconds": res["data"].get("duration_seconds"),
                                    **res["data"]["verification"],
                                }
                            )
                            if not runtime_step.get("voice_id"):
                                assumptions.append(
                                    f"Selected default Bodhan voice "
                                    f"{res['data'].get('voice_id')!r} for "
                                    f"{res['data'].get('language')}."
                                )
                    elif tool_name == "translate" and res.get("ok"):
                        verification_summary["translation_checks"].append(
                            {
                                "step_id": step_id,
                                "target_language": res["data"].get("target_language"),
                                "coverage_complete": res["data"].get("coverage_complete"),
                                "table_rows_preserved": res["data"].get("table_rows_preserved"),
                                "warnings": res.get("warnings", []),
                            }
                        )
                    elif tool_name == "ocr" and res.get("ok"):
                        verification_summary["ocr_checks"].append(
                            {
                                "step_id": step_id,
                                "pages_processed": res["data"].get("pages_processed"),
                                "total_pages": res["data"].get("total_pages"),
                                "coverage_complete": res["data"].get("coverage_complete"),
                            }
                        )
                    elif tool_name == "transcribe" and res.get("ok"):
                        if not runtime_step.get("language"):
                            detected = res["data"].get("language_detected")
                            assumptions.append(
                                f"Input audio/video language was unstated;"
                                f" {detected!r}."
                            )
                    elif tool_name == "detect_language" and res.get("ok"):
                        if res["data"].get("ambiguous"):
                            assumptions.append(
                                f"Script {res['data'].get('script')} matches multiple languages "
                                f"({res['data'].get('candidates')})."
                            )

                    latency_ms = int((time.monotonic() - started) * 1000)
                    usage = res.get("usage")
                    cached = bool(usage.get("cached", False)) if isinstance(usage, dict) else False
                    avoided = (
                        float(usage.get("avoided_inr", 0.0)) if isinstance(usage, dict) else 0.0
                    )
                    cost = (
                        float(usage.get("cost_inr", estimated_cost))
                        if isinstance(usage, dict)
                        else estimated_cost
                        if res.get("ok")
                        else 0.0
                    )
                    total_cost = round(total_cost + cost, 6)
                    if isinstance(usage, dict):
                        self.ledger.record(
                            model=str(usage.get("model", tool_name)),
                            op=str(tool_name),
                            cost_inr=cost,
                            cached=cached,
                            avoided_inr=avoided,
                            latency_ms=int(usage.get("latency_ms", latency_ms)),
                            units=usage.get("units", {}),
                        )

                    if res.get("ok"):
                        try:
                            self._verify_step(tool_name, res)
                        except ToolError as exc:
                            res = {**res, "ok": False, "error": exc.to_dict()}

                    step_record = {
                        "event": "tool_call",
                        "step_id": step_id,
                        "step_index": invocation_count - 1,
                        "iteration": iteration if for_each else None,
                        "tool": tool_name,
                        "arguments": self._trace_arguments(runtime_step),
                        "estimated_cost_inr": estimated_cost,
                        "cost_inr": cost,
                        "cached": cached,
                        "avoided_inr": avoided,
                        "latency_ms": latency_ms,
                        "result_summary": self._result_summary(res),
                    }
                    trace.append(step_record)
                    executed_steps.append(step_record)

                    if not res.get("ok"):
                        stopped_reason = "tool call failed"
                        if isinstance(res.get("error"), dict):
                            context["error"] = res["error"]
                        break
                    data = res.get("data")
                    if isinstance(data, dict):
                        for key, value in data.items():
                            collected.setdefault(key, []).append(value)
                    collected_artifacts.extend(res.get("artifacts", []))
                    step_artifacts.setdefault(step_id, []).extend(res.get("artifacts", []))
                    if total_cost > self.max_cost_inr + 1e-9:
                        stopped_reason = "reported cost exceeded the configured budget"
                        trace.append(
                            {
                                "event": "budget_stop",
                                "reason": stopped_reason,
                                "total_cost_inr": total_cost,
                                "max_cost_inr": self.max_cost_inr,
                            }
                        )
                        break
                except Exception as exc:
                    latency_ms = int((time.monotonic() - started) * 1000)
                    stopped_reason = f"step failed: {type(exc).__name__}"
                    error = (
                        exc.to_dict()
                        if isinstance(exc, ToolError)
                        else {"code": ErrorCode.INTERNAL.value, "message": str(exc)}
                    )
                    step_record = {
                        "event": "tool_call",
                        "step_id": step_id,
                        "step_index": invocation_count - 1,
                        "iteration": iteration if for_each else None,
                        "tool": tool_name,
                        "arguments": self._trace_arguments(runtime_step),
                        "latency_ms": latency_ms,
                        "cost_inr": 0.0,
                        "cached": False,
                        "result_summary": {"ok": False, "error": error},
                    }
                    trace.append(step_record)
                    executed_steps.append(step_record)
                    break
                if stopped_reason is not None:
                    break
            if for_each:
                step_outputs[step_id] = {
                    key: (
                        [item for value in value_list for item in value]
                        if value_list and all(isinstance(value, list) for value in value_list)
                        else value_list
                    )
                    for key, value_list in collected.items()
                }
            elif collected:
                step_outputs[step_id] = {key: values[-1] for key, values in collected.items()}
            else:
                step_outputs[step_id] = {}
            step_artifacts[step_id] = collected_artifacts
            if stopped_reason is not None:
                break

        declared_outputs: dict[str, Any] = {}
        for deliverable in deliverables or []:
            step_id = deliverable["step_id"]
            output = deliverable["output"]
            output_map = step_outputs.get(step_id, {})
            value = output_map.get(output)
            declared_outputs[deliverable["name"]] = value
            if output not in output_map or value is None:
                stopped_reason = stopped_reason or (
                    f"declared deliverable {deliverable['name']!r} was not produced"
                )
                trace.append(
                    {
                        "event": "deliverable_error",
                        "name": deliverable["name"],
                        "step_id": step_id,
                        "output": output,
                        "result_summary": {
                            "ok": False,
                            "error": {
                                "code": ErrorCode.PARTIAL_FAILURE.value,
                                "message": "The declared final output was not produced.",
                            },
                        },
                    }
                )
        if deliverables:
            context["deliverables"] = declared_outputs
            context["deliverable_artifacts"] = {
                item["name"]: step_artifacts.get(item["step_id"], [])
                for item in deliverables
            }
        context["_step_outputs"] = step_outputs
        context["_step_artifacts"] = step_artifacts

        result = AgentExecutionResult(
            success=stopped_reason is None and len(executed_steps) == invocation_count,
            steps=executed_steps,
            final_output=context,
            total_cost_inr=total_cost,
            stopped_reason=stopped_reason,
        )
        result.assumptions = list(dict.fromkeys(assumptions))
        result.verification = verification_summary
        trace.append(
            {
                "event": "summary",
                "success": result.success,
                "stopped_reason": stopped_reason,
                "steps_executed": len(executed_steps),
                "total_cost_inr": total_cost,
                "avoided_by_cache_inr": self.ledger.avoided_inr,
                "max_cost_inr": self.max_cost_inr,
                "max_steps": self.max_steps,
                "final_output_keys": sorted(k for k in context if not k.startswith("_")),
            }
        )
        result.trace = trace
        return result

    @staticmethod
    def _resolve_plan_value(
        value: Any,
        *,
        item: Any,
        step_outputs: dict[str, dict[str, Any]],
    ) -> Any:
        if isinstance(value, dict):
            if set(value) == {"$ref"}:
                ref = value["$ref"]
                return step_outputs[ref["step_id"]][ref["output"]]
            if set(value) == {"$item"}:
                item_value = item
                for part in value["$item"].split("."):
                    if part == "value" and not isinstance(item_value, dict):
                        continue
                    if not isinstance(item_value, dict) or part not in item_value:
                        raise ToolError(
                            ErrorCode.INVALID_ARGUMENT,
                            f"for_each item has no field {value['$item']!r}.",
                            hint="Bind a field provided by the selected iteration source.",
                        )
                    item_value = item_value[part]
                return item_value
            return {
                key: BodhanAgentLoop._resolve_plan_value(
                    nested,
                    item=item,
                    step_outputs=step_outputs,
                )
                for key, nested in value.items()
            }
        if isinstance(value, list):
            return [
                BodhanAgentLoop._resolve_plan_value(
                    nested,
                    item=item,
                    step_outputs=step_outputs,
                )
                for nested in value
            ]
        return value

    @staticmethod
    def _condition_is_true(
        condition: dict[str, Any],
        step_outputs: dict[str, dict[str, Any]],
    ) -> bool:
        ref = condition["ref"]
        actual = step_outputs[ref["step_id"]][ref["output"]]
        operator = condition["operator"]
        expected = condition.get("value")
        if operator == "truthy":
            return bool(actual)
        try:
            if operator == "equals":
                return actual == expected
            if operator == "not_equals":
                return actual != expected
            if operator == "greater_than":
                return actual > expected
            if operator == "greater_or_equal":
                return actual >= expected
            if operator == "less_than":
                return actual < expected
            if operator == "less_or_equal":
                return actual <= expected
            if operator == "in":
                return actual in expected
            if operator == "contains":
                return expected in actual
        except (TypeError, ValueError) as exc:
            raise ToolError(
                ErrorCode.INVALID_ARGUMENT,
                f"Condition operator {operator!r} cannot compare the referenced output.",
                hint="Use a comparison value compatible with the referenced output type.",
            ) from exc
        raise ToolError(
            ErrorCode.INVALID_ARGUMENT,
            f"Unsupported condition operator {operator!r}.",
            hint="Choose an operator documented in the composable plan schema.",
        )

    def _run_step(self, step: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        tool_name = step.get("tool")
        if tool_name == "transcribe":
            result = transcribe_audio(
                self.workspace,
                step["file_path"],
                language=step.get("language"),
                client=self.client,
                output_format=step.get("output_format", "text"),
                cache=self.cache,
            )
            if result.get("ok"):
                context["transcript"] = result["data"].get("transcript", "")
            return result

        if tool_name == "translate":
            source_key = step.get("source_key", "text")
            text = step.get("text")
            text_input = text if isinstance(text, str) and text else context.get(source_key, "")
            result = translate_text(
                self.workspace,
                text_input,
                target_language=step["target_language"],
                source_language=step.get("source_language"),
                client=self.client,
                cache=self.cache,
            )
            if result.get("ok"):
                context["translation"] = result["data"].get("translation", "")
            return result

        if tool_name == "speak":
            source_key = step.get("source_key", "text")
            text = step.get("text")
            text_input = text if isinstance(text, str) and text else context.get(source_key, "")
            result = speak_text(
                self.workspace,
                text_input,
                language=step["language"],
                voice_id=step.get("voice_id"),
                client=self.client,
                voice_gender=step.get("voice_gender"),
                output_format=step.get("output_format"),
                cache=self.cache,
            )
            if result.get("ok"):
                context["audio_path"] = result["data"].get("audio_path")
                context["audio_paths"] = result["data"].get("audio_paths", [])
            return result

        if tool_name == "ocr":
            page_number = step.get("page_number")
            result = ocr_document(
                self.workspace,
                step["file_path"],
                page_number=page_number,
                language=step.get("language"),
                client=self.client,
                table_format=step.get("table_format", "markdown"),
                cache=self.cache,
            )
            if result.get("ok"):
                ocr_text = result["data"].get("text", "")
                context["ocr_text"] = ocr_text
                context["text"] = ocr_text
            return result

        if tool_name == "detect_language":
            result = detect_language(step["text_or_file"], self.workspace)
            if result.get("ok"):
                context["detected_language"] = result["data"]
            return result

        if tool_name == "summarize_english":
            if self.english_summarizer is None:
                raise ToolError(
                    ErrorCode.INVALID_ARGUMENT,
                    "English summarization is unavailable in this agent instance.",
                    hint="Configure the planner's LLM summarizer and retry.",
                )
            text = step.get("text")
            if not isinstance(text, str) or not text.strip():
                raise ToolError(ErrorCode.INVALID_ARGUMENT, "Summary input must be non-empty text.")
            source_text = text
            text_suffixes = {".txt", ".md", ".markdown", ".srt", ".vtt"}
            if Workspace.is_probable_file_ref(text, text_suffixes):
                path = self.workspace.resolve_input(text, suffixes=text_suffixes)
                try:
                    source_text = path.read_text(encoding="utf-8")
                except UnicodeDecodeError as exc:
                    raise ToolError(
                        ErrorCode.UNSUPPORTED_FORMAT,
                        f"Could not decode {path.name!r} as UTF-8 text.",
                        hint="Save the file as UTF-8 and retry.",
                    ) from exc
            summary = self.english_summarizer(source_text, step.get("max_words"))
            if (
                not isinstance(summary, str)
                or not summary.strip()
                or contains_non_english_script(summary)
            ):
                raise ToolError(
                    ErrorCode.PARTIAL_FAILURE,
                    "The English summary was empty or contained non-English-script text.",
                    hint=(
                        "Indian-language text must come from Bodhan; "
                        "retry with an English-only summary."
                    ),
                )
            data = {
                "summary": summary.strip(),
                "word_count": len(summary.split()),
                "language": "en",
            }
            context["summary"] = data["summary"]
            return {"ok": True, "data": data, "artifacts": []}

        raise ToolError(
            ErrorCode.INVALID_ARGUMENT,
            f"Unknown pipeline tool {tool_name!r}.",
            hint=f"Choose one of the catalogued operations: {', '.join(AGENT_TOOLS)}.",
        )

    def _estimate_step_cost(self, step: dict[str, Any], context: dict[str, Any]) -> float:
        tool_name = step.get("tool")
        if tool_name in {"summarize_english", "detect_language"}:
            return 0.0
        if tool_name == "transcribe":
            path = self.workspace.resolve_input(step["file_path"])
            seconds = audio_duration_seconds(path)
            if seconds is None:
                raise ToolError(
                    ErrorCode.UNSUPPORTED_FORMAT,
                    f"Could not determine the duration of {path.name!r} for cost estimation.",
                    hint="Use a media file with readable duration metadata and try again.",
                )
            return cost_transcribe(seconds)
        if tool_name == "translate":
            text = self._text_for_estimate(
                step,
                context,
                {".txt", ".md", ".markdown", ".srt", ".vtt"},
            )

            return cost_translate(estimate_output_tokens(len(text) * 3))
        if tool_name == "speak":
            text = self._text_for_estimate(step, context, {".txt", ".md", ".markdown"})
            estimated_speech_seconds = len(text) / 8.0
            return cost_speak(len(text)) + cost_transcribe(estimated_speech_seconds)
        if tool_name == "ocr":
            path = self.workspace.resolve_input(step["file_path"])
            if path.suffix.lower() != ".pdf":
                return cost_ocr(1)
            import pypdfium2 as pdfium

            pdf = pdfium.PdfDocument(str(path))
            try:
                total_pages = len(pdf)
            finally:
                pdf.close()
            page_number = step.get("page_number")
            if page_number is not None:
                if not isinstance(page_number, int) or not 1 <= page_number <= total_pages:
                    raise ToolError(
                        ErrorCode.INVALID_ARGUMENT,
                        f"Page number must be between 1 and {total_pages}.",
                    )
                total_pages = 1
            return cost_ocr(total_pages)
        raise ToolError(
            ErrorCode.INVALID_ARGUMENT,
            f"Unknown pipeline tool {tool_name!r}.",
            hint=f"Choose one of the catalogued operations: {', '.join(AGENT_TOOLS)}.",
        )

    def _text_for_estimate(
        self,
        step: dict[str, Any],
        context: dict[str, Any],
        suffixes: set[str],
    ) -> str:
        value = step.get("text")
        if not isinstance(value, str) or not value:
            value = context.get(step.get("source_key", "text"), "")
        if not isinstance(value, str):
            raise ToolError(ErrorCode.INVALID_ARGUMENT, "Model input must be text.")
        if not Workspace.is_probable_file_ref(value, suffixes):
            return value
        path = self.workspace.resolve_input(value, suffixes=suffixes)
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError(
                ErrorCode.UNSUPPORTED_FORMAT,
                f"Could not decode {path.name!r} as UTF-8 text.",
                hint="Save the input file as UTF-8 and retry.",
            ) from exc

    def _verify_step(self, tool_name: str, result: dict[str, Any]) -> None:
        data = result.get("data")
        if not isinstance(data, dict):
            raise ToolError(ErrorCode.PARTIAL_FAILURE, f"{tool_name} returned no result data.")
        spec = AGENT_TOOLS.get(tool_name)
        if spec is not None:
            output_schema = spec.output_schema
            for key in output_schema.get("required", []):
                if key not in data:
                    raise ToolError(
                        ErrorCode.PARTIAL_FAILURE,
                        f"{tool_name} omitted required output {key!r}.",
                        hint="Retry the operation and check the provider response.",
                    )
            for key, value in data.items():
                property_schema = output_schema.get("properties", {}).get(key)
                if property_schema is None:
                    continue
                expected_types = property_schema.get("type")
                if isinstance(expected_types, str):
                    expected_types = [expected_types]
                if expected_types and not any(
                    self._matches_output_type(value, expected_type)
                    for expected_type in expected_types
                ):
                    raise ToolError(
                        ErrorCode.PARTIAL_FAILURE,
                        f"{tool_name} returned output {key!r} with an invalid type.",
                        hint=f"Expected {' or '.join(expected_types)}.",
                    )
        if tool_name == "translate":
            if not data.get("coverage_complete") or (
                data.get("translated_chunk_count") != data.get("chunk_count")
            ):
                raise ToolError(
                    ErrorCode.PARTIAL_FAILURE,
                    "Translation did not return output for every source chunk.",
                    hint="Retry the translation before using its output.",
                )
            if not str(data.get("translation", "")).strip():
                raise ToolError(
                    ErrorCode.PARTIAL_FAILURE,
                    "Translation returned empty output.",
                    hint="Retry the translation before using its output.",
                )
        elif tool_name == "transcribe" and not data.get("transcript", "").strip():
            raise ToolError(
                ErrorCode.PARTIAL_FAILURE,
                "Transcription output is empty.",
                hint="Check that the source audio is audible and supported.",
            )
        elif tool_name == "speak" and not (
            data.get("verification", {}).get("complete")
            and data.get("verification", {}).get("back_transcription_complete")
        ):
            raise ToolError(
                ErrorCode.PARTIAL_FAILURE,
                "Speech output could not be verified by back-transcribing every audio chunk.",
                hint="Check the speech and transcription endpoints, then retry synthesis.",
            )
        elif tool_name == "ocr" and not data.get("coverage_complete"):
            raise ToolError(
                ErrorCode.PARTIAL_FAILURE,
                "OCR did not process every requested document page.",
                hint="Check the OCR error and retry the failed document.",
            )

    @staticmethod
    def _matches_output_type(value: Any, type_name: str) -> bool:
        if type_name == "null":
            return value is None
        if type_name == "string":
            return isinstance(value, str)
        if type_name == "boolean":
            return isinstance(value, bool)
        if type_name == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if type_name == "number":
            return isinstance(value, int | float) and not isinstance(value, bool)
        if type_name == "object":
            return isinstance(value, dict)
        if type_name == "array":
            return isinstance(value, list)
        return False

    def _back_transcribe_speech(self, result: dict[str, Any]) -> dict[str, Any]:
        data = result.get("data")
        if not isinstance(data, dict):
            return result

        audio_artifacts = [
            item
            for item in result.get("artifacts", [])
            if isinstance(item, dict) and item.get("kind") == "audio"
        ]
        verification = dict(data.get("verification", {}))
        verification.update(
            {
                "back_transcription_complete": False,
                "back_transcribed_audio_chunks": 0,
                "recognized_character_count": 0,
                "transcript_artifact_refs": [],
                "script_match": None,
            }
        )
        data["verification"] = verification
        if not audio_artifacts:
            error = ToolError(
                ErrorCode.PARTIAL_FAILURE,
                "Speech synthesis returned no audio artifacts to verify.",
                hint="Retry synthesis and check the speech endpoint response.",
            )
            return {**result, "ok": False, "error": error.to_dict()}

        usage = dict(result.get("usage") or {})
        usage_units = dict(usage.get("units") or {})
        verification_cost = 0.0
        verification_latency = 0
        verified_chunks = 0
        recognized_characters = 0
        transcript_refs: list[str] = []
        transcript_artifacts: list[dict[str, Any]] = []
        combined_transcript: list[str] = []
        failure: ToolError | None = None

        for index, artifact in enumerate(audio_artifacts, start=1):
            back_transcription = transcribe_audio(
                self.workspace,
                str(artifact["path"]),
                language=data.get("language"),
                client=self.client,
                cache=self.cache,
            )
            if not back_transcription.get("ok"):
                partial_usage = (
                    back_transcription.get("error", {}).get("details", {}).get("partial_usage", {})
                )
                if isinstance(partial_usage, dict):
                    verification_cost += float(partial_usage.get("cost_inr", 0.0))
                    verification_latency += int(partial_usage.get("latency_ms", 0))
                    partial_units = partial_usage.get("units", {})
                    usage_units["verification_audio_seconds"] = usage_units.get(
                        "verification_audio_seconds", 0.0
                    ) + float(partial_units.get("audio_seconds", 0.0))
                failure = ToolError(
                    ErrorCode.PARTIAL_FAILURE,
                    f"Back-transcription failed for generated audio chunk "
                    f"{index}/{len(audio_artifacts)}.",
                    hint="Check transcription credentials and audio compatibility, then retry.",
                    details={"cause": back_transcription.get("error")},
                )
                break

            transcript = back_transcription["data"].get("transcript", "")
            if not isinstance(transcript, str) or not transcript.strip():
                failure = ToolError(
                    ErrorCode.PARTIAL_FAILURE,
                    f"Back-transcription returned no speech for generated audio chunk "
                    f"{index}/{len(audio_artifacts)}.",
                    hint="Check the synthesized audio and retry with a verified voice.",
                )
                break

            verified_chunks += 1
            recognized_characters += len(transcript)
            combined_transcript.append(transcript)
            transcript_refs.extend(
                f"artifact:{item['ref']}"
                for item in back_transcription.get("artifacts", [])
                if isinstance(item, dict) and isinstance(item.get("ref"), str)
            )
            transcript_artifacts.extend(
                item for item in back_transcription.get("artifacts", []) if isinstance(item, dict)
            )
            transcription_usage = back_transcription.get("usage") or {}
            verification_cost += float(transcription_usage.get("cost_inr", 0.0))
            verification_latency += int(transcription_usage.get("latency_ms", 0))
            usage_units["verification_audio_seconds"] = usage_units.get(
                "verification_audio_seconds", 0.0
            ) + float(transcription_usage.get("units", {}).get("audio_seconds", 0.0))

        script_match: bool | None = None
        if combined_transcript and data.get("language"):
            detected = detect_script_language("\n".join(combined_transcript))
            if detected["candidates"]:
                script_match = data["language"] in detected["candidates"]

        usage["model"] = "indic-speak+indic-transcribe"
        usage["cost_inr"] = round(float(usage.get("cost_inr", 0.0)) + verification_cost, 6)
        usage["latency_ms"] = int(usage.get("latency_ms", 0)) + verification_latency
        usage_units["verification_audio_chunks"] = float(verified_chunks)
        usage_units["verification_transcribe_cost_inr"] = round(verification_cost, 6)
        usage["units"] = usage_units
        verification.update(
            {
                "back_transcription_complete": failure is None
                and verified_chunks == len(audio_artifacts),
                "back_transcribed_audio_chunks": verified_chunks,
                "recognized_character_count": recognized_characters,
                "transcript_artifact_refs": transcript_refs,
                "script_match": script_match,
            }
        )
        result["usage"] = usage
        result["artifacts"] = [*result.get("artifacts", []), *transcript_artifacts]
        if failure is not None:
            return {**result, "ok": False, "error": failure.to_dict()}
        return result

    @staticmethod
    def _trace_arguments(step: dict[str, Any]) -> dict[str, Any]:
        def redact(value: Any, *, key: str = "") -> Any:
            if key in {"text", "text_or_file", "value"} and isinstance(value, str):
                return {"characters": len(value), "sha256": hash_text(value)}
            if isinstance(value, dict):
                return {name: redact(nested, key=name) for name, nested in value.items()}
            if isinstance(value, list):
                return [redact(item) for item in value]
            return value

        return redact(step)

    @staticmethod
    def _result_summary(result: dict[str, Any]) -> dict[str, Any]:
        if not result.get("ok"):
            return {"ok": False, "error": result.get("error")}
        data = result.get("data", {})
        usage = result.get("usage") or {}
        return {
            "ok": True,
            "artifacts": [
                {"ref": item.get("ref"), "kind": item.get("kind"), "bytes": item.get("bytes")}
                for item in result.get("artifacts", [])
            ],
            "data_summary": {
                key: value
                for key, value in data.items()
                if key.endswith("_count")
                or key
                in {
                    "coverage_complete",
                    "language",
                    "target_language",
                    "voice_id",
                    "audio_format",
                    "duration_seconds",
                    "script",
                    "candidates",
                    "ambiguous",
                    "verification",
                }
            },
            "usage": {
                "model": usage.get("model"),
                "cost_inr": usage.get("cost_inr", 0.0),
                "cached": usage.get("cached", False),
                "avoided_inr": usage.get("avoided_inr", 0.0),
                "latency_ms": usage.get("latency_ms", 0),
                "units": usage.get("units", {}),
            },
        }