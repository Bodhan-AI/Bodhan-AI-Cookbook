"""Step executor mapping abstract plan IDs to paths and executing MCP tool calls (SPEC §8.5)."""

import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from bodhan_agent.planner import Step
from bodhan_mcp import errors
from bodhan_mcp.tools.media import chunk_audio, get_voice_library, inspect_media
from bodhan_mcp.tools.ocr import ocr_document, ocr_page
from bodhan_mcp.tools.speak import synthesize_speech
from bodhan_mcp.tools.transcribe import transcribe_audio
from bodhan_mcp.tools.translate import translate_text


class StepResult(BaseModel):
    """Result of an executed plan step."""
    model_config = ConfigDict(extra="forbid")

    step_id: str
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    result_data: Any = None
    result_summary: str = ""
    latency_ms: float = 0.0
    cost_inr: float = 0.0
    error: str | None = None


async def execute_step(
    step: Step,
    id_to_path: dict[str, str],
    previous_step_results: dict[str, StepResult],
    out_dir: str | None = None,
    client: Any = None,
) -> StepResult:
    """Execute a single plan step, mapping IDs to paths and capturing latency and cost."""
    start_time = time.perf_counter()
    tool = step.tool
    args = dict(step.args)
    cost_inr = 0.0
    error_msg = None
    res_data = None
    summary = ""

    # 1. Resolve file_id -> absolute filesystem path (Code responsibility, never LLM)
    for path_key in ("file_id", "input_path", "path", "pdf_path", "image_path"):
        if path_key in args and isinstance(args[path_key], str) and args[path_key] in id_to_path:
            f_id = args.pop(path_key)
            path = id_to_path.get(f_id, "")
            if tool in ("ocr_document",):
                args["pdf_path"] = path
            elif tool in ("ocr_page",):
                args["image_path"] = path
            elif tool in ("inspect_media", "chunk_audio"):
                args["path"] = path
            elif tool in ("translate_text", "synthesize_speech"):
                if path and path.endswith(".txt") and Path(path).exists():
                    args["text"] = Path(path).read_text(encoding="utf-8")
                else:
                    args["input_path"] = path
            else:
                args["input_path"] = path
            break

    # 2. Resolve from_step dependencies for arguments
    for k, v in list(args.items()):
        if isinstance(v, dict) and "from_step" in v:
            dep_id = v["from_step"]
            dep_res = previous_step_results.get(dep_id)
            if dep_res and dep_res.result_data:
                data = dep_res.result_data
                if k == "text":
                    if hasattr(data, "transcript"):
                        args["text"] = data.transcript
                    elif hasattr(data, "translated"):
                        args["text"] = data.translated
                    elif hasattr(data, "markdown"):
                        args["text"] = data.markdown
                    elif isinstance(data, dict):
                        args["text"] = data.get("text", "") or data.get("translated", "") or data.get("markdown", "")
                    else:
                        args["text"] = str(data)
                elif k == "voice_id":
                    voices_dict = getattr(data, "voices", data)
                    if isinstance(voices_dict, dict):
                        target_l = str(args.get("language", "hi")).lower()
                        lang_m = {"hindi": "hi", "tamil": "ta", "telugu": "te", "bengali": "bn"}
                        l_code = lang_m.get(target_l, target_l)
                        if l_code in voices_dict and voices_dict[l_code]:
                            args["voice_id"] = "Kavya" if "Kavya" in voices_dict[l_code] else voices_dict[l_code][0]
                        else:
                            args["voice_id"] = "Kavya"
                    elif "kavya" in str(data).lower():
                        args["voice_id"] = "Kavya"
                    elif "ankur" in str(data).lower():
                        args["voice_id"] = "Ankur"
                    else:
                        args["voice_id"] = "Kavya"
                else:
                    args[k] = data
            else:
                if k == "voice_id":
                    args["voice_id"] = "Kavya"

    if isinstance(args.get("text"), str) and args["text"].startswith("from_step:"):
        dep_id = args["text"].split(":", 1)[1]
        dep_res = previous_step_results.get(dep_id)
        if dep_res and dep_res.result_data:
            data = dep_res.result_data
            if hasattr(data, "transcript"):
                args["text"] = data.transcript
            elif hasattr(data, "translated"):
                args["text"] = data.translated
            elif hasattr(data, "markdown"):
                args["text"] = data.markdown
            else:
                args["text"] = str(data)
    elif isinstance(args.get("text"), str) and args["text"] in previous_step_results:
        dep_id = args["text"]
        dep_res = previous_step_results.get(dep_id)
        if dep_res and dep_res.result_data:
            data = dep_res.result_data
            if hasattr(data, "transcript"):
                args["text"] = data.transcript
            elif hasattr(data, "translated"):
                args["text"] = data.translated
            elif hasattr(data, "markdown"):
                args["text"] = data.markdown
            else:
                args["text"] = str(data)

    if "from_step" in args:
        dep_id = args.pop("from_step")
        dep_res = previous_step_results.get(dep_id)
        if dep_res and dep_res.result_data:
            data = dep_res.result_data
            if hasattr(data, "transcript"):
                args["text"] = data.transcript
            elif hasattr(data, "translated"):
                args["text"] = data.translated
            elif hasattr(data, "markdown"):
                args["text"] = data.markdown
            elif isinstance(data, dict):
                args["text"] = data.get("text", "") or data.get("translated", "") or data.get("markdown", "")
            else:
                args["text"] = str(data)

    try:
        # Tool dispatch
        if tool == "transcribe_audio":
            res = await transcribe_audio(**args, client=client)
            res_data = res
            if isinstance(res, errors.ToolErrorPayload):
                error_msg = res.message
                summary = f"Error: {res.code} - {res.message}"
            else:
                cost_inr = res.cost_inr
                summary = f"{res.language} transcript ({res.duration_s}s, {len(res.segments)} segments): '{res.transcript[:40]}...'"

        elif tool == "translate_text":
            res = await translate_text(**args, client=client)
            res_data = res
            if isinstance(res, errors.ToolErrorPayload):
                error_msg = res.message
                summary = f"Error: {res.code} - {res.message}"
            else:
                cost_inr = res.cost_inr
                summary = f"Translated {len(res.translated)} chars: '{res.translated[:40]}...'"

        elif tool == "synthesize_speech":
            if out_dir:
                args["out_dir"] = out_dir
            res = await synthesize_speech(**args, client=client)
            res_data = res
            if isinstance(res, errors.ToolErrorPayload):
                error_msg = res.message
                summary = f"Error: {res.code} - {res.message}"
            else:
                cost_inr = res.cost_inr
                summary = f"Synthesized {res.chars} chars -> {res.path} ({res.duration_s}s)"

        elif tool == "ocr_page":
            res = await ocr_page(**args, client=client)
            res_data = res
            if isinstance(res, errors.ToolErrorPayload):
                error_msg = res.message
                summary = f"Error: {res.code} - {res.message}"
            else:
                cost_inr = res.cost_inr
                summary = f"OCR page {res.page} -> {len(res.markdown)} chars markdown"

        elif tool == "ocr_document":
            res = await ocr_document(**args, client=client)
            res_data = res
            if isinstance(res, errors.ToolErrorPayload):
                error_msg = res.message
                summary = f"Error: {res.code} - {res.message}"
            else:
                cost_inr = res.cost_inr
                summary = f"OCR document {res.pages} pages -> {len(res.markdown)} chars markdown"

        elif tool == "inspect_media":
            path_arg = args.get("input_path", args.get("path", ""))
            res = inspect_media(path=path_arg)
            res_data = res
            if isinstance(res, errors.ToolErrorPayload):
                error_msg = res.message
                summary = f"Error: {res.code} - {res.message}"
            else:
                summary = f"{res.kind} media ({res.format}, {res.size_mb}MB)"

        elif tool == "chunk_audio":
            path_arg = args.get("input_path", args.get("path", ""))
            res = chunk_audio(path=path_arg)
            res_data = res
            if isinstance(res, errors.ToolErrorPayload):
                error_msg = res.message
                summary = f"Error: {res.code} - {res.message}"
            else:
                summary = f"{len(res.chunks)} audio chunks created"

        elif tool == "get_voice_library":
            res = get_voice_library()
            res_data = res
            summary = f"{len(res.voices)} languages in voice library"

        else:
            error_msg = f"Unknown tool: {tool}"
            summary = error_msg

    except Exception as exc:
        error_msg = str(exc)
        summary = f"Execution exception: {exc}"

    latency_ms = (time.perf_counter() - start_time) * 1000.0

    return StepResult(
        step_id=step.id,
        tool=tool,
        args=step.args,
        result_data=res_data,
        result_summary=summary,
        latency_ms=round(latency_ms, 2),
        cost_inr=round(cost_inr, 4),
        error=error_msg,
    )
