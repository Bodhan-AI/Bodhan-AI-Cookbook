#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from mcp.server.stdio import stdio_server
from server import mcp

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.chdir(Path(__file__).resolve().parent.parent)


async def main():
    async with stdio_server() as streams:
        await mcp.run(stdin_stream=streams[0], stdout_stream=streams[1])


if __name__ == "__main__":
    asyncio.run(main())
