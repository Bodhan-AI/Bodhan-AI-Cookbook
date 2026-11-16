"""Tool-level tests: each tool's chunking and limit handling against the fake API."""

import json

import pytest
from PIL import Image

from bodhan_mcp import tools
from bodhan_mcp.errors import ToolFailure


def _write(tmp_path, name, data):
    p = tmp_path / name
    if isinstance(data, bytes):
        p.write_bytes(data)
    else:
        p.write_text(data, encoding="utf-8")
    return p


# -- transcribe_audio --------------------------------------------------------------------------

async def test_long_audio_is_chunked_under_30s(rt, fake, tmp_path):
    sentences = [f"वाक्य संख्या {i} यहाँ है और यह एक लंबा वाक्य है जो कुछ समय लेता है" for i in range(12)]
    wav = fake.make_wav(sentences, sr=16000, gap_s=0.8)  # ~45 s of "speech"
    audio = _write(tmp_path, "note.wav", wav)
    res = await tools.transcribe_audio(rt, str(audio), language="hi")
    assert res.segment_count >= 2
    assert all(s.end - s.start <= 29.5 for s in res.segments)
    assert res.duration_s > 30
    for s in sentences:  # fake API rejects >30 s requests, so success means chunking worked
        assert s in res.text
    data = json.loads(open(res.segments_path, encoding="utf-8").read())
    assert data["language"] == "hi" and len(data["segments"]) == res.segment_count
    assert not fake.duplicate_requests()


async def test_auto_language_detection_probe(rt, fake, tmp_path):
    clip = _write(tmp_path, "clip.wav", fake.make_wav(["నమస్కారం మిత్రమా ఇది తెలుగు"], sr=16000))
    res = await tools.transcribe_audio(rt, str(clip))
    assert res.language == "te" and res.language_source == "detected"
    assert fake.calls["transcribe"] == 1  # short clip: the probe is reused, audio not sent twice


async def test_unsupported_spoken_language_is_reported(rt, fake, tmp_path):
    clip = _write(tmp_path, "fr.wav", fake.make_wav(["le chat est sur la table et nous sommes dans la maison"], sr=16000))
    res = await tools.transcribe_audio(rt, str(clip))
    assert res.language_supported is False and res.language == "fr"


async def test_silent_audio_costs_nothing(rt, fake, tmp_path):
    import numpy as np

    from bodhan_mcp import media

    clip = _write(tmp_path, "silence.wav", media.to_wav_bytes(np.zeros(16000 * 5, dtype=np.float32), 16000))
    res = await tools.transcribe_audio(rt, str(clip), language="hi")
    assert res.no_speech and res.cost_inr == 0 and fake.calls["transcribe"] == 0


async def test_transcribe_rejects_wrong_inputs(rt, tmp_path):
    img = tmp_path / "page.png"
    Image.new("RGB", (50, 50), "white").save(img)
    with pytest.raises(ToolFailure) as e1:
        await tools.transcribe_audio(rt, str(img))
    assert e1.value.code == "unsupported_file_type" and "ocr_document" in e1.value.hint
    with pytest.raises(ToolFailure) as e2:
        await tools.transcribe_audio(rt, str(tmp_path / "missing.wav"))
    assert e2.value.code == "file_not_found"


async def test_transcribe_budget_guard(rt, fake, tmp_path):
    audio = _write(tmp_path, "a.wav", fake.make_wav(["x" * 400], sr=16000))
    with pytest.raises(ToolFailure) as e:
        await tools.transcribe_audio(rt, str(audio), language="hi", max_cost_inr=0.0001)
    assert e.value.code == "budget_exceeded" and fake.calls["transcribe"] == 0


# -- translate_text ---------------------------------------------------------------------------

async def test_markdown_table_kept_through_translation(rt, fake, tmp_path):
    md = "# Worksheet\n\n| Question | Answer |\n|---|---|\n| What is water? | H2O |\n| 2 + 2 | 4 |\n\nWrite your name below."
    src = _write(tmp_path, "sheet.md", md)
    res = await tools.translate_text(rt, "mr", input_path=str(src))
    out = open(res.output_path, encoding="utf-8").read()
    assert out.count("|") == md.count("|") and "|---|---|" in out
    assert "| 2 + 2 | 4 |" in out  # numbers and symbols are not sent to the model
    assert res.verification.passed and res.tables_preserved >= 1
    assert res.verification.target_script == "Devanagari"


async def test_long_document_is_chunked_for_context_window(rt, fake, tmp_path):
    body = "\n\n".join(("Paragraph %d. " % i) + "The water cycle moves water around the planet. " * 20 for i in range(12))
    assert len(body) > 5000  # the fake API rejects anything over 5,000 characters in one request
    res = await tools.translate_text(rt, "ta", text=body)
    assert res.verification.passed
    assert res.api_requests >= 2 and res.chars_out > 0.9 * res.chars_in


async def test_truncated_output_is_resplit(rt, fake):
    fake.truncate_over = 600
    text = " ".join("Sentence number %d is here." % i for i in range(60))
    res = await tools.translate_text(rt, "hi", text=text)
    assert res.verification.truncated_units == 0
    assert res.api_requests > 1


async def test_segments_translation_keeps_timings(rt, fake, tmp_path):
    seg = {"language": "hi", "segments": [{"index": 0, "start": 0.0, "end": 2.0, "text": "पहला"}, {"index": 1, "start": 2.5, "end": 5.0, "text": "दूसरा"}]}
    p = _write(tmp_path, "v.segments.json", json.dumps(seg, ensure_ascii=False))
    res = await tools.translate_text(rt, "en", input_path=str(p))
    out = json.loads(open(res.output_path, encoding="utf-8").read())
    assert [s["start"] for s in out["segments"]] == [0.0, 2.5]
    assert out["segments"][0]["source_text"] == "पहला" and out["language"] == "en"
    assert fake.calls["translate"] == 1  # short lines are batched into one request
    sub = tools.make_subtitles(rt, res.output_path)
    assert sub.cue_count == 2 and open(sub.subtitle_path, encoding="utf-8").read().startswith("1\n00:00:00,000 --> 00:00:02,000")


async def test_unsupported_target_language(rt):
    with pytest.raises(ToolFailure) as e:
        await tools.translate_text(rt, "French", text="Hello")
    assert e.value.code == "unsupported_language" and "French" in e.value.message


async def test_translate_needs_exactly_one_input(rt):
    with pytest.raises(ToolFailure) as e:
        await tools.translate_text(rt, "hi")
    assert e.value.code == "invalid_input"


# -- synthesize_speech ------------------------------------------------------------------------

async def test_long_text_is_split_for_tts_and_verified(rt, fake):
    text = " ".join(f"यह वाक्य संख्या {i} है।" for i in range(40))  # far beyond one TTS request
    res = await tools.synthesize_speech(rt, "hi", text=text, output_format="wav")
    assert res.chunks > 1 and res.voice == "Kavya"
    assert res.verification.passed and res.verification.similarity > 0.9
    assert res.cost_inr > 0 and res.duration_s > 5


async def test_repeated_chunk_is_regenerated_in_pieces(rt, fake):
    bad = "पहला वाक्य यहाँ है। दूसरा वाक्य यहाँ है।"
    fake.repeat_texts.add(bad)
    res = await tools.synthesize_speech(rt, "hi", text=bad, output_format="wav")
    assert res.verification.resynthesized_chunks == [0]
    assert res.verification.passed
    assert not fake.duplicate_requests()  # the retry used different (smaller) inputs


async def test_language_mismatch_is_caught_before_spending(rt, fake):
    with pytest.raises(ToolFailure) as e:
        await tools.synthesize_speech(rt, "bn", text="This is English text that should be translated first.")
    assert e.value.code == "language_mismatch" and fake.calls["speak"] == 0


async def test_voice_selection_and_validation(rt, fake):
    res = await tools.synthesize_speech(rt, "bn", text="আগামীকাল স্কুল বন্ধ থাকবে।", gender="female", output_format="wav", verify=False)
    assert res.voice == "Ishita"
    with pytest.raises(ToolFailure) as e:
        await tools.synthesize_speech(rt, "bn", text="আগামীকাল", voice="Morgan")
    assert e.value.code == "unsupported_voice"


async def test_tts_budget_guard(rt, fake):
    with pytest.raises(ToolFailure) as e:
        await tools.synthesize_speech(rt, "hi", text="नमस्ते " * 200, max_cost_inr=0.01)
    assert e.value.code == "budget_exceeded" and fake.calls["speak"] == 0


# -- ocr_document -----------------------------------------------------------------------------

def _pdf(tmp_path, pages):
    imgs = []
    for i in range(pages):
        im = Image.new("RGB", (600, 800 + i), "white")
        from PIL import ImageDraw

        ImageDraw.Draw(im).text((50, 50), f"Page {i + 1} text", fill="black")
        imgs.append(im)
    p = tmp_path / "book.pdf"
    imgs[0].save(p, save_all=True, append_images=imgs[1:])
    return p


async def test_multipage_pdf_is_rasterised_per_page(rt, fake, tmp_path):
    pdf = _pdf(tmp_path, 3)
    res = await tools.ocr_document(rt, str(pdf))
    assert res.pages_processed == 3 and fake.calls["ocr"] == 3
    md = open(res.output_path, encoding="utf-8").read()
    assert "<!-- page 1 -->" in md and "<!-- page 3 -->" in md
    assert res.cost_inr == pytest.approx(0.6)
    sub = await tools.ocr_document(rt, str(pdf), pages="2")
    assert sub.pages_processed == 1


async def test_blank_pages_are_skipped(rt, fake, tmp_path):
    blank = tmp_path / "blank.png"
    Image.new("RGB", (400, 400), "white").save(blank)
    res = await tools.ocr_document(rt, str(blank))
    assert res.pages[0].skipped == "blank page" and res.cost_inr == 0
    assert fake.calls["ocr"] == 0 and "No text was found." in res.warnings


async def test_ocr_bad_page_range_and_type(rt, tmp_path):
    pdf = _pdf(tmp_path, 2)
    with pytest.raises(ToolFailure) as e:
        await tools.ocr_document(rt, str(pdf), pages="5")
    assert e.value.code == "invalid_input"
    txt = _write(tmp_path, "a.txt", "hello")
    with pytest.raises(ToolFailure) as e2:
        await tools.ocr_document(rt, str(txt))
    assert e2.value.code == "unsupported_file_type" and "read_text" in e2.value.hint


async def test_ocr_502_retries_with_larger_max_tokens(rt, fake, tmp_path):
    img = tmp_path / "table.png"
    im = Image.new("RGB", (300, 300), "white")
    from PIL import ImageDraw

    ImageDraw.Draw(im).rectangle([10, 10, 200, 200], outline="black")
    im.save(img)
    fake.fail_next = [502]
    res = await tools.ocr_document(rt, str(img))
    assert res.pages_processed == 1 and res.failed_pages == []


# -- helpers ----------------------------------------------------------------------------------

def test_inspect_reports_kinds_costs_and_missing(rt, fake, tmp_path):
    _write(tmp_path, "a.wav", fake.make_wav(["hello"], sr=16000))
    _write(tmp_path, "b.txt", "আগামীকাল স্কুল বন্ধ থাকবে।")
    res = tools.inspect_files(rt, [str(tmp_path), str(tmp_path / "nope.mp3")])
    kinds = {f.path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]: f for f in res.files if f.exists}
    assert kinds["a.wav"].kind == "audio" and "transcribe_audio" in kinds["a.wav"].estimated_cost_inr
    assert kinds["b.txt"].language.language == "bn"
    assert res.missing == [str(tmp_path / "nope.mp3")]


def test_read_text_pages_long_files(rt, tmp_path):
    p = _write(tmp_path, "long.txt", "x" * 30000)
    r1 = tools.read_text(rt, str(p), max_chars=20000)
    assert r1.has_more and r1.returned_chars == 20000
    r2 = tools.read_text(rt, str(p), offset=20000)
    assert not r2.has_more and r2.returned_chars == 10000


def test_output_paths_cannot_escape(rt, tmp_path):
    with pytest.raises(ToolFailure) as e:
        tools.resolve_output(rt, "../../../etc/evil.txt", "x.txt")
    assert e.value.code == "path_not_allowed"
