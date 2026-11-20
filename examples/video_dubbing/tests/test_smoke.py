"""End-to-end smoke test: the first 30 seconds of the demo video, dubbed into Hindi by the real pipeline.

Needs the Bodhan keys (read from .env) and is skipped without them; the LLM settings are optional. It costs about
INR 0.30 the first time; reruns hit the cache in tests/.smoke and cost nothing. Set SMOKE_VIDEO_URL
to use another video.
"""

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest
from dotenv import load_dotenv

HERE = Path(__file__).parent
load_dotenv(HERE.parent / ".env")

DEMO_URL = (
    "https://media.classroom.bodhan.ai/course_videos/board/ap_9_mathematics__ch06__period-01/English.mp4"
    "?Expires=1799836079&KeyName=classroom-media-signed-key&Signature=5kOByxlA7ocgBg7QmpegTU6loZk="
)
HAVE_KEYS = bool(
    os.environ.get("BODHAN_API_KEY")
    or all(os.environ.get(f"BODHAN_{m}_KEY") for m in ("TRANSCRIBE", "TRANSLATE", "SPEAK"))
)


@pytest.mark.skipif(not HAVE_KEYS, reason="needs Bodhan keys")
def test_30_second_clip_end_to_end():
    work = HERE / ".smoke"
    work.mkdir(exist_ok=True)
    source, clip, out = work / "source.mp4", work / "clip30.mp4", work / "out"
    if not source.exists():
        urllib.request.urlretrieve(os.environ.get("SMOKE_VIDEO_URL", DEMO_URL), source)
    if not clip.exists():
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(source), "-t", "30", "-c", "copy", str(clip)], check=True)

    run = subprocess.run([sys.executable, "-m", "bodhan_dub", "--input", str(clip), "--langs", "hi", "--out", str(out)],
                         capture_output=True, text=True, timeout=1800)
    assert run.returncode == 0, run.stderr[-3000:]

    report = json.loads((out / "report.json").read_text())
    assert report["status"] == {"hi": "ok"}
    for name in ("transcript.en.json", "subtitles.en.srt", "run.jsonl", "hi/video.hi.mp4", "hi/subtitles.hi.srt", "hi/segments.hi.json"):
        assert (out / name).exists(), name
    hi = report["languages"]["hi"]
    assert abs(hi["outputs"]["duration_error"]) <= 0.1
    assert hi["targets"]["coverage"]["dubbed"] == report["units"] > 0
    assert hi["targets"]["loudness"]["met"]
