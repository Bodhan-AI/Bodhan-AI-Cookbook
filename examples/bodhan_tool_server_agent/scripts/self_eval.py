"""Self-evaluation: run a small task suite through the agent CLI and report a pass rate.

    uv run python scripts/self_eval.py [--out eval_runs] [--only french]

Each case lists the accepted statuses and a file that must exist when it completes. Uses real APIs
(about Rs 1-2 once the cache is warm); set LLM_* and BODHAN_* in .env first.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DONE = {"completed", "completed_with_warnings"}
# (name, task, files, cap, accepted statuses, artifact suffix expected)
CASES = [
    ("subtitles", "Transcribe this Hindi voice note and give me English subtitles", "demo_inputs/note", 3, DONE, ".srt"),
    ("read_aloud", "Read this paragraph aloud in Bengali with a female voice", "inputs/paragraph.txt", 2, DONE, ".mp3"),
    ("worksheet", "Translate this worksheet into Marathi and keep the table", "demo_inputs/worksheet", 3, DONE, ".md"),
    ("telugu_clips", "Which of these clips are in Telugu? Translate those into English", "demo_inputs/clips", 3, DONE, ".txt"),
    ("french_text", "Translate this text into French", "inputs/paragraph.txt", 3, {"unsupported"}, None),
    ("missing_file", "Transcribe the recording meeting.wav", "inputs", 1, {"unsupported"}, None),
    ("budget_stop", "Translate this scanned chapter into Marathi and keep the headings", "test_data/pdf/scanned_chapter_hi.pdf", 0.45,
     {"budget_exhausted"}, None),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="eval_runs")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    rows = []
    for name, task, files, cap, ok, suffix in CASES:
        if a.only not in name or not (ROOT / files).exists():
            continue
        out = ROOT / a.out / name
        subprocess.run([sys.executable, "-m", "bodhan_agent", "--task", task, "--files", str(ROOT / files), "--out", str(out),
                        "--max-cost-inr", str(cap)], cwd=ROOT, capture_output=True, timeout=900, check=False)
        try:
            r = json.loads((out / "report.json").read_text())
        except (OSError, json.JSONDecodeError):
            rows.append({"case": name, "pass": False, "status": "no report"})
            continue
        has = suffix is None or any(p.suffix == suffix for p in (out / "artifacts").glob("*"))
        spent_ok = r["cost_inr"]["total"] <= cap
        rows.append({"case": name, "status": r["status"], "cost_inr": r["cost_inr"]["total"], "seconds": r["duration_s"],
                     "pass": r["status"] in ok and has and spent_ok})
        print(f"{'PASS' if rows[-1]['pass'] else 'FAIL'} {name:14} {r['status']:24} Rs{r['cost_inr']['total']}  {r['duration_s']}s")
    rate = sum(x["pass"] for x in rows) / max(len(rows), 1)
    print(f"pass rate {rate:.0%} ({sum(x['pass'] for x in rows)}/{len(rows)})")
    (ROOT / a.out).mkdir(exist_ok=True)
    (ROOT / a.out / "summary.json").write_text(json.dumps({"pass_rate": rate, "cases": rows}, indent=1))
    return 0 if rate == 1 else 1


if __name__ == "__main__":
    sys.exit(main())
