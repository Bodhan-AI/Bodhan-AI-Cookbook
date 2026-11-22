"""End-to-end smoke test: the real CLI on the 33-second sample clip, against the hosted APIs.

It costs money (about Rs 0.5 per language) and needs keys, so it is opt-in:

    BODHAN_SMOKE=1 pytest tests/test_smoke_e2e.py            # Hindi
    BODHAN_SMOKE=1 BODHAN_SMOKE_LANGS=hi,mr pytest tests/test_smoke_e2e.py
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "sample_data" / "triangle_lesson_en_33s.mp4"

pytestmark = pytest.mark.skipif(os.environ.get("BODHAN_SMOKE") != "1",
                                reason="paid API run; set BODHAN_SMOKE=1 to enable")


def test_cli_dubs_the_sample_clip(tmp_path):
    langs = os.environ.get("BODHAN_SMOKE_LANGS", "hi").split(",")
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONIOENCODING": "utf-8"}
    p = subprocess.run([sys.executable, "-m", "bodhan_dub", "--input", str(SAMPLE), "--langs", ",".join(langs),
                        "--out", str(tmp_path)], env=env, capture_output=True, text=True, encoding="utf-8",
                       timeout=1800)
    assert p.returncode == 0, p.stderr[-3000:]

    rep = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert (tmp_path / "transcript.en.json").is_file()
    assert rep["english"]["asr_failed"] == 0 and rep["english"]["units"] >= 3
    for lang in langs:
        d = tmp_path / lang
        for name in (f"video.{lang}.mp4", f"subtitles.{lang}.srt", f"segments.{lang}.json"):
            assert (d / name).is_file() and (d / name).stat().st_size > 0, name
        t = rep["quality_targets"][lang]
        assert t["length_within_100ms"]["met"], t
        assert t["speech_minus16_lufs_pm1"]["met"], t
        assert t["speed_0.85_to_1.2"]["met"], t
        assert rep["per_language"][lang]["qa_mean_cer"] is not None
