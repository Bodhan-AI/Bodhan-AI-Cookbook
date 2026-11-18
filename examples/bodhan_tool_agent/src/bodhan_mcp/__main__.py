from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.tracers.langchain import wait_for_all_tracers

from bodhan_core import Run
from bodhan_mcp.server import create_server


async def _serve() -> None:
    load_dotenv()
    workspace = Path(os.getenv("BODHAN_WORKSPACE", ".")).resolve()
    run = Run([workspace], os.getenv("BODHAN_OUTPUT_DIR", "./out"),
              max_steps=int(os.getenv("BODHAN_MCP_MAX_STEPS", "1000")),
              max_cost_inr=float(os.getenv("BODHAN_MCP_MAX_COST_INR", "100")))
    try:
        await create_server(run).run_stdio_async()
    finally:
        await run.close()
        wait_for_all_tracers()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
