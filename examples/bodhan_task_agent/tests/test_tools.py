"""Tool behaviour against the fake Bodhan API: retries, errors, budgets, caching, chunking, verification."""

import json
from pathlib import Path

import pytest
from PIL import Image

from bodhan_mcp import errors
from bodhan_mcp.client import Ledger
from fakes import tone_wav


async def test_retry_on_429_then_success(service, fake):
    fake.fail_next["/v1/chat/completions"] = [429, 503]
    r = await service.translate_text("hi", "Hello there, friend.", None, "native", output_dir=None, ledger=Ledger())
    assert r.verification.passed
    assert fake.requests["/v1/chat/completions"] == 3
    assert r.calls[0].attempts == 3


async def test_non_retryable_errors_are_structured(service, fake):
    fake.fail_next["/v1/chat/completions"] = [401]
    with pytest.raises(errors.BodhanError) as e:
        await service.translate_text("hi", "Hello", None, "native", output_dir=None, ledger=Ledger())
    assert e.value.code == errors.AUTH_FAILED and fake.requests["/v1/chat/completions"] == 1


async def test_unsupported_language_is_refused_before_any_request(service, fake):
    with pytest.raises(errors.BodhanError) as e:
        await service.translate_text("French", "Hello", None, "native", output_dir=None, ledger=Ledger())
    assert e.value.code == errors.UNSUPPORTED_LANGUAGE and sum(fake.requests.values()) == 0


async def test_english_speech_falls_back_to_a_listed_voice(service, fake, tmp_path):
    r = await service.synthesize_speech("en", "Hello world.", None, None, "male", None, "wav", None, True,
                                        output_dir=str(tmp_path), ledger=Ledger())
    assert r.voice == "Amit" and r.warnings


async def test_budget_refuses_before_sending(service, fake, tmp_path):
    with pytest.raises(errors.BodhanError) as e:
        await service.synthesize_speech("en", "Hello world. " * 50, None, None, None, None, "wav", None, True,
                                        output_dir=str(tmp_path), ledger=Ledger(0.0001))
    assert e.value.code == errors.BUDGET_EXCEEDED and fake.requests["/v1/audio/speech"] == 0


async def test_same_input_is_never_sent_twice(service, fake):
    for _ in range(3):
        await service.translate_text("ta", "The meeting is on Monday.", None, "native", output_dir=None, ledger=Ledger())
    assert fake.requests["/v1/chat/completions"] == 1


async def test_falls_back_to_documented_translate_route(service, fake):
    fake.legacy_translate_only = True
    r = await service.translate_text("ta", "The meeting is on Monday.", None, "native", output_dir=None, ledger=Ledger())
    assert r.verification.passed and fake.requests["/translate"] == 1


async def test_table_separator_dropped_by_model_is_restored(service, fake, tmp_path):
    md = "| Day | Rain |\n|---|---|\n| Monday | 12 |"
    r = await service.translate_text("mr", md, None, "native", output_dir=str(tmp_path), ledger=Ledger())
    out = Path(r.output_file).read_text(encoding="utf-8")
    from bodhan_mcp import text as T
    assert T.table_structure(out) == T.table_structure(md)
    assert any(c.name == "table_rebuilt_cellwise" for c in r.verification.checks)


async def test_long_document_is_chunked_and_tables_survive(service, fake, tmp_path):
    table = "| Item | Qty |\n|---|---|\n| Apples | 12 |\n| Rice | 3 |"
    doc = ("This is a paragraph about rivers and mountains. " * 120) + "\n\n" + table + "\n\n" + ("Closing words. " * 50)
    src = tmp_path / "doc.md"
    src.write_text(doc, encoding="utf-8")
    r = await service.translate_text("mr", None, str(src), "native", output_dir=str(tmp_path / "o"), ledger=Ledger())
    assert r.chunks >= 3
    assert r.verification.passed, r.verification
    out = Path(r.output_file).read_text(encoding="utf-8")
    from bodhan_mcp import text as T
    assert "12" in out and T.table_shapes(out) == T.table_shapes(table)
    names = {c.name for c in r.verification.checks}
    assert {"target_script", "numbers_preserved", "document_tables_preserved"} <= names


async def test_table_rebuilt_cellwise_when_model_breaks_it(service, fake, tmp_path):
    fake.translate_breaks_tables = True
    md = "| Name | Marks |\n|---|---|\n| Asha | 42 |\n| Ravi | 37 |"
    r = await service.translate_text("ta", md, None, "native", output_dir=str(tmp_path), ledger=Ledger())
    assert r.verification.passed
    assert any(c.name == "table_rebuilt_cellwise" for c in r.verification.checks)
    out = Path(r.output_file).read_text(encoding="utf-8")
    assert out.strip().count("\n") == 3 and "| 42 |" in out


async def test_transcribe_long_audio_in_windows_and_segments_file(service, fake, tmp_path):
    p = tmp_path / "note.wav"
    p.write_bytes(tone_wav(70.0, sr=16000, bursts=[(i * 5.0, i * 5.0 + 4.2) for i in range(14)]))
    r = await service.transcribe_audio(str(p), "hi", output_dir=str(tmp_path / "o"), ledger=Ledger())
    assert r.segments >= 3 and len(r.calls) == r.segments
    assert fake.requests["/v1/audio/transcriptions"] <= r.segments  # identical windows are served from cache
    segs = json.loads(Path(r.segments_file).read_text(encoding="utf-8"))["segments"]
    assert all(s["end"] - s["start"] <= 30.0 for s in segs)
    assert r.detected_script == "Devanagari" and r.language_candidates[0] == "hi"
    assert r.cost_inr > 0


async def test_language_hint_mismatch_warns(service, fake, tmp_path):
    p = tmp_path / "c.wav"
    p.write_bytes(tone_wav(3.0, sr=16000))
    r = await service.transcribe_audio(str(p), "te", output_dir=str(tmp_path), ledger=Ledger())
    assert any("auto-detect" in w for w in r.warnings)


async def test_tts_chunks_verifies_and_recovers_from_repetition(service, fake, tmp_path):
    text_in = "अनुच्छेद एक। " * 40
    from bodhan_mcp import text as T
    first_piece = T.split_for_tts(text_in.strip(), 220)[0]
    fake.tts_repeat_once.add(first_piece)
    r = await service.synthesize_speech("hi", text_in, None, None, "female", None, "wav", None, True,
                                        output_dir=str(tmp_path), ledger=Ledger())
    assert r.pieces > 1 and r.voice in {"Kavya", "Suhani"}
    assert r.verification and r.verification.pieces[0].retried
    assert r.verification.passed, r.verification
    assert Path(r.audio_file).stat().st_size > 1000 and r.ai_generated


async def test_tts_rejects_text_in_wrong_script(service, fake):
    with pytest.raises(errors.BodhanError) as e:
        await service.synthesize_speech("bn", "This is English.", None, None, None, None, "wav", None, True,
                                        output_dir=None, ledger=Ledger())
    assert "translate_text" in (e.value.hint or "") and fake.requests["/v1/audio/speech"] == 0


async def test_ocr_multipage_pdf_has_page_markers_and_low_conf(service, fake, tmp_path):
    imgs = [Image.new("RGB", (400, 500), "white") for _ in range(3)]
    p = tmp_path / "scan.pdf"
    imgs[0].save(p, save_all=True, append_images=imgs[1:])
    r = await service.ocr_document(str(p), None, "markdown", output_dir=str(tmp_path / "o"), ledger=Ledger())
    assert r.page_count == 3 and fake.requests["/v1/chat/completions"] == 1  # identical pages hit the cache
    md = Path(r.markdown_file).read_text(encoding="utf-8")
    assert md.count("<!-- page") == 3 and r.tables == 3 and r.low_confidence_blocks
    assert r.cost_inr == pytest.approx(0.2)


async def test_subtitles_from_translated_segments(service, fake, tmp_path):
    p = tmp_path / "v.wav"
    p.write_bytes(tone_wav(40.0, sr=16000, bursts=[(i * 4.0, i * 4.0 + 3.0) for i in range(10)]))
    tr = await service.transcribe_audio(str(p), "hi", output_dir=str(tmp_path), ledger=Ledger())
    tl = await service.translate_text("en", None, tr.segments_file, "native", output_dir=str(tmp_path), ledger=Ledger())
    sub = await service.make_subtitles(tl.segments_file, "auto", "srt", None, output_dir=str(tmp_path))
    assert sub.field_used == "translation" and sub.cues >= tr.segments
    assert "-->" in Path(sub.subtitle_file).read_text(encoding="utf-8")


async def test_missing_file_and_wrong_media(service, tmp_path):
    with pytest.raises(errors.BodhanError) as e:
        await service.transcribe_audio(str(tmp_path / "nope.wav"), None, output_dir=None, ledger=Ledger())
    assert e.value.code == errors.FILE_NOT_FOUND
    txt = tmp_path / "a.txt"
    txt.write_text("hello")
    with pytest.raises(errors.BodhanError) as e:
        await service.ocr_document(str(txt), None, "markdown", output_dir=None, ledger=Ledger())
    assert e.value.code == errors.UNSUPPORTED_MEDIA
