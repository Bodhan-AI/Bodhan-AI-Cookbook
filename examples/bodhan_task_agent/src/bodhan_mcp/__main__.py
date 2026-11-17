from __future__ import annotations

import anyio
from dotenv import find_dotenv, load_dotenv
from mcp.server.stdio import stdio_server

from bodhan_mcp.utils.logging_setup import configure_logging


async def _serve() -> None:
    from bodhan_mcp.server import build_server

    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    load_dotenv(find_dotenv(usecwd=True))
    configure_logging()
    anyio.run(_serve)


if __name__ == "__main__":
    main()
