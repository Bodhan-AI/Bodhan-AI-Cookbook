"""
Output generator — creates result.md and manages the artifacts directory.
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def write_result_md(
    output_dir: Path,
    task: str,
    status: str,
    summary: str,
    artifacts: list[dict[str, Any]],
    assumptions: list[str] | None = None,
    errors: list[str] | None = None,
) -> Path:
    """
    Write result.md summarizing the task execution.

    Args:
        output_dir: The output directory (contains artifacts/, trace.jsonl, etc.)
        task: The original task description.
        status: Final status ('completed', 'partial', 'failed', 'impossible').
        summary: Human-readable summary of what was done.
        artifacts: List of dicts with 'name', 'path', 'description'.
        assumptions: Assumptions made during execution.
        errors: Errors encountered.

    Returns:
        Path to the written result.md.
    """
    result_path = output_dir / "result.md"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        f"# Task Result",
        f"",
        f"**Task:** {task}",
        f"**Status:** {status}",
        f"**Timestamp:** {timestamp}",
        f"",
        f"---",
        f"",
        f"## Summary",
        f"",
        summary,
        f"",
    ]

    # Artifacts section
    if artifacts:
        lines.extend([
            f"## Artifacts",
            f"",
        ])
        for art in artifacts:
            name = art.get("name", "unnamed")
            desc = art.get("description", "")
            path = art.get("path", "")
            lines.append(f"- **{name}**: {desc}")
            if path:
                lines.append(f"  - File: `{path}`")
        lines.append("")

    # Assumptions section
    if assumptions:
        lines.extend([
            f"## Assumptions",
            f"",
        ])
        for a in assumptions:
            lines.append(f"- {a}")
        lines.append("")

    # Errors section
    if errors:
        lines.extend([
            f"## Errors Encountered",
            f"",
        ])
        for e in errors:
            lines.append(f"- {e}")
        lines.append("")

    # Links to other output files
    lines.extend([
        f"## Output Files",
        f"",
        f"- `result.md` — This file",
        f"- `artifacts/` — Generated files (audio, text, subtitles, etc.)",
        f"- `trace.jsonl` — Step-by-step execution trace",
        f"- `report.json` — Machine-readable execution report",
    ])

    result_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Result written to {result_path}")
    return result_path


def setup_output_dir(base_dir: str | Path) -> Path:
    """
    Create the output directory structure:
    out/
      result.md
      artifacts/
      trace.jsonl
      report.json
    """
    out = Path(base_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "artifacts").mkdir(exist_ok=True)
    return out


def save_artifact(
    output_dir: Path,
    name: str,
    source_path: str | Path | None = None,
    content: str | bytes | None = None,
    description: str = "",
) -> dict[str, Any]:
    """
    Save an artifact to the artifacts directory.

    Either copies from source_path or writes content directly.

    Returns:
        dict with 'name', 'path', 'description', 'size_bytes'.
    """
    artifacts_dir = output_dir / "artifacts"
    artifacts_dir.mkdir(exist_ok=True)
    dest = artifacts_dir / name

    if source_path:
        src = Path(source_path)
        if src.exists():
            shutil.copy2(str(src), str(dest))
        else:
            raise FileNotFoundError(f"Artifact source not found: {source_path}")
    elif content is not None:
        if isinstance(content, bytes):
            dest.write_bytes(content)
        else:
            dest.write_text(content, encoding="utf-8")
    else:
        raise ValueError("Either source_path or content must be provided")

    return {
        "name": name,
        "path": str(dest),
        "description": description,
        "size_bytes": dest.stat().st_size,
    }
