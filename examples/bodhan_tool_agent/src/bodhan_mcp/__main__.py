"""`python -m bodhan_mcp` : stdio MCP server. Logs go to stderr only (stdout carries the protocol)."""

from __future__ import annotations

import logging
import sys

from bodhan_core.runtime import Runtime

from .server import build_server


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    rt = Runtime.create()  # keys from env / .env; work dir from BODHAN_WORK_DIR; optional BODHAN_MAX_COST_INR cap
    logging.getLogger("bodhan_mcp").info("work dir: %s", rt.work_dir)
    build_server(rt).run(transport="stdio")


if __name__ == "__main__":
    main()
