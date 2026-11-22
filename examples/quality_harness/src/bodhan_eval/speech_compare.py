"""Compare Bodhan ASR against a separately supplied known-good baseline clip."""

import argparse
import json
from difflib import SequenceMatcher
from pathlib import Path

from .calibration import score
from .checks import normalize


def compare_transcripts(parent, actual):
    expected, observed = (normalize(value).split() for value in (parent, actual))
    if not expected or not observed:
        return None
    extra = observed[len(expected) :]
    if extra and observed[: len(expected)] == expected and extra == expected[-len(extra) :]:
        return {"category": "speech_repetition", "evidence": {"repeated_suffix": extra}}
    if len(observed) < len(expected):
        prefix = expected[: len(observed)]
        similarity = SequenceMatcher(None, prefix, observed, autojunk=False).ratio()
        if similarity >= 0.6:
            return {
                "category": "speech_omission",
                "evidence": {"prefix_similarity": similarity, "baseline_tokens": len(expected), "observed_tokens": len(observed)},
            }
    return None


def read(path):
    return [json.loads(line) for line in (Path(path) / "items.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("parents", "asr-report", "offline-report", "out"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    parents = {(r["id"], r["lang"]): r for r in read(args.parents)}
    rows = read(args.offline_report)
    by_id = {(r["id"], r["lang"]): r for r in rows}
    comparisons = []
    splits = set()
    for observed in read(args.asr_report):
        key = (observed["id"], observed["lang"])
        row = by_id[key]
        splits.add(row.get("split"))
        parent = parents[row["parent_id"], row["lang"]]
        complete = all(r["checks"].get("bodhan_asr", {}).get("status") == "completed" and r.get("transcript") for r in (parent, observed))
        finding = compare_transcripts(parent["transcript"], observed["transcript"]) if complete else None
        if finding:
            row["flags"].append({**finding, "check": "baseline_asr", "severity": "major"})
        comparisons.append({"id": key[0], "lang": key[1], "assessed": bool(complete), "finding": finding})
    if len(splits) != 1 or not splits <= {"calibration", "evaluation"}:
        parser.error("ASR report must contain one explicit split")
    split = splits.pop()
    selected = [r for r in rows if r.get("split") == split and r.get("labels") is not None]
    report = {
        "split": split,
        "metrics": score(selected),
        "comparisons": comparisons,
        "note": "Paired synthetic evaluation requires baseline audio and ASR. This check does not localize in audio seconds.",
    }
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
