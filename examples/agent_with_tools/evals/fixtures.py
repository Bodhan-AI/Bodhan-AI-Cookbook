"""Builds each case's input files in a temp folder. No media is committed: audio, image and PDF come from sample_data/."""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # examples/agent_with_tools


def build_inputs(case: dict, fixtures: dict, dest: Path) -> list[str]:
    """Create the case's input files under `dest` and return the paths to pass as --files."""
    dest.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    for spec in case.get("inputs", []):
        target = dest / spec["name"]
        if "text" in spec:
            target.write_text(spec["text"], encoding="utf-8")
        elif "copy" in spec:
            shutil.copyfile(ROOT / fixtures[spec["copy"]], target)
        elif spec.get("fake_video"):
            target.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 256)
        else:
            raise ValueError(f"unknown fixture spec: {spec}")
        paths.append(str(target))
    return paths + list(case.get("extra_paths", []))
