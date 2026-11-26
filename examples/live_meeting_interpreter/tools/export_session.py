"""Rebuild a session's Markdown transcript and SRT subtitles from its JSONL record.

    python -m tools.export_session out/sessions/2026-11-26_213000.jsonl
    python -m tools.export_session --latest

The app does this itself on exit; use this after a crash, or to re-export older sessions.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.session import export


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a session record to Markdown and SRT")
    parser.add_argument("jsonl", nargs="*", type=Path)
    parser.add_argument("--latest", action="store_true", help="the newest file in --dir")
    parser.add_argument("--dir", type=Path, default=Path("out/sessions"))
    args = parser.parse_args()
    paths = list(args.jsonl)
    if args.latest:
        found = sorted(args.dir.glob("*.jsonl"))
        if not found:
            sys.exit(f"No sessions in {args.dir}")
        paths.append(found[-1])
    if not paths:
        parser.error("give a .jsonl file or --latest")
    for path in paths:
        for out in export(path):
            print(out)


if __name__ == "__main__":
    main()
