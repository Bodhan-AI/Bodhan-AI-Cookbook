"""Entrypoint for ``python -m bodhan_agent`` (autonomous task agent CLI)."""
from __future__ import annotations

import sys

from src.bodhan_agent.__main__ import main


def run(argv: list[str] | None = None) -> int:
    """Run the task agent and return the process exit code."""
    return main(argv)


if __name__ == "__main__":
    sys.exit(run())
