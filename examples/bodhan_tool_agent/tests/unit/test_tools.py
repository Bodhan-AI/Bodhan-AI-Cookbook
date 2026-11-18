"""Tool-level tests against the fake Bodhan API: limits handled inside tools, structured errors, no waste."""

import json
from pathlib import Path

import pytest

from bodhan_mcp import server
from bodhan_mcp.tools import speak as speak_tool
from conftest import pdf_file, png_file, tone_wav


def load(path):
    return Path(path).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- inspect_media


async def test_inspect_media_kinds(fake, tmp_path):
    wav = tone_wav(tmp_path / "a.wav", [("tone", 3)])
    r = await server.inspect_media(str(wav))
    assert r["ok"] and r["kind"] == "audio" and r["duration_s"] == pytest.approx(3, abs=0.05)
    assert r["suggested_tool"] == "transcribe_audio"

    pdf = pdf_file(tmp_path / "doc.pdf", pages=4)
    r = await server.inspect_media(str(pdf))
    assert r["kind"] == "pdf" and r["pages"] == 4 and r["estimated_cost_inr"]["ocr_document"] == pytest.approx(0.8)

    txt = tmp_path / "t.txt"
    txt.write_text("வணக்கம், இன்று வகுப்பு.", encoding="utf-8")
    r = await server.inspect_media(str(txt))
    assert r["kind"] == "text" and r["script"]["candidates"] == ["ta"] and r["untrusted_content"]
    assert sum(fake.calls.values()) == 0  # inspect is free


@pytest.mark.parametrize("bad,code", [("/nope/missing.wav", "FILE_NOT_FOUND"), ("", "INVALID_ARGUMENT"),
                                      ("data:audio/wav;base64,AAAA", "INVALID_ARGUMENT")])
async def test_bad_paths_are_structured_errors(fake, bad, code):
    for tool in (server.inspect_media, server.ocr_document, server.transcribe_audio):
        r = await tool(bad)
        assert r["ok"] is False and r["error"]["code"] == code and r["error"]["message"]


async def test_directory_and_empty_file(fake, tmp_path):
    assert (await server.inspect_media(str(tmp_path)))["error"]["code"] == "INVALID_ARGUMENT"
    empty = tmp_path / "e.wav"
    empty.write_bytes(b"")
    assert (await server.inspect_media(str(empty)))["error"]["code"] == "UNSUPPORTED_MEDIA"


# --------------------------------------------------------------------------- OCR


async def test_ocr_pdf_page_by_page(fake, tmp_path):
    pdf = pdf_file(tmp_path / "chapter.pdf", pages=3)
    fake.ocr_pages = ["# अध्याय", "पहला पृष्ठ", "| क | ख |\n|---|---|\n| 1 | 2 |"]
    r = await server.ocr_document(str(pdf))
    assert r["ok"] and r["complete"] and r["pages_processed"] == [1, 2, 3]
    assert fake.calls["ocr"] == 3 and r["cost_inr"] == pytest.approx(0.6)
    md = load(r["text_path"])
    assert "<!-- page 1 -->" in md and "<!-- page 3 -->" in md and "| 1 | 2 |" in md
    assert r["untrusted_content"] and r["script"]["script"] == "Devanagari"

    again = await server.ocr_document(str(pdf))  # same input never sent twice
    assert fake.calls["ocr"] == 3 and again["cached_pages"] == 3 and again["cost_inr"] == 0


async def test_ocr_page_selection_and_limits(fake, tmp_path):
    pdf = pdf_file(tmp_path / "d.pdf", pages=5)
    r = await server.ocr_document(str(pdf), pages="2-3,5")
    assert r["pages_processed"] == [2, 3, 5] and fake.calls["ocr"] == 3
    assert (await server.ocr_document(str(pdf), pages="4-9"))["error"]["code"] == "INVALID_ARGUMENT"
    assert (await server.ocr_document(str(pdf), pages="x"))["error"]["code"] == "INVALID_ARGUMENT"
    r = await server.ocr_document(str(pdf), max_pages=2)
    assert r["error"]["code"] == "INVALID_ARGUMENT" and "max_pages" in r["error"]["message"]


async def test_ocr_partial_failure_is_reported(fake, tmp_path):
    pdf = pdf_file(tmp_path / "d.pdf", pages=3)
    fake.fail_ocr_calls = {2}
    r = await server.ocr_document(str(pdf))
    assert r["ok"] and not r["complete"] and len(r["failed_pages"]) == 1
    assert "OCR failed" in load(r["text_path"])


async def test_ocr_image_and_wrong_kind(fake, tmp_path):
    r = await server.ocr_document(str(png_file(tmp_path / "w.png")))
    assert r["ok"] and r["pages_processed"] == [1]
    wav = tone_wav(tmp_path / "a.wav", [("tone", 1)])
    assert (await server.ocr_document(str(wav)))["error"]["code"] == "UNSUPPORTED_MEDIA"


# --------------------------------------------------------------------------- transcription


async def test_transcribe_long_audio_in_windows(fake, tmp_path):
    pattern = [("tone", 4.6), ("gap", 0.4)] * 15  # 75 s
    wav = tone_wav(tmp_path / "note.wav", pattern)
    fake.transcripts = ["पहला भाग", "दूसरा भाग", "तीसरा भाग"]
    r = await server.transcribe_audio(str(wav), language="Hindi")
    assert r["ok"] and r["complete"] and fake.calls["transcribe"] == 3
    segs = json.loads(load(r["segments_path"]))
    assert [s["text"] for s in segs] == ["पहला भाग", "दूसरा भाग", "तीसरा भाग"]
    assert segs[0]["start"] == 0 and segs[-1]["end"] == pytest.approx(75, abs=0.05)
    assert all(s["end"] - s["start"] <= 30 for s in segs)
    assert load(r["transcript_path"]).strip() == "पहला भाग दूसरा भाग तीसरा भाग"
    assert r["language"] == "hi" and r["cost_inr"] == pytest.approx(75 / 60 * 0.10, rel=0.01)


async def test_transcribe_skips_silence_and_detects_script(fake, tmp_path):
    wav = tone_wav(tmp_path / "s.wav", [("tone", 5), ("gap", 60), ("tone", 5)])
    fake.transcripts = ["வணக்கம்", "நன்றி"]
    r = await server.transcribe_audio(str(wav))
    assert r["windows"]["silent_skipped"] >= 1 and fake.calls["transcribe"] == r["windows"]["transcribed"]
    assert r["language"] == "ta" and r["language_source"] == "script"


async def test_transcribe_ambiguous_devanagari_warns(fake, tmp_path):
    fake.transcripts = ["आज शाळा बंद आहे"]
    r = await server.transcribe_audio(str(tone_wav(tmp_path / "m.wav", [("tone", 3)])))
    assert r["language"] is None and any("several languages" in w for w in r["warnings"])


async def test_transcribe_subtitle_segments_and_srt(fake, tmp_path):
    wav = tone_wav(tmp_path / "v.wav", [("tone", 2.5), ("gap", 0.4)] * 8)
    r = await server.transcribe_audio(str(wav), language="hi", segment_style="subtitle")
    segs = json.loads(load(r["segments_path"]))
    assert len(segs) >= 3 and all(s["end"] - s["start"] <= 7.0 for s in segs)
    t = await server.translate_segments(r["segments_path"], "en")
    assert t["ok"] and t["segments"] == len(segs)
    srt_r = await server.make_srt(t["segments_path"])
    assert srt_r["ok"] and srt_r["valid"] and srt_r["cues"] == len(segs)
    assert "-->" in load(srt_r["srt_path"])


async def test_transcribe_rejects_bad_language_and_long_audio(fake, tmp_path):
    wav = tone_wav(tmp_path / "a.wav", [("tone", 3)])
    r = await server.transcribe_audio(str(wav), language="French")
    assert r["error"]["code"] == "UNSUPPORTED_LANGUAGE" and fake.calls["transcribe"] == 0
    r = await server.transcribe_audio(str(wav), max_minutes=0.01)
    assert r["error"]["code"] == "INVALID_ARGUMENT"


async def test_transcribe_video_soundtrack(fake, tmp_path):
    import subprocess

    mp4 = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=2", "-f", "lavfi",
                    "-i", "sine=frequency=300:duration=2", "-shortest", "-c:v", "libx264", "-c:a", "aac", str(mp4)],
                   check=True)
    assert (await server.inspect_media(str(mp4)))["kind"] == "video"
    r = await server.transcribe_audio(str(mp4), language="te")
    assert r["ok"] and fake.calls["transcribe"] == 1


# --------------------------------------------------------------------------- translation


WORKSHEET = """# Worksheet

Answer all 3 questions.

| Subject | Marks |
|---|---|
| Maths | 45 |
| Science | 38 |
"""


async def test_translate_markdown_keeps_table(fake, tmp_path):
    src = tmp_path / "w.md"
    src.write_text(WORKSHEET, encoding="utf-8")
    r = await server.translate_text("Marathi", path=str(src))
    assert r["ok"] and r["coverage"]["complete"] and r["coverage"]["tables_preserved"]
    out = load(r["output_path"])
    assert "|---|---|" in out and "| 45 |" in out and out.startswith("# ")
    assert r["issue_count"] == 0
    assert fake.calls["translate"] == 6  # heading, paragraph, 4 text cells; numbers untouched


async def test_translate_inline_and_cache(fake):
    r1 = await server.translate_text("ta", text="School is closed on 5 June.")
    r2 = await server.translate_text("ta", text="School is closed on 5 June.")
    assert r1["translation"] and "5" in r1["translation"]
    assert fake.calls["translate"] == 1 and r2["cached_calls"] == 1 and r2["cost_inr"] == 0


async def test_translate_long_text_is_chunked(fake):
    text = " ".join(f"This is sentence number {i} of the chapter." for i in range(200))  # ~9k chars
    r = await server.translate_text("hi", text=text, preserve_format=False)
    assert r["ok"] and r["calls"] >= 6 and r["coverage"]["complete"]
    assert set(str(i) for i in range(200)) <= set(load(r["output_path"]).split()) | {
        w.strip(".") for w in load(r["output_path"]).split()}


async def test_translate_errors(fake, tmp_path):
    assert (await server.translate_text("French", text="Hello"))["error"]["code"] == "UNSUPPORTED_LANGUAGE"
    assert (await server.translate_text("hi"))["error"]["code"] == "INVALID_ARGUMENT"
    assert (await server.translate_text("hi", text="a", path="b"))["error"]["code"] == "INVALID_ARGUMENT"
    pdf = pdf_file(tmp_path / "x.pdf", 1)
    r = await server.translate_text("hi", path=str(pdf))
    assert r["error"]["code"] == "UNSUPPORTED_MEDIA" and "ocr_document" in r["error"]["hint"]
    assert fake.calls["translate"] == 0


async def test_translate_same_language_is_free(fake):
    r = await server.translate_text("en", text="Already English text here.")
    assert r["ok"] and r["calls"] == 0 and "unchanged" in r["note"]


# --------------------------------------------------------------------------- speech


async def test_speak_long_text_pieces_verified(fake):
    text = " ".join(["আজ আবহাওয়া খুব ভালো এবং শিশুরা বাগানে খেলছে।"] * 12)  # ~550 chars
    r = await server.synthesize_speech("Bengali", text=text, gender="female")
    assert r["ok"] and r["voice"] == "Ishita" and r["pieces"] >= 3
    assert r["verification"]["checked"] and r["verification"]["passed"]
    unique = len({p["text"] for p in json.loads(load(r["manifest_path"]))["pieces"]})
    assert unique < r["pieces"]  # repeated text: identical pieces come from the cache, not a second paid call
    assert fake.calls["speak"] == unique and fake.calls["transcribe"] == unique
    assert r["label"].startswith("AI-generated") and Path(r["audio_path"]).suffix == ".mp3"
    from bodhan_core import audio

    assert audio.probe(Path(r["audio_path"]))["duration_s"] == pytest.approx(r["duration_s"], abs=0.1)
    manifest = json.loads(load(r["manifest_path"]))
    assert all(len(p["text"]) <= speak_tool.PIECE_CHARS for p in manifest["pieces"])


async def test_speak_resynthesises_misheard_piece_once(fake):
    text = "இது முதல் வாக்கியம். இது இரண்டாவது வாக்கியம்."
    fake.mishear_first_take = {text}
    r = await server.synthesize_speech("ta", text=text)
    assert r["verification"]["passed"] and r["verification"]["retried_pieces"] == [1]
    assert fake.calls["speak"] == 2  # one deliberate retry, no more


async def test_speak_guards(fake):
    r = await server.synthesize_speech("bn", text="Read this in Bengali please.")
    assert r["error"]["code"] == "INVALID_ARGUMENT" and "translate_text" in r["error"]["hint"]
    assert (await server.synthesize_speech("bn", text="নমস্কার", voice="Nobody"))["error"]["code"] == "INVALID_ARGUMENT"
    assert (await server.synthesize_speech("fr", text="Bonjour"))["error"]["code"] == "UNSUPPORTED_LANGUAGE"
    assert (await server.synthesize_speech("hi", text="नमस्ते " * 100, max_chars=50))["error"]["code"] == "INVALID_ARGUMENT"
    assert (await server.synthesize_speech("hi", text="नमस्ते", style="shouting"))["error"]["code"] == "INVALID_ARGUMENT"
    assert fake.calls["speak"] == 0  # every guard fires before any paid call


async def test_speak_reads_markdown_file(fake, tmp_path):
    md = tmp_path / "s.hi.md"
    md.write_text("# शीर्षक\n\nयह पहला वाक्य है।\n\n| विषय | अंक |\n|---|---|\n| गणित | ४५ |\n", encoding="utf-8")
    r = await server.synthesize_speech("hi", path=str(md), verify=False)
    assert r["ok"] and r["verification"] == {"checked": False}
    spoken = " ".join(p["text"] for p in json.loads(load(r["manifest_path"]))["pieces"])
    assert "#" not in spoken and "|" not in spoken and "गणित, ४५" in spoken


# --------------------------------------------------------------------------- helpers and budgets


async def test_read_text_bounds(fake, tmp_path):
    f = tmp_path / "t.md"
    f.write_text("x" * 50, encoding="utf-8")
    r = await server.read_text(str(f), offset=40, length=20)
    assert r["text"] == "x" * 10 and not r["has_more"] and r["total_chars"] == 50
    assert (await server.read_text(str(f), offset=-1))["error"]["code"] == "INVALID_ARGUMENT"


async def test_make_srt_rejects_non_segments(fake, tmp_path):
    f = tmp_path / "x.json"
    f.write_text('{"not": "segments"}', encoding="utf-8")
    assert (await server.make_srt(str(f)))["error"]["code"] == "INVALID_ARGUMENT"


async def test_server_budget_cap_stops_paid_calls(fake, tmp_path):
    from bodhan_mcp import runtime

    runtime.get_client().ledger.limit_inr = 0.3
    pdf = pdf_file(tmp_path / "big.pdf", pages=3)
    r = await server.ocr_document(str(pdf))
    assert r["ok"] and not r["complete"]  # 1 page fits (₹0.20); the rest are refused before sending
    assert all(f["error"]["code"] == "BUDGET_EXCEEDED" for f in r["failed_pages"])
    assert runtime.get_client().ledger.spent_inr <= 0.3


async def test_list_voices_and_languages(fake):
    r = await server.list_voices(language="Bengali", gender="female")
    assert r["ok"] and r["voices"] == [{"voice": "Ishita", "language": "bn", "gender": "female"}] and r["styles"]
    assert (await server.list_voices(language="French"))["error"]["code"] == "UNSUPPORTED_LANGUAGE"
    langs = (await server.supported_languages())["languages"]
    assert "fr" not in {x["code"] for x in langs["indic-translate"]}
