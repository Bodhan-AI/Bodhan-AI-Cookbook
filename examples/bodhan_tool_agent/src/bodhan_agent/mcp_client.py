"""MCP Client wrapper that starts 'python -m bodhan_mcp' over stdio."""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


COMPACT_AGENT_TOOL_DESCRIPTIONS: Dict[str, str] = {
    "indic_translate": "Translate text between English and 22 Indian languages. Supports @stepN references.",
    "indic_ocr": "Extract text from image (PNG, JPEG) or PDF. Returns structured page text.",
    "indic_transcribe": "Transcribe Indian-language speech audio (WAV, MP3, M4A) to text. Returns transcribed text.",
    "indic_speak": "Synthesize speech WAV from text. Non-English text must use @stepN references. Saves WAV to output_path.",
    "inspect_media": "Inspect media file (image, audio, PDF) for type, duration, or page count.",
    "read_text_file": "Read text file (.txt, .md, .csv, .srt) content.",
    "make_srt": "Generate SRT subtitle file: make_srt(transcript=@stepN, text=@stepM.text, output_path='artifacts/subtitles.srt'). Deterministically formats timed cues from transcript chunk boundaries.",
    "detect_script_language": "Detect script and language of text using Unicode analysis.",
    "finish": "Finish task. Set status 'completed' (text deliverables written to result.md) or 'impossible' if task requires unsupported capabilities.",
}


class BodhanMcpClient:
    """Manages connection to bodhan_mcp server subprocess over stdio transport."""

    def __init__(self, files_dir: Optional[str] = None):
        self.files_dir = files_dir or os.getcwd()
        self._session: Optional[ClientSession] = None
        self._client_cm = None
        self._tools_cache: Dict[str, Any] = {}

    async def start(self) -> None:
        """Starts MCP server subprocess over stdio and initializes session."""
        env = os.environ.copy()
        src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        env["PYTHONPATH"] = src_path
        env["BODHAN_FILES_DIR"] = os.path.abspath(self.files_dir)

        server_params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "bodhan_mcp"],
            env=env,
        )

        self._client_cm = stdio_client(server_params)
        read, write = await self._client_cm.__aenter__()
        self._session = ClientSession(read, write)
        await self._session.__aenter__()
        await self._session.initialize()

        # Cache available tools
        tools_resp = await self._session.list_tools()
        self._tools_cache = {tool.name: tool for tool in tools_resp.tools}

    async def close(self) -> None:
        """Terminates session and subprocess cleanly."""
        if self._session:
            try:
                await self._session.__aexit__(None, None, None)
            except Exception:
                pass
            self._session = None

        if self._client_cm:
            try:
                await self._client_cm.__aexit__(None, None, None)
            except Exception:
                pass
            self._client_cm = None

    def get_openai_tool_definitions(self) -> List[Dict[str, Any]]:
        """Converts MCP tool schemas into OpenAI-compatible tool definitions with compact agent descriptions."""
        openai_tools = []
        for tool in self._tools_cache.values():
            if tool.name == "echo":
                continue
            schema = getattr(tool, "input_schema", getattr(tool, "inputSchema", None)) or {
                "type": "object",
                "properties": {},
            }
            desc = COMPACT_AGENT_TOOL_DESCRIPTIONS.get(tool.name, tool.description or "")
            openai_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": desc,
                        "parameters": schema,
                    },
                }
            )

        # Agent-internal tool: finish (never on MCP server, but provided to agent LLM)
        if not any(t["function"]["name"] == "finish" for t in openai_tools):
            openai_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": "finish",
                        "description": COMPACT_AGENT_TOOL_DESCRIPTIONS.get(
                            "finish",
                            "Finish task. Use status 'completed' when finished, or 'impossible' if task cannot be satisfied.",
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "status": {
                                    "type": "string",
                                    "enum": ["completed", "failed", "impossible"],
                                    "description": "Final status of the task execution.",
                                },
                                "explanation": {
                                    "type": "string",
                                    "description": "Short explanation of the outcome and summary of generated artifacts.",
                                },
                            },
                            "required": ["status", "explanation"],
                        },
                    },
                }
            )
        return openai_tools

    async def call_tool(
        self, name: str, arguments: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], Optional[str]]:
        """Calls tool on the MCP server and parses returned JSON content.

        Returns:
            (result_dict, error_string)
        """
        if not self._session:
            return {}, "MCP Client session is not running."

        if name not in self._tools_cache:
            return {}, f"Unknown tool '{name}'. Available: {list(self._tools_cache.keys())}"

        try:
            call_resp = await self._session.call_tool(name, arguments=arguments)
            if not call_resp.content:
                return {}, "Tool returned empty content."

            first_text = call_resp.content[0].text
            try:
                data = json.loads(first_text)
                return data, None
            except json.JSONDecodeError:
                return {"raw_text": first_text}, None

        except Exception as e:
            return {}, f"MCP call_tool error on '{name}': {str(e)}"
