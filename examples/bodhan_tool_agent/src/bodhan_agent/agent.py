from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openai import OpenAI

load_dotenv(".env")

SYSTEM_PROMPT = """You are the Bodhan Task Agent.
You have access to MCP tools for Indian language and document AI:
- inspect_media: inspect local audio, PDF, image, or text files
- indic_transcribe: transcribe speech audio (>30s auto-chunked)
- indic_translate: translate text across Indian languages and English
- indic_speak: synthesize speech into a WAV file under outputs/
- indic_ocr: extract text, layout, and tables from images or PDFs

Plan clearly, call the required tools with accurate arguments, inspect tool outputs before answering, and summarize results clearly including any output paths or cost ledger figures when present."""


def _build_llm_client() -> tuple[OpenAI, str]:
    api_key = (
        os.environ.get("AGENT_LLM_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("BODHAN_API_KEY")
        or ""
    ).strip()
    if not api_key:
        raise RuntimeError(
            "Missing LLM API key. Set AGENT_LLM_API_KEY, OPENAI_API_KEY, or BODHAN_API_KEY in your environment or .env file."
        )

    raw_base_url = (
        os.environ.get("AGENT_LLM_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("BODHAN_BASE_URL")
        or ""
    ).strip().rstrip("/")

    if raw_base_url and not raw_base_url.endswith("/v1"):
        base_url: str | None = f"{raw_base_url}/v1"
    else:
        base_url = raw_base_url or None

    model = (
        os.environ.get("BODHAN_AGENT_MODEL")
        or os.environ.get("OPENAI_MODEL")
        or "gpt-4o-mini"
    ).strip()

    client = OpenAI(api_key=api_key, base_url=base_url)
    return client, model


def _mcp_tools_to_openai_tools(mcp_tools: list[Any]) -> list[dict[str, Any]]:
    openai_tools: list[dict[str, Any]] = []
    for tool in mcp_tools:
        schema = tool.inputSchema or {"type": "object", "properties": {}}
        openai_tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": schema,
                },
            }
        )
    return openai_tools


def _extract_tool_text(result: Any) -> str:
    parts: list[str] = []
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text is not None:
            parts.append(text)
    if parts:
        return "\n".join(parts)
    if hasattr(result, "model_dump"):
        return json.dumps(result.model_dump(), ensure_ascii=False, indent=2)
    return str(result)


def _show_exceptions(err: BaseException, depth: int = 0) -> None:
    prefix = "  " * depth
    print(f"{prefix}Agent error ({type(err).__name__}): {err}", file=sys.stderr)
    nested = getattr(err, "exceptions", None)
    if nested:
        for sub in nested:
            _show_exceptions(sub, depth + 1)
    elif getattr(err, "__cause__", None) is not None:
        _show_exceptions(err.__cause__, depth + 1)
    elif getattr(err, "__context__", None) is not None:
        _show_exceptions(err.__context__, depth + 1)


async def run_agent_task(prompt: str, max_turns: int = 8) -> str:
    llm, model = _build_llm_client()

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "bodhan_mcp.server"],
        env=dict(os.environ),
    )

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools_response = await session.list_tools()
            openai_tools = _mcp_tools_to_openai_tools(tools_response.tools)

            messages: list[dict[str, Any]] = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ]

            for _ in range(max_turns):
                response = llm.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=openai_tools if openai_tools else None,
                    tool_choice="auto" if openai_tools else None,
                )

                msg = response.choices[0].message
                tool_calls = getattr(msg, "tool_calls", None) or []

                if not tool_calls:
                    return (msg.content or "").strip()

                assistant_entry: dict[str, Any] = {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in tool_calls
                    ],
                }
                messages.append(assistant_entry)

                for tc in tool_calls:
                    tool_name = tc.function.name
                    raw_args = tc.function.arguments or "{}"
                    try:
                        parsed_args = json.loads(raw_args)
                        if not isinstance(parsed_args, dict):
                            parsed_args = {}
                    except json.JSONDecodeError as exc:
                        tool_output = json.dumps(
                            {"status": "error", "error": f"Invalid JSON arguments: {exc}"}
                        )
                    else:
                        tool_result = await session.call_tool(tool_name, arguments=parsed_args)
                        tool_output = _extract_tool_text(tool_result)

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": tool_output,
                        }
                    )

            return "Reached maximum reasoning turns without a final response."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bodhan-agent",
        description="Run task prompts against the Bodhan MCP server.",
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        help="Task instruction for the agent (or pass via stdin / --interactive).",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=8,
        help="Maximum tool-calling loop iterations (default: 8).",
    )
    args = parser.parse_args(argv)

    prompt = args.prompt
    if not prompt and not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()

    if not prompt:
        print("Usage: python -m bodhan_agent.agent \"<your task prompt>\"")
        return 1

    try:
        output = asyncio.run(run_agent_task(prompt=prompt, max_turns=args.max_turns))
        print(output)
        return 0
    except BaseException as exc:
        _show_exceptions(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())