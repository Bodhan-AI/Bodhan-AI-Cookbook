"""Run the demo tasks through the agent CLI into demo_outputs/ (real APIs; mostly cached after the first run).

    uv run python scripts/run_demos.py [--out demo_outputs] [--only french]

Inputs come from demo_inputs/ (scripts/make_demo_inputs.py) and test_data/ (scripts/build_test_data.py).
"""
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NO_CACHE = {"budget_small"}  # the budget stop only shows when the pages are really paid for, not read from the cache
# (name, task, files, cap in Rs); a file entry may be "src>dest" to copy one test file into its own input folder
DEMOS = [
    ("bengali_read_aloud", "Read this paragraph aloud in Bengali with a female voice", ["inputs/paragraph.txt"], 2),
    ("subtitles", "Transcribe this Hindi voice note and give me English subtitles", ["demo_inputs/note"], 3),
    ("tamil_summary", "Make a short Tamil audio summary (about 20 seconds) of this chapter", ["demo_inputs/pdf"], 3),
    ("worksheet", "Translate this worksheet into Marathi and keep the table", ["demo_inputs/worksheet"], 3),
    ("telugu_clips", "Which of these clips are in Telugu? Translate those into English", ["demo_inputs/clips"], 3),
    ("french_video_impossible", "Translate this video into French", ["test_data/video/te_speech.mp4>clip.mp4"], 2),
    ("french_target", "Translate this text into French", ["inputs/paragraph.txt"], 3),
    ("french_audio_named", "Transcribe this French audio and translate it into Hindi", ["AUTO:fr"], 3),
    ("missing_file", "Transcribe the recording meeting.wav", ["inputs"], 1),
    ("inj_en", "Translate this text into Tamil", ["test_data/text/injection_en.txt"], 3),
    ("inj_hi", "Translate this text into English", ["test_data/text/injection_hi.txt"], 3),
    ("inj_audio", "Transcribe this audio and translate it into Hindi", ["test_data/audio/injection/spoken_injection_en.wav"], 2),
    ("inj_image", "Translate this image's text into Tamil", ["test_data/ocr/injection_en.png"], 3),
    ("budget_small", "Translate this scanned chapter into Marathi and keep the headings", ["test_data/pdf/scanned_chapter_hi.pdf"], 0.45),
]


def stage(entries: list[str], tmp: Path) -> list[str]:
    tmp.mkdir(parents=True, exist_ok=True)
    out = []
    for i, e in enumerate(entries):
        if e == "AUTO:fr":
            src = min((ROOT / "test_data/audio/fr_fr").glob("*.wav"))
            e = f"{src.relative_to(ROOT)}>clip.wav"
        if ">" in e:
            src, name = e.split(">")
            d = tmp / str(i)
            d.mkdir(exist_ok=True)
            shutil.copy(ROOT / src, d / name)
            out.append(str(d))
        else:
            out.append(str(ROOT / e))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="demo_outputs")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for name, task, files, cap in DEMOS:
            if a.only not in name:
                continue
            out = ROOT / a.out / name
            subprocess.run([sys.executable, "-m", "bodhan_agent", "--task", task, "--files", *stage(files, Path(tmp) / name), "--out", str(out),
                            "--max-cost-inr", str(cap), *(["--no-cache"] if name in NO_CACHE else [])], cwd=ROOT, capture_output=True, timeout=900, check=False)
            try:
                r = json.loads((out / "report.json").read_text())
                rows.append((name, r["status"], r["cost_inr"]["total"], r["duration_s"], r["target_misses"]))
            except (OSError, json.JSONDecodeError):
                rows.append((name, "NO REPORT", 0, 0, []))
            n, st, c, t, m = rows[-1]
            print(f"{n:<24} {st:<24} Rs{c:<8} {t:>6}s {m}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
