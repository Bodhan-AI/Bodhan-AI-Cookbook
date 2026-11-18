"""Write the run's outputs: artifacts/, result.md and report.json (trace.jsonl is written as the run goes)."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

HASH_PART = re.compile(r"^[0-9a-f]{8}$")
AI_AUDIO_NOTE = "Audio files are AI-generated speech (Bodhan Indic-Speak, fixed voice library; no voice cloning)."


def clean_name(path: Path) -> str:
    parts = path.name.split(".")
    return ".".join(p for p in parts if not HASH_PART.match(p)) or path.name


def copy_artifacts(paths: list[str], dest: Path) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    copied = []
    for a in paths:
        src = Path(a)
        if not src.is_file():
            continue
        name = clean_name(src)
        target = dest / name
        n = 2
        while target.exists():
            target = dest / f"{Path(name).stem}-{n}{''.join(Path(name).suffixes[-1:])}"
            n += 1
        shutil.copy2(src, target)
        copied.append(target)
    return copied


def write_result_md(out: Path, *, task: str, status: str, answer: str, reason: str, artifacts: list[Path],
                    assumptions: list[str], verification: list[dict], missed: list[dict], produced: list[str],
                    cost_inr: float, seconds: float) -> None:
    headline = {
        "completed": "✅ Completed",
        "partial": "⚠️ Partially completed",
        "impossible": "⛔ Not possible",
        "budget_exhausted": "⏹️ Stopped: budget exhausted",
        "failed": "❌ Failed",
    }.get(status, status)
    lines = ["# Result", "", f"**Task:** {task}", "", f"**Status:** {headline}", ""]
    if answer:
        lines += ["## Answer", "", answer.strip(), ""]
    if reason and status != "completed":
        lines += ["## Why", "", reason.strip(), ""]
    if artifacts:
        lines += ["## Artifacts", ""] + [f"- [{p.name}](artifacts/{p.name})" for p in artifacts] + [""]
        if any(p.suffix in {".mp3", ".wav"} for p in artifacts):
            lines += [f"> {AI_AUDIO_NOTE}", ""]
    elif status != "completed":
        lines += ["No artifacts were produced, so none are presented as results.", ""]
    if produced and status in {"budget_exhausted", "failed", "partial"}:
        lines += ["## Work done before stopping", ""] + [f"- {p}" for p in produced] + [""]
    if assumptions:
        lines += ["## Assumptions", ""] + [f"- {a}" for a in assumptions] + [""]
    if verification:
        lines += ["## Verification", ""] + [
            f"- {'✅' if c['passed'] else '❌'} {c['check']}{' — ' + c['artifact'] if c.get('artifact') else ''}: {c['detail']}"
            for c in verification] + [""]
    if missed:
        lines += ["## Missed targets", ""] + [f"- {m['target']}: got {m['actual']} ({m['why']})" for m in missed] + [""]
    lines += ["## Cost and time", "", f"- Bodhan cost: ₹{cost_inr:.4f}", f"- Wall-clock time: {seconds:.1f}s",
              "- Full step-by-step trace: [trace.jsonl](trace.jsonl); summary: [report.json](report.json)", ""]
    (out / "result.md").write_text("\n".join(lines), encoding="utf-8")


def write_report_json(out: Path, report: dict) -> None:
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
