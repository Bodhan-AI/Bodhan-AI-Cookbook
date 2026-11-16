"""Run the demo tasks end to end and check the expected outcomes.

    python scripts/run_demo_tasks.py            # mock run (default, no keys)
    python scripts/run_demo_tasks.py --live     # real Bodhan API (needs keys)

Each task runs in its own folder under outputs/demo-<n>-<slug>/ with
result.md, artifacts/, trace.jsonl and report.json. The script exits 1
if any task's outcome differs from what is expected (including the
impossible French task, which must fail honestly).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "sample_data"
OUT_ROOT = ROOT / "outputs"

TASKS = [
    {
        "slug": "voice-note-to-english",
        "task": "Transcribe the Hindi voice note and translate it into English.",
        "files": SAMPLE / "voice_note_hi.wav",
        "expect": "completed",
        "checks": [
            ("artifacts/transcript.txt", "सुजल"),
            ("artifacts/translation.en.md", "Sujal"),
        ],
    },
    {
        "slug": "paragraph-to-hindi-speech",
        "task": "Translate the paragraph into Hindi and read it aloud.",
        "files": SAMPLE / "paragraph.txt",
        "expect": "completed",
        "checks": [
            ("artifacts/translation.hi.md", "नमस्ते"),
            ("artifacts/speech.hi.wav", None),
        ],
    },
    {
        "slug": "chapter-to-tamil-speech",
        "task": (
            "Read the chapter PDF, summarize it, translate the summary "
            "into Tamil and read it aloud with a female voice."
        ),
        "files": SAMPLE / "chapter.pdf",
        "expect": "completed",
        "checks": [
            ("artifacts/source.md", "Lines and Angles"),
            ("artifacts/translation.ta.md", "கோடுகள்"),
            ("artifacts/speech.ta.wav", None),
        ],
    },
    {
        "slug": "telugu-clips-to-english",
        "task": (
            "Listen to the clips and tell me which ones are Telugu; "
            "translate those into English."
        ),
        "files": SAMPLE / "clips",
        "expect": "completed",
        "checks": [
            ("artifacts/translation.en.md", "Hyderabad"),
            ("artifacts/translation.en.md", "mother tongue"),
        ],
    },
    {
        "slug": "worksheet-to-marathi",
        "task": "Read the worksheet image and translate the table into Marathi.",
        "files": SAMPLE / "worksheet.png",
        "expect": "completed",
        "checks": [
            ("artifacts/source.md", "Asha"),
            ("artifacts/translation.mr.md", "आशा"),
        ],
    },
    {
        "slug": "video-to-french-impossible",
        "task": "Translate the video into French.",
        "files": SAMPLE / "sample_video.mp4",
        "expect": "failed",
        "error_code": "unsupported_language",
        "checks": [],
    },
    {
        "slug": "voice-note-subtitles",
        "task": (
            "Transcribe the Hindi voice note and translate it into English "
            "with subtitles."
        ),
        "files": SAMPLE / "voice_note_hi.wav",
        "expect": "completed",
        "checks": [
            ("artifacts/subtitles.en.srt", "00:00:00,000 -->"),
            ("artifacts/subtitles.en.srt", "Sujal"),
        ],
    },
]


def run_one(task: dict, out_dir: Path, mock: bool) -> tuple[bool, dict]:
    cmd = [
        sys.executable, "-m", "bodhan_agent",
        "--task", task["task"],
        "--files", str(task["files"]),
        "--out", str(out_dir),
    ]
    if mock:
        cmd.append("--mock")
    env = dict(__import__("os").environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=ROOT)
    report_path = out_dir / "report.json"
    if not report_path.exists():
        return False, {"error": f"no report.json; stderr: {proc.stderr[-500:]}"}
    report = json.loads(report_path.read_text(encoding="utf-8"))
    ok = report["status"] == task["expect"]
    if task.get("error_code"):
        ok = ok and report.get("error", {}).get("code") == task["error_code"]
    for rel, needle in task["checks"]:
        path = out_dir / rel
        if not path.exists():
            ok = False
            report.setdefault("check_failures", []).append(f"missing {rel}")
        elif needle is not None and needle not in path.read_text(
            encoding="utf-8", errors="replace"
        ):
            ok = False
            report.setdefault("check_failures", []).append(
                f"{rel} does not contain {needle!r}"
            )
    return ok, report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the demo tasks.")
    parser.add_argument("--live", action="store_true",
                        help="Use the real Bodhan API (needs keys in .env).")
    args = parser.parse_args()
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    results = []
    for i, task in enumerate(TASKS, start=1):
        out_dir = OUT_ROOT / f"demo-{i}-{task['slug']}"
        ok, report = run_one(task, out_dir, mock=not args.live)
        status = "PASS" if ok else "FAIL"
        cost = report.get("total_cost_inr", "?")
        print(
            f"[{status}] demo-{i} {task['slug']}: "
            f"status={report.get('status')} cost=Rs {cost} -> {out_dir}"
        )
        if not ok:
            detail = report.get("error") or report.get("check_failures")
            print(f"       detail: {json.dumps(detail)}")
        results.append(ok)
    passed = sum(results)
    print(f"\n{passed}/{len(TASKS)} demo tasks passed.")
    return 0 if passed == len(TASKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
