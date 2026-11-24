"""Unit tests for the HTML sync reader."""

from __future__ import annotations

import json
from pathlib import Path

from bodhan_lesson.reader import (
    build_cues,
    build_reader_metrics,
    render_reader_html,
    write_reader_for_script,
    write_readers_for_speaks,
)


def _sample_script() -> dict:
    return {
        "lang": "hi",
        "lesson_mp3": "lesson.hi.mp3",
        "sections": [
            {
                "id": "sec_001",
                "title": "अध्याय 6",
                "text_for_tts": "अध्याय 6",
                "units": [],
                "start_s": 0.0,
                "duration_s": 1.0,
                "speak_status": "ok",
            },
            {
                "id": "sec_002",
                "title": "परिचय",
                "text_for_tts": "परिचय\n\nपहला।\n\nदूसरा।",
                "units": [
                    {"id": "u1", "kind": "prose", "text_for_tts": "aaa"},
                    {"id": "u2", "kind": "prose", "text_for_tts": "bbbbbb"},
                ],
                "start_s": 1.0,
                "duration_s": 9.0,
                "speak_status": "ok",
            },
        ],
    }


def test_build_cues_section_and_units() -> None:
    cues = build_cues(_sample_script())
    assert cues[0]["id"] == "sec_001"
    assert cues[0]["start"] == 0.0
    assert cues[0]["end"] == 1.0
    # Title + two units inside sec_002
    unit_cues = [c for c in cues if c["section_id"] == "sec_002"]
    assert len(unit_cues) == 3
    assert unit_cues[0]["kind"] == "title"
    assert unit_cues[0]["start"] == 1.0
    assert unit_cues[-1]["end"] == 10.0
    # Longer unit gets a larger share than the short one.
    short = next(c for c in unit_cues if c["id"].endswith("u1"))
    long = next(c for c in unit_cues if c["id"].endswith("u2"))
    assert (long["end"] - long["start"]) > (short["end"] - short["start"])


def test_build_cues_skips_untimed() -> None:
    script = _sample_script()
    script["sections"].append(
        {
            "id": "sec_x",
            "title": "skip me",
            "text_for_tts": "x",
            "units": [],
            "speak_status": "skipped",
        }
    )
    cues = build_cues(script)
    assert all(c["section_id"] != "sec_x" for c in cues)


def test_render_reader_html_embeds_cues_and_escapes() -> None:
    cues = [
        {
            "id": "sec_001",
            "section_id": "sec_001",
            "kind": "section",
            "title": "T",
            "text": "A <B> & C",
            "start": 0.0,
            "end": 1.0,
            "cer": 0.12,
        }
    ]
    doc = render_reader_html(
        lang="hi",
        audio_name="lesson.hi.mp3",
        cues=cues,
        metrics={
            "lang": "hi",
            "mean_cer": 0.1065,
            "audio_s": 300.0,
            "bodhan_inr_total": 9.08,
            "llm_usd_total": 0.0034,
            "speak_s": 327.9,
            "source": "report",
        },
    )
    assert "lesson.hi.mp3" in doc
    assert "A &lt;B&gt; &amp; C" in doc
    assert "const CUES =" in doc
    assert 'data-id="sec_001"' in doc
    assert "Mean CER" in doc
    assert "0.106" in doc
    assert "₹9.08" in doc
    assert "CER 0.120" in doc


def test_build_reader_metrics_from_report() -> None:
    script = _sample_script()
    script["qa_summary"] = {"mean_cer": 0.2, "status": "ok", "ok": 2, "failed": 0}
    script["voice"] = "Kavya"
    report = {
        "page_count": 2,
        "qa": {"mean_cer": 0.145},
        "languages": {
            "hi": {
                "status": "qa_ok",
                "voice": "Kavya",
                "timings": {"translate_s": 1, "narrate_s": 2, "speak_s": 100, "qa_s": 40},
                "qa": {"mean_cer": 0.1065},
            }
        },
        "cost": {
            "bodhan_inr": {"total": 9.08},
            "llm_usd": {"total_usd": 0.0034},
        },
    }
    metrics = build_reader_metrics("hi", script, report)
    assert metrics["mean_cer"] == 0.1065
    assert metrics["bodhan_inr_total"] == 9.08
    assert metrics["lang_wall_s"] == 143.0
    assert metrics["source"] == "report"


def test_write_reader_for_script(tmp_path: Path) -> None:
    out = tmp_path
    lang_dir = out / "hi"
    lang_dir.mkdir()
    (lang_dir / "lesson.hi.mp3").write_bytes(b"ID3fake")
    script = _sample_script()
    script["qa_summary"] = {"mean_cer": 0.11, "status": "ok", "ok": 2, "failed": 0}
    script_path = lang_dir / "narration_script.hi.json"
    script_path.write_text(
        json.dumps(script, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (out / "report.json").write_text(
        json.dumps(
            {
                "page_count": 2,
                "qa": {"mean_cer": 0.15},
                "languages": {
                    "hi": {
                        "status": "qa_ok",
                        "timings": {
                            "translate_s": 1.0,
                            "narrate_s": 2.0,
                            "speak_s": 50.0,
                            "qa_s": 10.0,
                        },
                        "qa": {"mean_cer": 0.11},
                        "voice": "Kavya",
                    }
                },
                "cost": {
                    "bodhan_inr": {"total": 9.08},
                    "llm_usd": {"total_usd": 0.0034},
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    result = write_reader_for_script(script_path)
    assert result["status"] == "ok"
    html_path = Path(result["path"])
    assert html_path.is_file()
    html = html_path.read_text(encoding="utf-8")
    assert "sec_001" in html
    assert "Mean CER" in html
    assert "₹9.08" in html
    assert "Speak time" in html
    data = json.loads(script_path.read_text(encoding="utf-8"))
    assert data.get("reader_cue_count") == result["cue_count"]
    assert data.get("reader_metrics", {}).get("bodhan_inr_total") == 9.08


def test_write_readers_for_speaks(tmp_path: Path) -> None:
    lang_dir = tmp_path / "hi"
    lang_dir.mkdir()
    (lang_dir / "lesson.hi.mp3").write_bytes(b"ID3")
    script_path = lang_dir / "narration_script.hi.json"
    script_path.write_text(
        json.dumps(_sample_script(), ensure_ascii=False) + "\n", encoding="utf-8"
    )
    rows = write_readers_for_speaks(
        tmp_path,
        [{"lang": "hi", "status": "ok", "script_path": str(script_path)}],
    )
    assert rows[0]["status"] == "ok"
    assert (lang_dir / "reader.html").is_file()
