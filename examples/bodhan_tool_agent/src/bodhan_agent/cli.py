#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    parser = argparse.ArgumentParser(
        description=\"Unattended CLI task agent for Bodhan AI\"
    )
    parser.add_argument(\"--task\", required=True, help=\"Task description\")
    parser.add_argument(\"--files\", nargs=\"*\", help=\"Input files\")
    parser.add_argument(\"--out\", default=\"./out\", help=\"Output directory\")
    parser.add_argument(
        \"--max-steps\", type=int, default=30, help=\"Maximum planning steps\"
    )
    parser.add_argument(
        \"--max-cost-inr\", type=float, default=20.0, help=\"Maximum cost in INR\"
    )
    args = parser.parse_args()

    print(f\"Task: {args.task}\")
    print(f\"Files: {args.files or 'none'}\")
    print(f\"Output: {args.out}\")
    print(f\"Max steps: {args.max_steps}\")
    print(f\"Max cost: ?{args.max_cost_inr}\")
    return 0


if __name__ == \"__main__\":
    sys.exit(main())
