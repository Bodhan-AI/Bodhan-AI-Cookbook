from pathlib import Path

from app.pipeline.common import Caption
from app.session import Entry, RecordingUI, SessionRecorder, load, resolve, to_markdown, to_srt
from tests.test_outgoing import FakeUI

META = {"my_language": "en", "their_language": "hi", "asr_backend": "hosted", "mode": "both"}


def recorder(tmp_path: Path, now: list[float]) -> SessionRecorder:
    return SessionRecorder(tmp_path / "s.jsonl", META, clock=lambda: now[0])


def test_only_final_captions_are_recorded_with_session_times(tmp_path):
    now = [100.0]
    rec = recorder(tmp_path, now)
    ui = FakeUI()
    shown = RecordingUI(ui, rec)
    shown.show_caption(Caption("in", "आधा", "half …", 1, live=True, start=101.0, end=103.0))
    shown.show_caption(Caption("in", "नमस्ते", "", 1, pending=True, start=101.0, end=103.5))
    shown.show_caption(Caption("in", "नमस्ते", "Hello", 1, start=101.0, end=103.5))
    shown.show_caption(Caption("out", "Thank you", "धन्यवाद", 1, start=105.0, end=106.2))
    shown.set_status("ok")
    files = rec.close()
    assert len(ui.captions) == 4 and ui.statuses == ["ok"]  # the overlay still sees everything
    meta, entries = load(tmp_path / "s.jsonl")
    assert meta["their_language"] == "hi" and "started" in meta
    assert entries == [Entry("in", 1, 1.0, 3.5, "नमस्ते", "Hello"),
                       Entry("out", 1, 5.0, 6.2, "Thank you", "धन्यवाद")]
    assert [p.suffix for p in files] == [".jsonl", ".md", ".srt"] and all(p.exists() for p in files)


def test_empty_session_leaves_no_files(tmp_path):
    rec = recorder(tmp_path, [0.0])
    assert rec.close() == [] and list(tmp_path.iterdir()) == []


def test_merged_caption_supersedes_its_fragment():
    entries = [Entry("in", 4, 10.0, 11.7, "एक साल के लिए", "for a year"),
               Entry("out", 4, 12.0, 13.0, "ok", "ठीक है"),   # same seq, other direction: kept
               Entry("in", 5, 12.4, 14.5, "एक साल के लिए अगर जरूरत ना हो", "if not needed for a year", replaces=4)]
    resolved = resolve(entries)
    assert [(e.direction, e.seq, e.start) for e in resolved] == [("in", 5, 10.0), ("out", 4, 12.0)]


def test_markdown_lists_both_directions():
    md = to_markdown({**META, "started": "2026-11-26T21:30:05"},
                     [Entry("in", 1, 3.0, 4.5, "नमस्ते", "Hello"), Entry("out", 1, 65.0, 66.0, "Thanks", "धन्यवाद")])
    assert md.startswith("# Meeting transcript, 2026-11-26 21:30\n")
    assert "They spoke Hindi, you spoke English. Length 00:01:06." in md
    assert "**00:00:03 · Them (Hindi → English)**  \nनमस्ते  \n→ Hello" in md
    assert "**00:01:05 · You (English → Hindi)**  \nThanks  \n→ धन्यवाद" in md


def test_markdown_marks_a_missing_translation():
    assert "→ *(no translation)*" in to_markdown(META, [Entry("in", 1, 0.0, 1.0, "नमस्ते", "")])


def test_srt_is_bilingual_with_a_minimum_duration():
    srt = to_srt([Entry("in", 1, 3.25, 3.5, "हाँ", "yes"), Entry("out", 1, 3723.0, 3725.5, "Thanks", "धन्यवाद")])
    assert srt == ("1\n00:00:03,250 --> 00:00:04,450\nThem: yes\nहाँ\n\n"
                   "2\n01:02:03,000 --> 01:02:05,500\nYou: धन्यवाद\nThanks\n")
    assert to_srt([]) == ""
