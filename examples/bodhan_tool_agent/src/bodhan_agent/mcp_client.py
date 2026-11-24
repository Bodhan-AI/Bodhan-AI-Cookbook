
import os
import sys

from pathlib import Path
from contextlib import asynccontextmanager

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@asynccontextmanager
async def connect_to_mcp_server():
    """
    Start the Bodhan MCP server and establish a session.
    """

    project_root = Path(__file__).resolve().parents[2]

    env = os.environ.copy()

    src_path = str(project_root / "src")

    existing_pythonpath = env.get("PYTHONPATH", "")

    env["PYTHONPATH"] = (
        src_path
        + os.pathsep
        + existing_pythonpath
    )

    server_parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "bodhan_mcp"],
        env=env,
    )

    async with stdio_client(server_parameters) as (read, write):

        async with ClientSession(read, write) as session:

            await session.initialize()

            yield session
