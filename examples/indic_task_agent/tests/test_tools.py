"""Tool-level tests against the fake Bodhan API: limits, retries, caching, budgets, verification, errors."""

from __future__ import annotations

import json
import wave

import pytest
from PIL import Image, ImageDraw

from bodhan_mcp.errors import BodhanToolError
from bodhan_mcp.tools.files import make_subtitles, read_text
from bodhan_mcp.tools.inspect import inspect_files
from bodhan_mcp.tools.ocr import ocr_document, parse_pages
from bodhan_mcp.tools.speak import synthesize_speech
from bodhan_mcp.tools.transcribe import transcribe_audio
from bodhan_mcp.tools.translate import translate_text
from tests.fakes.fake_bodhan import tone_wav


def _page(width: int) -> Image.Image:
    im = Image.new("RGB", (width, 700), "white")
    ImageDraw.Draw(im).rectangle([30, 30, 300, 300], fill="black")
    return im


# ---------------------------------------------------------------- transcribe


async def test_long_audio_is_chunked_under_30s(ctx, fake, tmp_path):
    clip = tmp_path / "long.wav"
    tone_wav(clip, 185, 330, gaps=[(t, t + 0.5) for t in range(18, 185, 19)])
    res = await transcribe_audio(str(clip))
    sizes = [x["bytes"] for x in fake.state.inputs["transcribe"]]
    assert len(sizes) >= 7
    assert all((b - 44) / (2 * 16_000) <= 30 for b in sizes)
    assert res.complete and res.detected_script == "Telugu" and res.likely_languages == ["te"]
    assert res.usage.cost_inr == pytest.approx(185 / 60 * 0.10, rel=0.02)
    segs = json.loads(open(res.segments_path, encoding="utf-8").read())["segments"]
    assert segs[0]["start"] == 0 and segs[-1]["end"] == pytest.approx(185, abs=0.1)


async def test_cache_means_no_second_payment(ctx, fake, tmp_path):
    clip = tmp_path / "c.wav"
    tone_wav(clip, 12, 220)
    first = await transcribe_audio(str(clip), language="hi")
    calls = fake.state.calls["/v1/audio/transcriptions"]
    second = await transcribe_audio(str(clip), language="hi")
    assert fake.state.calls["/v1/audio/transcriptions"] == calls
    assert first.usage.cost_inr > 0 and second.usage.cost_inr == 0
    assert second.usage.by_model["indic-transcribe"].cached >= 1


async def test_retries_on_429_then_succeeds(ctx, fake, tmp_path):
    clip = tmp_path / "c.wav"
    tone_wav(clip, 5, 440)
    fake.state.fail_next["/v1/audio/transcriptions"] = [429, 503]
    res = await transcribe_audio(str(clip))
    assert res.complete and res.detected_script == "Tamil"
    assert fake.state.calls["/v1/audio/transcriptions"] == 3


async def test_persistent_5xx_becomes_structured_error(ctx, fake, tmp_path):
    clip = tmp_path / "c.wav"
    tone_wav(clip, 5, 440)
    fake.state.fail_next["/v1/audio/transcriptions"] = [500] * 10
    with pytest.raises(BodhanToolError) as ei:
        await transcribe_audio(str(clip))
    assert ei.value.code == "upstream_error" and ei.value.retryable


async def test_auth_error_is_explicit(ctx, fake, tmp_path):
    fake.state.valid_key = "another-key"
    with pytest.raises(BodhanToolError) as ei:
        await translate_text("hi", text="Hello there")
    assert ei.value.code == "auth_error" and "BODHAN_TRANSLATE_KEY" in ei.value.hint


async def test_video_without_audio_and_missing_file(ctx, tmp_path):
    with pytest.raises(BodhanToolError) as ei:
        await transcribe_audio(str(tmp_path / "nope.wav"))
    assert ei.value.code == "file_not_found"
    img = tmp_path / "x.png"
    _page(500).save(img)
    with pytest.raises(BodhanToolError) as ei:
        await transcribe_audio(str(img))
    assert ei.value.code in {"no_audio_stream", "unsupported_file_type"}


# ---------------------------------------------------------------- translate


async def test_unsupported_language_is_refused_without_calls(ctx, fake):
    with pytest.raises(BodhanToolError) as ei:
        await translate_text("French", text="Hello")
    assert ei.value.code == "unsupported_language"
    assert "fr" not in ei.value.details["supported"]
    assert fake.state.calls.get("translate", 0) == 0


async def test_truncated_translation_is_resplit(ctx, fake):
    text = (
        "TRUNCATE_ME This is the first sentence of a long paragraph. And here is the second sentence that follows it."
    )
    res = await translate_text("hi", text=text)
    sent = [r["text"] for r in fake.state.inputs["translate"]]
    assert text in sent and len(sent) >= 3  # original + two halves
    assert any("re-translated" in w for w in res.warnings)


async def test_markdown_table_preserved_and_numbers_kept(ctx, tmp_path):
    md = tmp_path / "w.md"
    md.write_text("# Title\n\n| Name | Score |\n|---|---|\n| Ram | 45 |\n| Sita | 50 |\n", encoding="utf-8")
    res = await translate_text("mr", input_path=str(md))
    out = open(res.output_path, encoding="utf-8").read()
    assert res.structure_preserved and res.coverage_ok
    assert "| मराठी | 45 |" in out and "|---|---|" in out
    assert res.target_script_share == 1.0


async def test_text_already_in_target_is_not_sent(ctx, fake):
    res = await translate_text("ta", text="இன்று பள்ளியில் கூட்டம் உள்ளது.")
    assert res.units_kept == 1 and fake.state.calls.get("translate", 0) == 0


async def test_long_document_split_for_context_window(ctx, fake):
    para = " ".join(
        f"Notice {i}: the school will remain closed tomorrow because of heavy rain in the city." for i in range(60)
    )
    await translate_text("bn", text=para)
    assert all(len(r["text"]) <= 1200 for r in fake.state.inputs["translate"])
    assert len(fake.state.inputs["translate"]) >= 4


async def test_segments_translation_keeps_timings_and_subtitles(ctx, tmp_path):
    seg = tmp_path / "s.json"
    seg.write_text(
        json.dumps(
            {
                "segments": [
                    {"start": 0, "end": 4.2, "text": "नमस्ते दोस्तों"},
                    {"start": 4.2, "end": 9.0, "text": "आज हम पढ़ेंगे"},
                ]
            }
        ),
        encoding="utf-8",
    )
    res = await translate_text("en", input_path=str(seg))
    out = json.loads(open(res.output_path, encoding="utf-8").read())["segments"]
    assert [(s["start"], s["end"]) for s in out] == [(0, 4.2), (4.2, 9.0)]
    assert out[0]["source_text"] == "नमस्ते दोस्तों"
    subs = await make_subtitles(res.output_path)
    body = open(subs.subtitle_path, encoding="utf-8").read()
    assert "00:00:00,000 --> 00:00:04,200" in body and subs.cues == 2


async def test_budget_refusal_happens_before_spending(ctx, fake):
    with pytest.raises(BodhanToolError) as ei:
        await translate_text("hi", text="Hello " * 2000, max_cost_inr=0.000001)
    assert ei.value.code == "budget_exceeded"
    assert fake.state.calls.get("translate", 0) == 0


# ---------------------------------------------------------------- speak


async def test_speech_is_chunked_joined_and_verified(ctx, fake, tmp_path):
    text = "আজ বিদ্যালয়ে সভা হবে। সবাই সময়মতো আসবেন। " * 6
    res = await synthesize_speech("bn", text=text, gender="female", format="wav")
    assert res.voice == "Ishita" and res.chunks >= 3
    assert all(len(r["input"]) <= 240 for r in fake.state.inputs["speak"])
    assert res.verification.all_passed and res.verification.checked == res.chunks
    with wave.open(res.audio_path) as wf:
        assert wf.getframerate() == 24_000 and wf.getnframes() / 24_000 == pytest.approx(res.duration_s, abs=0.05)


async def test_failed_chunk_is_repaired_with_smaller_inputs(ctx, fake):
    first = "আজ বিদ্যালয়ে সভা হবে। সবাই সময়মতো আসবেন।"
    fake.state.tts_glitch.add(first)  # its audio "repeats" when transcribed back
    res = await synthesize_speech("bn", text=first, format="wav")
    v = res.verification
    assert v.repaired == 1 and v.all_passed
    inputs = [r["input"] for r in fake.state.inputs["speak"]]
    assert inputs.count(first) == 1  # the same input was never sent twice


async def test_script_mismatch_and_bad_voice(ctx):
    with pytest.raises(BodhanToolError) as ei:
        await synthesize_speech("ta", text="This is English text.")
    assert ei.value.code == "script_mismatch" and "translate_text" in ei.value.hint
    with pytest.raises(BodhanToolError) as ei:
        await synthesize_speech("hi", text="नमस्ते।", voice="Morgan Freeman")
    assert ei.value.code == "unsupported_voice"
    with pytest.raises(BodhanToolError) as ei:
        await synthesize_speech("hi", text="नमस्ते।", style="opera")
    assert ei.value.code == "unsupported_voice"


async def test_mp3_output_is_labelled(ctx, tmp_path):
    res = await synthesize_speech("hi", text="आज मौसम अच्छा है।", format="mp3")
    import subprocess

    tags = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json", res.audio_path],
        capture_output=True,
        text=True,
    ).stdout
    assert "AI-generated" in tags


# ---------------------------------------------------------------- ocr


async def test_multipage_pdf_ocr_with_502_retry_and_blank_skip(ctx, fake, tmp_path):
    fake.state.ocr_by_width[1001] = "OVERFLOW"
    pdf = tmp_path / "chapter.pdf"
    pages = [_page(801), Image.new("RGB", (900, 700), "white"), _page(1001)]
    pages[0].save(pdf, save_all=True, append_images=pages[1:], resolution=200)  # renders back at the same pixel size
    res = await ocr_document(str(pdf))
    assert res.pages_total == 3 and res.complete
    assert [p.status for p in res.pages] == ["ok", "blank_skipped", "ok"]
    assert res.has_tables and res.detected_script == "Tamil"
    assert any(i["max_tokens"] == 4096 for i in fake.state.inputs["ocr"])
    text = open(res.output_path, encoding="utf-8").read()
    assert "<!-- page 1 -->" in text and "Recovered page" in text
    assert res.usage.cost_inr == pytest.approx(0.40)  # blank page free; the 502 attempt is not billed


def test_parse_pages():
    assert parse_pages("1-3,5", 6) == [0, 1, 2, 4]
    assert parse_pages(None, 2) == [0, 1]
    with pytest.raises(BodhanToolError):
        parse_pages("4-9", 5)


async def test_ocr_rejects_text_files(ctx, tmp_path):
    t = tmp_path / "a.txt"
    t.write_text("hello")
    with pytest.raises(BodhanToolError) as ei:
        await ocr_document(str(t))
    assert ei.value.code == "unsupported_file_type"


# ---------------------------------------------------------------- helpers


async def test_inspect_and_read(ctx, tmp_path):
    (tmp_path / "in").mkdir()
    tone_wav(tmp_path / "in" / "a.wav", 3, 220)
    (tmp_path / "in" / "t.txt").write_text("ఈ రోజు పాఠశాలలో సమావేశం ఉంది." * 100, encoding="utf-8")
    res = await inspect_files([str(tmp_path / "in"), str(tmp_path / "missing.mp3")])
    kinds = {f.kind for f in res.files}
    assert {"audio", "text", "missing"} <= kinds and res.missing
    txt = next(f for f in res.files if f.kind == "text")
    assert txt.detected_script == "Telugu" and txt.likely_languages == ["te"]
    part = await read_text(str(tmp_path / "in" / "t.txt"), max_chars=100)
    assert len(part.content) == 100 and part.next_offset == 100


async def test_translate_uses_chat_route_and_falls_back_on_404(ctx, fake):
    res = await translate_text("hi", text="Good morning")
    assert res.coverage_ok and fake.state.calls["/v1/chat/completions"] == 1
    fake.state.fail_next["/v1/chat/completions"] = [404]
    await translate_text("hi", text="Good evening")  # chat route 404s -> documented JSON route
    assert fake.state.calls["/translate"] == 1
    await translate_text("hi", text="Good night")  # the working route is remembered
    assert fake.state.calls["/translate"] == 2 and fake.state.calls["/v1/chat/completions"] == 2
