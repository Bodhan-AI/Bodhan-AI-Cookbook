from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openai import OpenAI

from . import bodhan_tools as tools
from .config import Settings

TOOL_NAMES = {"indic_asr", "indic_translate", "indic_tts", "indic_ocr"}
TOOL_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "indic_asr",
            "description": "Transcribe an audio file with Bodhan Indic-Transcribe.",
            "parameters": {
                "type": "object",
                "properties": {"audio_path": {"type": "string"}, "language": {"type": "string"}},
                "required": ["audio_path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "indic_translate",
            "description": "Translate text to an Indic language or English with Bodhan. French is unsupported.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "target_language": {"type": "string"},
                    "script": {"type": "string", "enum": ["native", "roman", "codemix"]},
                },
                "required": ["text", "target_language"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "indic_tts",
            "description": "Generate Indic or English speech with Bodhan Indic-Speak. Use a language-appropriate voice from the cookbook rules.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "language": {"type": "string"},
                    "output_path": {"type": "string"},
                    "voice": {"type": "string"},
                    "style": {"type": "string"},
                },
                "required": ["text", "language", "output_path", "voice"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "indic_ocr",
            "description": "Read text and layout from an image using Bodhan Indic-OCR.",
            "parameters": {
                "type": "object",
                "properties": {"image_path": {"type": "string"}, "prompt": {"type": "string"}},
                "required": ["image_path"],
                "additionalProperties": False,
            },
        },
    },
]


def _verify(name: str, result: dict[str, Any]) -> None:
    if name in {"indic_asr", "indic_translate", "indic_ocr"} and not str(result.get("text", "")).strip():
        raise ValueError(f"Verification failed: {name} returned empty text")
    if name == "indic_tts" and not Path(result["media_ref"]).is_file():
        raise ValueError("Verification failed: speech output file is missing")


def _cost_value(value: Any) -> float | None:
    try:
        cost = float(value)
        return cost if cost >= 0 else None
    except (TypeError, ValueError):
        return None


def _supported_task_guard(task: str) -> str | None:
    asks_for_french = re.search(
        r"\b(?:into|to|in|as)\s+(?:the\s+)?(?:french|français|francais)\b|\b(?:french|français|francais)\s+translation\b",
        task,
        re.I,
    )
    if asks_for_french and re.search(r"\b(translat|convert)\w*\b", task, re.I):
        return "Unsupported task: Bodhan indic-translate does not support French. No translation call was made."
    return None


def run_task(
    task: str,
    settings: Settings | None,
    output_dir: Path,
    input_file: Path | None = None,
    max_steps: int | None = None,
    max_budget_inr: float | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_path, report_path, result_path = output_dir / "trace.jsonl", output_dir / "report.json", output_dir / "result.md"
    trace_path.write_text("", encoding="utf-8")
    unsupported = _supported_task_guard(task)
    step_limit = max_steps if max_steps is not None else (settings.max_steps if settings else 8)
    budget_limit = (settings.max_budget_inr if settings else 10.0) if max_budget_inr is None else max_budget_inr
    if step_limit < 1 or budget_limit < 0:
        raise ValueError("Step limit must be positive and budget limit cannot be negative")
    state: dict[str, Any] = {"status": "planned", "task": task, "steps": [], "spent_inr": 0.0, "unmetered_calls": 0}
    if unsupported:
        state.update(status="unsupported", summary=unsupported)
        return _write_outputs(state, trace_path, report_path, result_path)
    if settings is None:
        raise ValueError("Credentials are required for supported tasks. Copy .env.example to .env and configure Bodhan and planner keys.")

    client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url, timeout=settings.timeout_seconds, max_retries=settings.max_retries)
    user_content = task
    user_content += f"\nGenerated media output directory: {output_dir.resolve()}"
    if input_file:
        user_content += f"\nInput media local reference: {input_file.expanduser().resolve()} (pass this exact path to a compatible tool)."
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "You are a bounded planner for a Bodhan-only media agent. Use only the four provided Bodhan tools. "
                "Never claim completion without tool output. Preserve local media references exactly. "
                "Do not put file contents in plans. Save generated audio under the requested output directory. "
                "For TTS choose a voice listed for the language in the Bodhan cookbook rules. "
                "Return a concise final answer and say when the requested capability is unsupported."
            ),
        },
        {"role": "user", "content": user_content},
    ]
    cache: dict[str, dict[str, Any]] = {}
    for _step in range(step_limit):
        try:
            response = client.chat.completions.create(model=settings.llm_model, messages=messages, tools=TOOL_SCHEMA, tool_choice="auto")
        except Exception as exc:
            state.update(status="failed", summary=f"Planner request failed ({type(exc).__name__}).")
            break
        message = response.choices[0].message
        if not message.tool_calls:
            summary = (message.content or "").strip()
            if not summary:
                state.update(status="failed", summary="Planner returned neither tool calls nor a summary.")
            elif re.search(r"\b(unsupported|cannot|can't|unable to)\b", summary, re.I):
                state.update(status="unsupported", summary=summary)
            else:
                state.update(status="planned" if dry_run else "completed", summary=summary)
            break
        messages.append({"role": "assistant", "content": message.content, "tool_calls": [tc.model_dump(exclude_none=True) for tc in message.tool_calls]})
        stop = False
        for call in message.tool_calls:
            if len(state["steps"]) >= step_limit:
                state.update(status="step_limit", summary=f"Stopped at the configured {step_limit}-step limit.")
                stop = True
                break
            name = call.function.name
            if name not in TOOL_NAMES:
                state.update(status="failed", summary=f"Planner requested unsupported tool '{name}'.")
                stop = True
                break
            try:
                arguments = json.loads(call.function.arguments)
            except json.JSONDecodeError:
                state.update(status="failed", summary="Planner returned invalid tool arguments.")
                stop = True
                break
            cache_key = hashlib.sha256((name + json.dumps(arguments, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()
            if cache_key in cache:
                result = cache[cache_key]
                event = {"event": "cache_hit", "tool": name, "step": len(state["steps"]) + 1}
                state["steps"].append({"tool": name, "cached": True, **result})
            else:
                if dry_run:
                    result = {"dry_run": True, "tool": name}
                    event = {"event": "planned_call", "tool": name, "step": len(state["steps"]) + 1}
                else:
                    cost = sum(_cost_value(s.get("cost_inr")) or 0.0 for s in state["steps"])
                    if cost >= budget_limit:
                        state.update(status="budget_exhausted", summary="Known API charges reached the configured budget limit; remaining calls were stopped.")
                        stop = True
                        break
                    try:
                        result = getattr(tools, name)(settings, **arguments)
                        _verify(name, result)
                        cache[cache_key] = result
                        event = {"event": "tool_call", "tool": name, "step": len(state["steps"]) + 1}
                    except Exception as exc:
                        result = {"error": f"{type(exc).__name__}: {exc}"}
                        event = {"event": "tool_error", "tool": name, "step": len(state["steps"]) + 1}
                state["steps"].append({"tool": name, "arguments": arguments, **result})
                if event["event"] == "tool_call":
                    reported_cost = _cost_value(result.get("cost_inr"))
                    if reported_cost is None:
                        state["unmetered_calls"] += 1
                    else:
                        state["spent_inr"] += reported_cost
            _append_trace(
                trace_path, {**event, "at": datetime.now(timezone.utc).isoformat(), "arguments": _redact_paths(arguments), "result": _safe_result(result)}
            )
            messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(_safe_result(result), ensure_ascii=False)})
        if stop:
            break
    else:
        state.update(status="step_limit", summary=f"Stopped at the configured {step_limit}-step limit.")
    if "summary" not in state:
        state.update(status="step_limit", summary=f"Stopped at the configured {step_limit}-step limit.")
    return _write_outputs(state, trace_path, report_path, result_path)


def _redact_paths(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("<local-file-reference>" if k.endswith("_path") or k == "output_path" else _redact_paths(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_paths(x) for x in value]
    return value


def _safe_result(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: ("<generated-media-file>" if k == "media_ref" else "<text omitted; see report.json>" if k == "text" else _safe_result(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_safe_result(x) for x in value]
    return value


def _append_trace(path: Path, event: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + "\n")


def _write_outputs(state: dict[str, Any], trace: Path, report: Path, result: Path) -> dict[str, Any]:
    public = {**state, "trace": str(trace), "report": str(report), "result": str(result)}
    report.write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding="utf-8")
    body = [
        "# Task result",
        "",
        f"**Status:** {state['status']}",
        "",
        state.get("summary", ""),
        "",
        f"**Tool steps:** {len(state['steps'])}",
        f"**Known spend:** ₹{state['spent_inr']:.4f}",
        f"**Unmetered Bodhan calls:** {state['unmetered_calls']}",
        "",
        "## Outputs",
        "",
    ]
    for item in state["steps"]:
        body.append(f"- `{item['tool']}`: {item.get('text') or item.get('media_ref') or item.get('error') or 'completed'}")
    result.write_text("\n".join(body) + "\n", encoding="utf-8")
    return public
