"""Compare Indic-Speak voices or generation settings across two or more harness runs.

Run the harness once per setting on the same rows, for example:

    python -m bodhan_eval --dataset d.jsonl --langs hi --out runs/kavya --generate --voice hi=Kavya
    python -m bodhan_eval --dataset d.jsonl --langs hi --out runs/amit --generate --voice hi=Amit
    python -m bodhan_eval.compare runs/kavya runs/amit --out runs/voices.md

The report puts speech intelligibility (back-transcription error), pace, silence and speech
flags side by side per language, then lists the rows where the settings disagree.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

from .schema import SPEECH_CATEGORIES


def load_run(path: Path) -> tuple[str, dict[tuple[str, str], dict]]:
    """Read one report directory and name it by its generation settings."""
    summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
    config = summary.get("configuration", {})
    voices = config.get("voices") or {}
    style = config.get("speech_style")
    name = path.name + (f" ({', '.join(f'{lang}={voice}' for lang, voice in voices.items())}" + (f", {style}" if style else "") + ")" if voices else "")
    rows = {}
    for line in (path / "items.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        rows[(row["id"], row["lang"])] = row
    return name, rows


def mean(values: list[float]) -> float | None:
    """Average observed values, leaving an empty cohort unmeasured."""
    return round(statistics.fmean(values), 4) if values else None


def speech_metrics(rows: list[dict]) -> dict:
    """Summarize the speech evidence of the rows that have audio and a completed alignment."""
    audio = [row for row in rows if row["checks"].get("signal", {}).get("status") == "ok"]
    aligned = [row["checks"]["speech_alignment"] for row in audio if row["checks"].get("speech_alignment", {}).get("status") == "ok"]
    flags = {category: sum(any(flag["category"] == category for flag in row["flags"]) for row in audio) for category in SPEECH_CATEGORIES}
    return {
        "clips": len(audio),
        "aligned": len(aligned),
        "mean_cer": mean([value["cer"] for value in aligned if "cer" in value]),
        "mean_repeat_adjusted_cer": mean([value["repeat_adjusted_cer"] for value in aligned if "repeat_adjusted_cer" in value]),
        "mean_duration_s": mean([row["checks"]["signal"]["duration_ms"] / 1000 for row in audio]),
        "mean_script_chars_per_s": mean([value["script_characters_per_second"] for value in aligned if "script_characters_per_second" in value]),
        "mean_silence_ratio": mean([row["checks"]["signal"]["silence_ratio"] for row in audio]),
        "clips_with_speech_flags": sum(any(flag["category"] in SPEECH_CATEGORIES for flag in row["flags"]) for row in audio),
        "flags_by_category": flags,
    }


def compare(paths: list[Path]) -> tuple[str, dict]:
    """Build the side-by-side markdown and its JSON form."""
    runs = [load_run(path) for path in paths]
    languages = sorted({lang for _, rows in runs for _, lang in rows})
    data = {name: {lang: speech_metrics([row for (_, row_lang), row in rows.items() if row_lang == lang]) for lang in languages} for name, rows in runs}
    lines = [
        "# Speech setting comparison",
        "",
        "Lower error and fewer speech flags are better. Error is the character error rate of the Bodhan back-transcript against the script.",
        "",
    ]
    measures = (
        ("Clips", "clips"),
        ("Mean CER", "mean_cer"),
        ("Mean CER without explained repeats", "mean_repeat_adjusted_cer"),
        ("Mean duration (s)", "mean_duration_s"),
        ("Script characters per second", "mean_script_chars_per_s"),
        ("Mean quiet share", "mean_silence_ratio"),
        ("Clips with speech flags", "clips_with_speech_flags"),
    )
    for lang in languages:
        lines.extend([f"## {lang}", "", "| Measure | " + " | ".join(name for name, _ in runs) + " |", "| --- |" + " ---: |" * len(runs)])
        for label, key in measures:
            values = [data[name][lang][key] for name, _ in runs]
            lines.append(f"| {label} | " + " | ".join("n/a" if value is None else str(value) for value in values) + " |")
        for category in SPEECH_CATEGORIES:
            lines.append(f"| {category} flags | " + " | ".join(str(data[name][lang]["flags_by_category"][category]) for name, _ in runs) + " |")
        lines.append("")
    shared = set.intersection(*(set(rows) for _, rows in runs))
    disagreements = []
    for key in sorted(shared):
        found = [sorted({flag["category"] for flag in rows[key]["flags"] if flag["category"] in SPEECH_CATEGORIES}) for _, rows in runs]
        if any(value != found[0] for value in found[1:]):
            disagreements.append((key, found))
    lines.extend(["## Rows where the settings disagree", ""])
    if not disagreements:
        lines.append("All shared rows have the same speech flags.")
    for (item_id, lang), found in disagreements[:40]:
        lines.append(f"- {item_id} [{lang}]: " + "; ".join(f"{name}: {', '.join(value) or 'clean'}" for (name, _), value in zip(runs, found, strict=True)))
    return "\n".join(lines) + "\n", {"runs": [name for name, _ in runs], "languages": data, "disagreements": len(disagreements)}


def main() -> int:
    """Write the comparison report and its JSON next to it."""
    parser = argparse.ArgumentParser(description="Compare speech quality across harness runs that used different Indic-Speak settings.")
    parser.add_argument("runs", nargs="+", type=Path, help="two or more report directories")
    parser.add_argument("--out", type=Path, required=True, help="markdown file to write; JSON is written beside it")
    args = parser.parse_args()
    if len(args.runs) < 2:
        print("bodhan_eval.compare: give at least two report directories", file=sys.stderr)
        return 2
    try:
        markdown, data = compare(args.runs)
    except (OSError, ValueError, KeyError) as error:
        print(f"bodhan_eval.compare: {error}", file=sys.stderr)
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(markdown, encoding="utf-8", newline="\n")
    args.out.with_suffix(".json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
