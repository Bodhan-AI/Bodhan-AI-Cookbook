"""
CLI entry point for running the Bodhan task agent:
    python -m bodhan_agent --task "..." --files path/ --out ./out
"""

from .cli import main

if __name__ == "__main__":
    main()
