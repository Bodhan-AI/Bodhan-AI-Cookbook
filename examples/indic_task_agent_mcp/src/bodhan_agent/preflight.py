"""Deterministic checks before any paid call: inputs exist, what they are, and whether the task is possible.

#Plan
- Resolve --files (a folder, one file, or a comma-separated list). A path that does not exist ends the run as
  impossible before the LLM or any model is called.
- Spot languages named in the task. "into French" style requests for a language no Bodhan model supports end the
  run as impossible, deterministically and identically every time; other mentions are passed to the planner.
- Pull a duration target ("two-minute", "90 seconds") out of the task so verification can check it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from bodhan_mcp import langs

WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
            "half": 0.5, "a": 1, "an": 1}


@dataclass
class Preflight:
    input_root: Path | None
    files: list[Path]
    missing: list[str]
    supported_mentioned: list[str]
    unsupported_mentioned: list[str]
    unsupported_targets: list[str]
    duration_target_s: float | None
    impossible_reason: str | None = None
    notes: list[str] = field(default_factory=list)


def resolve_files(spec: str | None) -> tuple[Path | None, list[Path], list[str]]:
    if not spec:
        return None, [], []
    parts = [s.strip() for s in spec.split(",") if s.strip()]
    files, missing, roots = [], [], []
    for part in parts:
        p = Path(part).expanduser()
        if not p.exists():
            missing.append(part)
            continue
        p = p.resolve()
        if p.is_dir():
            roots.append(p)
            files += sorted(f for f in p.rglob("*") if f.is_file() and not f.name.startswith("."))
        else:
            roots.append(p.parent)
            files.append(p)
    root = roots[0] if len(roots) == 1 else (Path(_common(roots)) if roots else None)
    return root, files, missing


def _common(paths: list[Path]) -> str:
    import os

    return os.path.commonpath([str(p) for p in paths])


def duration_target(task: str) -> float | None:
    m = re.search(r"\b(\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|half|a|an)[\s-]*(minutes?|mins?|seconds?|secs?)\b",
                  task.lower())
    if not m:
        return None
    raw, unit = m.group(1), m.group(2)
    value = float(raw) if raw[0].isdigit() else WORD_NUM[raw]
    if raw in ("a", "an") and not re.search(r"\b(a|an)\s+(minute|second)", task.lower()):
        return None
    return value * (60 if unit.startswith("min") else 1)


def run_preflight(task: str, files_spec: str | None, max_cost_inr: float) -> Preflight:
    root, files, missing = resolve_files(files_spec)
    supported, unsupported = langs.mentioned_languages(task)
    targets = [name for name in unsupported
               if re.search(rf"\b(?:into|to|in)\s+{name.lower()}\b", task.lower())]
    pf = Preflight(root, files, missing, supported, unsupported, targets, duration_target(task))
    if missing:
        pf.impossible_reason = f"Input not found: {', '.join(missing)}. Nothing was processed."
    elif targets:
        pf.impossible_reason = (f"{', '.join(targets)} is not supported by any Bodhan model. Supported target languages: "
                                f"{', '.join(langs.name_of(c) for c in sorted(langs.TRANSLATE) if '-' not in c)}.")
    elif max_cost_inr <= 0:
        pf.impossible_reason = "The cost budget is zero, so no model can be called."
    if files_spec and not files and not missing:
        pf.notes.append("The input folder is empty.")
    if unsupported and not targets:
        pf.notes.append(f"The task mentions {', '.join(unsupported)}, which no Bodhan model supports.")
    return pf
