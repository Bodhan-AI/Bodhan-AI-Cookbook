#!/usr/bin/env python3
"""Check the demo media folder with probe_media only: free, no model calls.

    python scripts/check_media.py [--strict]

Without --strict, missing files are reported and the exit code stays 0, so the
check is safe to run in CI before any media exists.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "demo_tasks" / "media.manifest.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--strict", action="store_true", help="missing media is a failure")
    args = parser.parse_args(argv)

    from bodhan_mcp.tools.probe import probe_media

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    root = Path(os.environ.get(manifest["env_override"]) or ROOT / "demo_tasks")
    wanted = [root / entry["path"] for entry in manifest["files"]]
    missing = [p for p in wanted if not p.exists()]
    present = [p for p in wanted if p.exists()]
    inventory = probe_media([str(p) for p in present]) if present else None
    by_path = {item.path: item for item in inventory.items} if inventory else {}

    for path in wanted:
        item = by_path.get(str(path.resolve()))
        if item is None:
            print(f"MISSING  {path.relative_to(root)}")
            continue
        detail = f"{item.duration_s:.1f}s" if item.duration_s else f"{item.pages} pages" if item.pages else f"{item.size_bytes} bytes"
        print(f"{item.kind:<7}  {path.relative_to(root):<28} {detail:<10} {' '.join(item.warnings)}")
    if inventory:
        print(f"\nEstimated transcription cost for all audio: Rs {sum(i.est_asr_cost_inr or 0 for i in inventory.items):.2f}")
    print(f"{len(present)} of {len(wanted)} present; {len(missing)} missing")
    return 1 if (args.strict and missing) else 0


if __name__ == "__main__":
    raise SystemExit(main())
