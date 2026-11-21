"""Unit tests for all 8 BodhanToolkit tools against FakeBodhanTransport."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from bodhan_mcp.cache import DiskCache
from bodhan_mcp.client import BodhanClient
from bodhan_mcp.tools import BodhanToolkit

from tests.fakes import (
    FakeBodhanState,
    make_fake_bodhan_transport,
    make_segmented_wav,
    make_test_image,
    make_test_pdf,
)


def _toolkit(tmp_path: Path, state: FakeBodhanState | None = None) -> tuple[BodhanToolkit, FakeBodhanState]:
    transport, st = make_fake_bodhan_transport(state)
    client = BodhanClient(
        cache=DiskCache(tmp_path / "cache"),
        transport=transport,
        base_backoff_s=0.005,
        max_retries=0,  # Let tool-level 502 recovery run cleanly without waiting on HTTP retries.
    )
    return BodhanToolkit(client=client, work_dir=tmp_path / "work"), st


async def test_inspect_files_handles_all_kinds_and_missing(tmp_path: Path) -> None:
    tk, _ = _toolkit(tmp_path)
    wav = make_segmented_wav(tmp_path / "clip.wav", [("tone", 3.0)])
    img = make_test_image(tmp_path / "page.png")
    pdf = make_test_pdf(tmp_path / "doc.pdf", page_count=4)
    txt = tmp_path / "note.txt"
    txt.write_text("வணக்கம் இது தமிழ் உரை.", encoding="utf-8")

    res = await tk.inspect_files([str(wav), str(img), str(pdf), str(txt), str(tmp_path / "nope.wav")])
    assert res["ok"] is True
    assert res["cost_inr"] == 0.0
    by_name = {Path(f["path"]).name: f for f in res["data"]["files"]}
    assert by_name["clip.wav"]["kind"] == "audio" and by_name["clip.wav"]["duration_s"] >= 2.9
    assert by_name["page.png"]["kind"] == "image" and by_name["page.png"]["page_count"] == 1
    assert by_name["doc.pdf"]["kind"] == "pdf" and by_name["doc.pdf"]["page_count"] == 4
    assert by_name["note.txt"]["kind"] == "text" and by_name["note.txt"]["detected_language_hint"] == "ta"
    assert by_name["nope.wav"]["kind"] == "missing" and by_name["nope.wav"]["exists"] is False


async def test_transcribe_audio_chunks_long_audio_and_writes_srt(tmp_path: Path) -> None:
    st = FakeBodhanState(
        transcribe_texts=[
            "पहला भाग हिंदी में है।",
            "दूसरा भाग हिंदी में है।",
            "तीसरा भाग हिंदी में है।",
        ]
    )
    tk, _ = _toolkit(tmp_path, st)
    wav = make_segmented_wav(
        tmp_path / "note.wav",
        [("tone", 20.0), ("silence", 1.5), ("tone", 20.0), ("silence", 1.5), ("tone", 18.0)],
    )
    res = await tk.transcribe_audio(str(wav), language="hi", output_dir=str(tmp_path / "out"))
    assert res["ok"] is True
    assert res["data"]["segment_count"] >= 3
    assert Path(res["data"]["text_path"]).exists()
    assert Path(res["data"]["segments_path"]).exists()

    # Translate transcript to English and build SRT.
    tr = await tk.translate_text("en", path=res["data"]["text_path"], output_dir=str(tmp_path / "out"))
    assert tr["ok"] is True
    srt_res = await tk.write_subtitles(
        res["data"]["segments_path"],
        translations_path=tr["data"]["text_path"],
        format="srt",
        output_dir=str(tmp_path / "out"),
    )
    assert srt_res["ok"] is True
    assert srt_res["data"]["validation"]["valid"] is True
    assert srt_res["data"]["cue_count"] >= 3


async def test_translate_text_recovers_from_truncated_and_preserves_tables(tmp_path: Path) -> None:
    st = FakeBodhanState(translate_truncate_once=True)
    tk, _ = _toolkit(tmp_path, st)
    md = (
        "| Subject | Marks |\n"
        "| --- | --- |\n"
        "| Math | 95 |\n"
        "| Science | 88 |\n"
        "| History | 91 |\n"
        "| Art | 84 |"
    )
    res = await tk.translate_text("mr", text=md, output_dir=str(tmp_path / "out"))
    assert res["ok"] is True
    assert res["data"]["truncation_splits"] >= 1
    assert res["data"]["coverage"]["tables_preserved"] is True
    assert res["data"]["coverage"]["digits_preserved"] is True


async def test_ocr_document_pdf_paging_and_502_recovery(tmp_path: Path) -> None:
    # Force 2 consecutive 502s on page 1 -> recovers via retry_downscale!
    st = FakeBodhanState(ocr_502_remaining=2, ocr_pages=["# Page One\n\nText 1", "# Page Two\n\nText 2"])
    tk, _ = _toolkit(tmp_path, st)
    pdf = make_test_pdf(tmp_path / "chapter.pdf", page_count=3)

    res = await tk.ocr_document(str(pdf), pages="1-2", output_dir=str(tmp_path / "out"))
    assert res["ok"] is True
    assert res["data"]["page_count"] == 2
    strategies = [p["strategy"] for p in res["data"]["pages"]]
    assert "retry_downscale" in strategies or "retry_max_tokens" in strategies
    combined = Path(res["data"]["text_path"]).read_text(encoding="utf-8")
    assert "Page One" in combined and "Page Two" in combined


async def test_synthesize_and_verify_speech_with_ai_tag_and_repetition_detection(tmp_path: Path) -> None:
    st = FakeBodhanState(
        transcribe_by_lang={"bn": "এটি একটি বাংলা পরীক্ষা বাক্য।"},
        speak_duration_s=2.0,
    )
    tk, _ = _toolkit(tmp_path, st)
    res = await tk.synthesize_speech(
        "bn",
        text="এটি একটি বাংলা পরীক্ষা বাক্য।",
        gender="female",
        format="mp3",
        output_dir=str(tmp_path / "out"),
    )
    assert res["ok"] is True
    assert res["data"]["voice"] == "Ishita"
    audio_path = Path(res["data"]["audio_path"])
    assert audio_path.exists() and audio_path.stat().st_size > 100

    # Verify ID3 metadata carries AI-generated label.
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", str(audio_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    tags = json.loads(probe.stdout)["format"].get("tags", {})
    tag_blob = " ".join(str(v) for v in tags.values()).lower()
    assert "ai-generated" in tag_blob

    # Back-transcribe verify (passing).
    ver = await tk.verify_speech(
        str(audio_path),
        "bn",
        chunk_manifest_path=res["data"]["chunk_manifest_path"],
    )
    assert ver["ok"] is True
    assert ver["data"]["passed"] is True
    assert ver["data"]["mean_cer"] == 0.0

    # Now simulate an Indic-Speak repetition loop -> verify_speech must catch it.
    st.speak_repeat_loop = True
    tk.client.cache.enabled = False
    ver_bad = await tk.verify_speech(
        str(audio_path),
        "bn",
        chunk_manifest_path=res["data"]["chunk_manifest_path"],
    )
    assert ver_bad["ok"] is True
    assert ver_bad["data"]["passed"] is False
    assert ver_bad["data"]["verdicts"][0]["repetition_detected"] is True


async def test_structured_errors_on_bad_inputs(tmp_path: Path) -> None:
    tk, _ = _toolkit(tmp_path)

    e1 = await tk.translate_text("fr", text="Hello")
    assert e1["ok"] is False and e1["error"]["code"] == "unsupported_language"

    e2 = await tk.transcribe_audio(str(tmp_path / "missing.wav"))
    assert e2["ok"] is False and e2["error"]["code"] == "file_not_found"

    txt = tmp_path / "not_audio.txt"
    txt.write_text("hello", encoding="utf-8")
    e3 = await tk.transcribe_audio(str(txt))
    assert e3["ok"] is False and e3["error"]["code"] == "invalid_file_kind"

    e4 = await tk.ocr_document(str(txt))
    assert e4["ok"] is False and e4["error"]["code"] == "invalid_file_kind"

    e5 = await tk.synthesize_speech("ta", text="   ")
    assert e5["ok"] is False and e5["error"]["code"] == "empty_input"

    e6 = await tk.synthesize_speech("ta", text="வணக்கம்", voice="FakeVoice")
    assert e6["ok"] is False and e6["error"]["code"] == "invalid_voice"

    e7 = await tk.write_subtitles(str(tmp_path / "no_segs.json"), format="srt")
    assert e7["ok"] is False and e7["error"]["code"] == "file_not_found"
