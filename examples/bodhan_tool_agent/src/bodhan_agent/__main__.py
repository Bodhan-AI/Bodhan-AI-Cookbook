"""CLI entrypoint for running python -m bodhan_agent."""

from __future__ import annotations

import sys
from bodhan_agent.cli import main

if __name__ == "__main__":
    sys.exit(main())
