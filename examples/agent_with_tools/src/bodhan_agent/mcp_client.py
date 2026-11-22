"""Connects the agent to the Bodhan MCP server and returns its tools."""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools


@asynccontextmanager
async def mcp_tools(output_root: Path | None = None) -> AsyncIterator[list[BaseTool]]:
    """Start `python -m bodhan_mcp` over stdio ONCE and yield its tools as LangChain tools.

    The server process (and its in-memory state) lives for the whole `async with` block and is
    stopped on exit. The tools are bound to that open session, so use them only inside the block.
    """
    # The server is a child process; pass the environment explicitly (MCP only inherits a safe subset).
    env = dict(os.environ)
    if output_root is not None:
        env["BODHAN_OUTPUT_ROOT"] = str(output_root)  # confines write_text_file to this run's artifacts folder
    client = MultiServerMCPClient({"bodhan": {"transport": "stdio", "command": sys.executable, "args": ["-m", "bodhan_mcp"], "env": env}})
    async with client.session("bodhan") as session:
        tools = await load_mcp_tools(session)   # ask the server "what tools do you have?"
        yield tools # hand tools to the caller and pause here

