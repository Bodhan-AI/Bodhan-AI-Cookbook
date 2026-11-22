"""Tool-level tests against the fake Bodhan API: chunking, limits, caching, budgets, recovery."""
import json
import wave
from pathlib import Path

import pytest
from conftest import fake_translate_text, make_speech_file

from bodhan_mcp import tools
from bodhan_mcp.costs import tracker
from bodhan_mcp.errors import BudgetExceeded, ToolFailure


@pytest.fixture
def long_audio(tmp_path):
    p = tmp_path / "talk.wav"
    make_speech_file(p, [("tone", 12), ("silence", 1.2), ("tone", 15), ("silence", 1.2), ("tone", 20),
                         ("silence", 1.0), ("tone", 20)])
    return p


# ---------------------------------------------------------------- speech to text
async def test_long_audio_is_split_below_30s_and_covers_everything(fake, long_audio):
    fake.asr_text = lambda dur, lang, i: f"segment {i}"
    r = await tools.transcribe_audio(str(long_audio), language="Hindi")
    durs = [c["duration"] for c in fake.calls if c["path"] == "/v1/audio/transcriptions"]
    assert len(durs) >= 3 and max(durs) <= 30.0
    assert 66.0 <= sum(durs) <= 70.5            # silence is trimmed, speech is not
    assert all(c["language"] == "hi" for c in fake.calls)
    data = json.loads(Path(r.segments_path).read_text(encoding="utf-8"))
    assert [s["id"] for s in data["segments"]] == list(range(len(data["segments"])))
    assert data["segments"][0]["start"] == 0 and data["segments"][-1]["end"] == pytest.approx(70.4, abs=0.2)
    assert tracker.total == pytest.approx(sum(durs) / 60 * 0.10, rel=0.01)
    assert r.language_source == "given"


async def test_silent_stretches_are_skipped_not_hallucinated(fake, tmp_path):
    p = tmp_path / "gap.wav"
    make_speech_file(p, [("tone", 5), ("silence", 40), ("tone", 5)])
    r = await tools.transcribe_audio(str(p), language="ta")
    assert r.skipped_silent_chunks == 1
    sent = [c["duration"] for c in fake.calls if c["path"] == "/v1/audio/transcriptions"]
    assert len(sent) == 2 and sum(sent) < 11            # the 40 s of silence is never uploaded or billed


async def test_language_is_auto_detected_from_script(fake, long_audio):
    fake.asr_text = "இது ஒரு சோதனை"
    r = await tools.transcribe_audio(str(long_audio))
    assert r.language == "ta" and r.language_source == "auto-detected" and r.language_confidence == "high"
    first = [c for c in fake.calls if c["path"] == "/v1/audio/transcriptions"][0]
    assert first["language"] is None            # detection probe has no language hint
    assert all(c["language"] == "ta" for c in fake.calls[1:])


async def test_detect_audio_language_reports_ambiguity(fake, long_audio):
    fake.asr_text = "मैं आज बाजार जा रहा हूँ"
    r = await tools.detect_audio_language(str(long_audio))
    assert r.language == "hi" and "mr" in r.candidates and r.confidence != "high"


async def test_audio_errors_are_structured(fake, tmp_path):
    with pytest.raises(ToolFailure) as e:
        await tools.transcribe_audio(str(tmp_path / "missing.wav"))
    assert e.value.code == "file_not_found"
    junk = tmp_path / "junk.wav"
    junk.write_bytes(b"not audio")
    with pytest.raises(ToolFailure) as e:
        await tools.transcribe_audio(str(junk), language="hi")
    assert e.value.code == "media_decode_error"
    with pytest.raises(ToolFailure) as e:
        await tools.transcribe_audio("x.wav", language="French")
    assert e.value.code in ("unsupported_language", "file_not_found")


async def test_subtitles_from_segments(fake, long_audio):
    fake.asr_text = "This is a fairly long sentence that should be split into several readable subtitle cues for viewers."
    r = await tools.transcribe_audio(str(long_audio), language="en")
    s = await tools.write_subtitles(r.segments_path, "subs.en.srt")
    text = Path(s.path).read_text(encoding="utf-8")
    assert s.cues > 3 and "-->" in text and text.startswith("1\n00:00:00,000 --> ")


async def test_translate_segments_keeps_timings(fake, long_audio):
    fake.asr_text = "Hello there my friend."
    r = await tools.transcribe_audio(str(long_audio), language="en")
    t = await tools.translate_segments(r.segments_path, "ta")
    a = json.loads(Path(r.segments_path).read_text(encoding="utf-8"))["segments"]
    b = json.loads(Path(t.segments_path).read_text(encoding="utf-8"))["segments"]
    assert [(x["start"], x["end"]) for x in a] == [(x["start"], x["end"]) for x in b]
    assert all(x["source_text"] == "Hello there my friend." for x in b)


# ---------------------------------------------------------------- translation
DOC = ("# Lines and angles\n\n" + "A line has no thickness. It is straight. " * 60 +
       "\n\n| angle | degrees |\n|---|---|\n| right | 90 |\n| straight | 180 |\n\nThe sum is $a+b=180$ always.\n\n" +
       "Another paragraph with 25 items. " * 60)


async def test_long_markdown_is_chunked_and_structure_survives(fake, tmp_path):
    src = tmp_path / "doc.md"
    src.write_text(DOC, encoding="utf-8")
    r = await tools.translate_text("Tamil", input_path=str(src), output_path="doc.ta.md")
    assert r.chunks > 1 and r.checks_ok, r.issues
    out = Path(r.translation_path).read_text(encoding="utf-8")
    assert "| right | 90 |" in out.replace("வ", "").replace("ய", "") or "| 90 |" in out or "90" in out
    assert out.count("|---|---|") == 1 and "$a+b=180$" in out
    assert all(len(c["text"]) <= 4000 for c in fake.calls if c["path"] == "/translate")


async def test_truncated_output_is_retried_in_smaller_pieces(fake):
    text = "Sentence number one is here. " * 40
    fake.truncate_over = 500
    r = await tools.translate_text("ta", text=text)
    assert r.truncation_retries >= 1 and r.checks_ok, r.issues
    assert r.characters_out >= 0.9 * len(text)


async def test_unsupported_and_invalid_translate_inputs(fake):
    for kwargs, code in [({"target_language": "French", "text": "hi"}, "unsupported_language"),
                         ({"target_language": "bho", "text": "hi"}, "unsupported_language"),
                         ({"target_language": "ta"}, "invalid_arguments"),
                         ({"target_language": "ta", "text": "a", "input_path": "b"}, "invalid_arguments"),
                         ({"target_language": "ta", "text": "   "}, "empty_input"),
                         ({"target_language": "en", "text": "hi", "script": "roman"}, "invalid_arguments"),
                         ({"target_language": "ta", "text": "hi", "script": "weird"}, "invalid_arguments")]:
        with pytest.raises(ToolFailure) as e:
            await tools.translate_text(**kwargs)
        assert e.value.code == code, kwargs
    assert fake.count("/translate") == 0       # bad input never reaches the paid API


async def test_verify_translation_catches_omission(fake):
    src = "First sentence here. Second sentence here. Third sentence here. Fourth sentence here with 42 apples."
    good = fake_translate_text(src)
    assert (await tools.verify_translation("ta", translation=good, source=src)).ok
    bad = fake_translate_text("First sentence here.")
    r = await tools.verify_translation("ta", translation=bad, source=src)
    assert not r.ok and r.issues


# ---------------------------------------------------------------- OCR
@pytest.fixture
def pdf3(tmp_path):
    from PIL import Image
    ims = [Image.new("RGB", (400, 600), c) for c in ("white", "lightgray", "beige")]
    p = tmp_path / "book.pdf"
    ims[0].save(p, save_all=True, append_images=ims[1:])
    return p


async def test_pdf_pages_are_rendered_ocred_and_furniture_dropped(fake, pdf3):
    r = await tools.ocr_document(str(pdf3))
    assert r.page_count == 3 and fake.count("/v1/chat/completions") == 3
    md = Path(r.markdown_path).read_text(encoding="utf-8")
    assert md.count("# CHAPTER") == 3 and "www.example.com" not in md   # footer removed, titles become headings
    assert "| a | b |" in md and tracker.total == pytest.approx(0.6)
    assert "IGNORE PREVIOUS INSTRUCTIONS" in md                          # it is data; the agent must not obey it


async def test_ocr_page_ranges_and_errors(fake, pdf3, tmp_path):
    r = await tools.ocr_document(str(pdf3), pages="2-3")
    assert [p.page for p in r.pages] == [2, 3]
    for bad, code in [({"pages": "9"}, "invalid_arguments"), ({"pages": "x"}, "invalid_arguments"),
                      ({"table_format": "csv"}, "invalid_arguments")]:
        with pytest.raises(ToolFailure) as e:
            await tools.ocr_document(str(pdf3), **bad)
        assert e.value.code == code
    txt = tmp_path / "a.txt"
    txt.write_text("hi", encoding="utf-8")
    with pytest.raises(ToolFailure) as e:
        await tools.ocr_document(str(txt))
    assert e.value.code == "unsupported_file_type"


async def test_one_bad_page_does_not_lose_the_others(fake, pdf3):
    fake.fail["/v1/chat/completions"] = [502]
    r = await tools.ocr_document(str(pdf3))
    assert r.page_count == 3 and any(p.failed for p in r.pages) and r.needs_review


# ---------------------------------------------------------------- speech synthesis
async def test_tts_splits_into_short_requests_and_labels_output(fake, tmp_path):
    text = " ".join(f"This is sentence {i} for the narration test." for i in range(25))
    r = await tools.synthesize_speech("Tamil", text=text, gender="male", output_path="out.ta.mp3")
    reqs = [c for c in fake.calls if c["path"] == "/v1/audio/speech"]
    assert len(reqs) == r.chunks > 1 and all(len(c["text"]) <= 240 for c in reqs)
    assert all(c["voice"] == "Arun" and json.loads(c["instructions"]) == {"lang": "ta"} for c in reqs)
    assert r.voice == "Arun" and r.voice_assumed is False
    assert Path(r.audio_path).suffix == ".mp3" and Path(r.audio_path).stat().st_size > 1000
    assert tracker.total == pytest.approx(len(Path(r.spoken_text_path).read_text(encoding="utf-8")) / 10000 * 6, rel=0.01)
    from bodhan_mcp import audio
    assert abs(audio.probe_duration(r.audio_path) - r.duration_s) < 1.0
    meta = audio.run(["ffprobe", "-v", "error", "-show_entries", "format_tags=comment", "-of", "default=nw=1:nk=1",
                      r.audio_path]).stdout
    assert "AI-generated" in meta


async def test_tts_input_validation(fake):
    for kwargs, code in [({"language": "French", "text": "hi"}, "unsupported_language"),
                         ({"language": "ta", "text": "hi", "voice": "Nobody"}, "unknown_voice"),
                         ({"language": "ta", "text": "hi", "style": "sarcastic"}, "invalid_style"),
                         ({"language": "ta", "text": "!!! ---"}, "empty_input"),
                         ({"language": "ta", "text": "hi", "gender": "robot"}, "invalid_gender")]:
        with pytest.raises(ToolFailure) as e:
            await tools.synthesize_speech(**kwargs)
        assert e.value.code == code, kwargs
    assert fake.count("/v1/audio/speech") == 0


async def test_suspicious_chunk_is_resynthesised_in_halves(fake):
    calls = {"n": 0}
    orig = fake.handler

    def handler(req):
        resp = orig(req)
        if req.url.path == "/v1/audio/speech":
            calls["n"] += 1
        return resp
    fake.tts_cps = 60.0      # audio far too short for the text -> looks like omitted words
    r = await tools.synthesize_speech("hi", text="This sentence is long enough to trigger the sanity check on speed.")
    assert r.recovered_chunks == 1 and fake.count("/v1/audio/speech") == 3


async def test_verify_speech_passes_and_flags_repetition(fake, tmp_path):
    text = "Welcome to the lesson on lines and angles. Today we learn about parallel lines."
    r = await tools.synthesize_speech("hi", text=text, output_path="v.wav", loudness_normalize=False)
    fake.asr_text = fake.last_tts_text
    ok = await tools.verify_speech(r.audio_path, "hi", expected_path=r.spoken_text_path)
    assert ok.ok and ok.cer == 0
    # a second, different clip whose "audio" loops a phrase (ASR results are cached per audio, so use new audio)
    text2 = "Next we study transversals and the angles they make with two parallel lines."
    r2 = await tools.synthesize_speech("hi", text=text2, output_path="v2.wav", loudness_normalize=False)
    fake.asr_text = fake.last_tts_text + " parallel lines parallel lines parallel lines"
    bad = await tools.verify_speech(r2.audio_path, "hi", expected_path=r2.spoken_text_path)
    assert not bad.ok and bad.repetitions


# ---------------------------------------------------------------- cache, budget, resilience
async def test_same_input_is_never_paid_for_twice(fake):
    a = await tools.synthesize_speech("hi", text="One short line to read aloud.", output_path="a.mp3")
    b = await tools.synthesize_speech("hi", text="One short line to read aloud.", output_path="b.mp3")
    assert fake.count("/v1/audio/speech") == 1 and tracker.total == pytest.approx(29 / 10000 * 6)
    t1 = await tools.translate_text("ta", text="Same words again.")
    t2 = await tools.translate_text("ta", text="Same words again.")
    assert fake.count("/translate") == 1
    assert tracker.duplicate_requests() == 0


async def test_budget_is_enforced_before_spending(fake):
    tracker.set_limit(0.05)
    with pytest.raises(BudgetExceeded):
        await tools.synthesize_speech("hi", text="x" * 500 + ".")
    assert fake.count("/v1/audio/speech") == 0 and tracker.total == 0


async def test_rate_limits_and_server_errors_are_retried(fake):
    fake.fail["/translate"] = [429, 503]
    r = await tools.translate_text("hi", text="Please retry me.")
    assert fake.count("/translate") == 3 and r.checks_ok


async def test_persistent_failure_becomes_a_structured_error(fake):
    fake.fail["/translate"] = [503] * 10
    with pytest.raises(ToolFailure) as e:
        await tools.translate_text("hi", text="Never works.")
    assert e.value.code == "upstream_error" and e.value.retryable


async def test_a_401_after_earlier_success_is_retried_once(fake):
    await tools.translate_text("ta", text="First works.")
    fake.fail["/translate"] = [401]
    assert (await tools.translate_text("ta", text="Second also works.")).checks_ok


async def test_missing_key_and_bad_key(fake, monkeypatch):
    fake.fail["/translate"] = [401]
    with pytest.raises(ToolFailure) as e:
        await tools.translate_text("hi", text="bad key")
    assert e.value.code == "auth_error" and "BODHAN_TRANSLATE_KEY" in e.value.hint
    monkeypatch.delenv("BODHAN_TRANSLATE_KEY")
    with pytest.raises(ToolFailure) as e:
        await tools.translate_text("hi", text="no key")
    assert e.value.code == "missing_api_key"


async def test_truncated_translations_are_not_cached(fake):
    fake.truncate_over = 0                                          # every answer comes back truncated
    with pytest.raises(ToolFailure) as e:
        await tools.translate_text("ta", text="word " * 3)
    assert e.value.code == "translation_truncated"
    n = fake.count("/translate")
    fake.truncate_over = None
    await tools.translate_text("ta", text="word " * 3)
    assert fake.count("/translate") == n + 1


# ---------------------------------------------------------------- inspect / capabilities
async def test_inspect_and_capabilities(fake, long_audio, pdf3, tmp_path):
    a = await tools.inspect_file(str(long_audio))
    assert a.kind == "audio" and a.duration_s == pytest.approx(70.4, abs=0.2) and a.has_audio
    assert (await tools.inspect_file(str(pdf3))).pages == 3
    d = await tools.inspect_file(str(tmp_path))
    assert d.kind == "directory" and any(e["kind"] == "pdf" for e in d.entries)
    cap = await tools.list_capabilities()
    assert "fr" not in cap.translate_languages and "ta" in cap.speak_languages and len(cap.voices) >= 20
    with pytest.raises(ToolFailure):
        await tools.inspect_file(str(tmp_path / "nope"))


# ---------------------------------------------------------------- translate transports
async def test_translate_falls_back_to_the_chat_route_when_slash_translate_is_404(fake):
    fake.translate_404 = True
    r = await tools.translate_text("Tamil", text="Hello there, my friend.")
    assert r.checks_ok and fake.count("/translate") == 1                    # probed once ...
    chat = [c for c in fake.calls if c.get("via") == "chat"]
    assert len(chat) == 1 and chat[0]["target"] == "Tamil"                   # ... then the documented prompt format
    await tools.translate_text("Hindi", text="Another one.")
    assert fake.count("/translate") == 1                                     # remembered: no more 404 probes


async def test_chat_route_prompt_names_scripts_and_truncation(fake, monkeypatch):
    monkeypatch.setenv("BODHAN_TRANSLATE_TRANSPORT", "chat")
    await tools.translate_text("ks", text="Good morning.")
    await tools.translate_text("hi", text="Good evening.", script="roman")
    await tools.translate_text("hi", text="Good night.", script="codemix")
    assert [c["target"] for c in fake.calls if c.get("via") == "chat"] == [
        "Kashmiri (Perso-Arabic script)", "Hindi (Roman)", "Hindi (code-mixed)"]
    assert fake.count("/translate") == 0
    fake.truncate_over = 300                                                 # finish_reason == "length" is detected
    r = await tools.translate_text("ta", text="Sentence number one is here. " * 40)
    assert r.truncation_retries >= 1 and r.checks_ok, r.issues


async def test_chat_route_into_english_roundtrips(fake, monkeypatch):
    monkeypatch.setenv("BODHAN_TRANSLATE_TRANSPORT", "chat")
    r = await tools.translate_text("ta", text="Water boils at 100 degrees. It is safe to drink.")
    back = await tools.verify_translation("ta", translation_path=r.translation_path, source_path=r.source_path,
                                          back_translate=True)
    assert back.ok and back.back_translation_coverage > 0.9
