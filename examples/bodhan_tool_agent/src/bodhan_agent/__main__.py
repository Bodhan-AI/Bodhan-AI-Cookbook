from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import AIMessage
from langchain_core.tracers.langchain import wait_for_all_tracers

from bodhan_core import Run, ToolError
from bodhan_agent.graph import run_task


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Run the Bodhan LangGraph task agent.")
    result.add_argument("--task", required=True)
    result.add_argument("--files", required=True, nargs="+")
    result.add_argument("--out", default="./out")
    result.add_argument("--max-steps", type=int, default=30)
    result.add_argument("--max-cost-inr", type=float, default=20.0)
    return result


def _result_status(run: Run, checks: list[dict]) -> str:
    failed_checks = any(not check.get("ok", False) for check in checks)
    unresolved = bool(run.stop_reason or failed_checks or any(not error.get("recovered", False)
                                                             for error in run.errors))
    status = ("partial" if run.artifacts else "failed") if unresolved else "completed"
    impossible_codes = {
        "unsupported_language", "file_not_found", "unsupported_media", "empty_input", "empty_transcription",
    }
    error_codes = {error["code"] for error in run.errors if not error.get("recovered", False)}
    if not run.artifacts and not run.tool_successes and error_codes \
            and error_codes <= impossible_codes:
        status = "impossible"
    if status == "completed" and run.steps == 0 and not run.artifacts:
        status = "failed"
        run.errors.append({"code": "no_tool_calls",
                           "message": "The planner returned without invoking a tool; task completion was not verified."})
    return status


async def _run(args) -> int:
    load_dotenv()
    try:
        run = Run(args.files, args.out, args.max_steps, args.max_cost_inr)
    except ToolError as exc:
        fallback = Run([], args.out)
        fallback.errors.append({"code": exc.code, "message": exc.message})
        fallback.event("stop", code=exc.code)
        fallback.write_outputs(exc.message, "impossible" if exc.code == "file_not_found" else "failed")
        await fallback.close()
        print(f"{exc.code}: {exc.message}")
        return 0 if exc.code == "file_not_found" else 2
    try:
        required = [name for name in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY") if not os.getenv(name)]
        if required:
            raise ToolError("missing_llm_config", "Set " + ", ".join(required) + " in .env.")
        state = await run_task(run, args.task, os.environ["LLM_BASE_URL"],
                               os.environ["LLM_MODEL"], os.environ["LLM_API_KEY"])
        final = next((str(message.content) for message in reversed(state["messages"])
                      if isinstance(message, AIMessage) and not message.tool_calls), "")
        final = final or run.stop_message or "The task stopped before producing a final answer."
        checks = state.get("verification", [])
        status = _result_status(run, checks)
        run.write_outputs(final or "The task stopped without a final answer.", status)
        print(f"{status}: {Path(args.out).resolve()}")
        return 0 if status in {"completed", "impossible"} else 1
    except ToolError as exc:
        run.event("stop", code=exc.code)
        run.write_outputs(exc.message, "failed")
        print(f"{exc.code}: {exc.message}")
        return 2
    except Exception as exc:
        status = "partial" if run.artifacts else "failed"
        message = f"The planner stopped unexpectedly ({type(exc).__name__}). Check LLM configuration and retry."
        run.errors.append({"code": "planner_error", "message": message})
        run.event("stop", code="planner_error", detail=type(exc).__name__)
        run.write_outputs(message, status)
        print(f"planner_error: {type(exc).__name__}")
        return 1
    finally:
        await run.close()
        wait_for_all_tracers()


def main() -> None:
    raise SystemExit(asyncio.run(_run(parser().parse_args())))


if __name__ == "__main__":
    main()
