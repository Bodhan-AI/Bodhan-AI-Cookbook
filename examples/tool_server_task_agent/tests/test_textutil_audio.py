"""Pure-logic tests: script detection, chunking, window planning, SRT, error rate."""
from __future__ import annotations

import wave

from conftest import needs_ffmpeg, tone_wav

from bodhan_mcp import audio, textutil


def test_detect_language_by_script():
    assert textutil.detect_language("வணக்கம் இன்று")["language"] == "ta"
    assert textutil.detect_language("నమస్కారం ఈ రోజు")["language"] == "te"
    assert textutil.detect_language("ಕನ್ನಡ ಭಾಷೆ")["language"] == "kn"
    d = textutil.detect_language("कल विद्यालय बंद रहेगा। यह सूचना है")
    assert d["script"] == "Devanagari" and "hi" in d["candidates"] and d["confidence"] != "high"
    assert textutil.detect_language("आज पाऊस पडणार आहे आणि शाळा बंद आहे")["language"] == "mr"
    assert textutil.detect_language("12345 !!!")["language"] is None


def test_pack_sentences_respects_limit_and_boundaries():
    text = "यह पहला वाक्य है। " * 40 + "A very long sentence without any stop " * 20
    chunks = textutil.pack_sentences(text, 220)
    assert all(len(c) <= 220 for c in chunks)
    assert len(chunks) > 5
    assert chunks[0].endswith("।")  # cut at a sentence end, not mid-sentence


def test_hard_split_unspaced_text():
    assert all(len(c) <= 50 for c in textutil.pack_sentences("ক" * 500, 50))


def test_chunk_markdown_keeps_table_whole_and_splits_big_table_with_header():
    small = "# Title\n\nIntro paragraph.\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\nOutro."
    chunks = textutil.chunk_markdown(small, 1500)
    assert any("| A | B |" in c.text and "| 1 | 2 |" in c.text for c in chunks)
    rows = "\n".join(f"| item {i} | {i} |" for i in range(200))
    big = f"| Name | Qty |\n|---|---|\n{rows}"
    chunks = textutil.chunk_markdown(big, 500)
    assert len(chunks) > 3 and all(len(c.text) <= 500 for c in chunks)
    assert all(c.text.startswith("| Name | Qty |") for c in chunks)
    # re-join strips repeated headers: result is one table with every row once
    joined = textutil.join_chunks(chunks, [c.text for c in chunks])
    assert joined.count("| Name | Qty |") == 1 and joined.count("| item ") == 200


def test_chunk_markdown_splits_long_paragraphs_at_sentences():
    para = ("This is a sentence. " * 200).strip()
    chunks = textutil.chunk_markdown(para, 1500)
    assert len(chunks) >= 3 and all(len(c.text) <= 1500 for c in chunks)
    assert all(c.text.endswith(".") for c in chunks)


def test_math_protection_roundtrip():
    prot, spans = textutil.protect_math("Area is $\\pi r^2$ and $$x=1$$ ok")
    assert "$" not in prot and len(spans) == 2
    back, lost = textutil.restore_math(prot.replace("[[M0]]", "[[ M0 ]]"), spans)
    assert back == "Area is $\\pi r^2$ and $$x=1$$ ok" and lost == 0
    _, lost = textutil.restore_math("nothing left", spans)
    assert lost == 2


def test_srt_roundtrip_and_digits_and_cer():
    cues = [textutil.Cue(1, 0, 2.5, "Hello"), textutil.Cue(2, 3, 5.25, "World")]
    parsed = textutil.parse_srt(textutil.build_srt(cues))
    assert [(c.start, c.end, c.text) for c in parsed] == [(0, 2.5, "Hello"), (3, 5.25, "World")]
    assert textutil.numbers_in("कीमत ९० और 5") == ["90", "5"]
    assert textutil.cer("नमस्ते दुनिया", "नमस्ते दुनिया") == 0
    assert textutil.cer("abc def", "abc") > 0.3
    assert textutil.cer("कीमत 90", "कीमत नब्बे", ignore_digits=True) == 0.0


def test_repetition_detector():
    assert textutil.repeated_ngrams("a b c a b c a b c a b c d e f", 3, 3)
    assert not textutil.repeated_ngrams("one two three four five six seven", 3, 3)


def test_plan_windows_cuts_at_silence_and_never_exceeds_limit():
    sil = [20.5, 41.5]
    windows = audio.plan_windows(62.0, sil)
    assert windows == [(0.0, 20.5), (20.5, 41.5), (41.5, 62.0)]
    assert all(b - a <= audio.config.ASR_WINDOW_MAX_S for a, b in windows)


def test_plan_windows_hard_cut_without_silence_and_short_audio():
    windows = audio.plan_windows(70.0, [])
    assert all(b - a <= 28.0 + 1e-9 for a, b in windows) and windows[0][0] == 0 and windows[-1][1] == 70.0
    assert all(a2 == b1 for (_, b1), (a2, _) in zip(windows, windows[1:], strict=False))  # contiguous, no gaps
    assert audio.plan_windows(12.0, [5.0]) == [(0.0, 12.0)]


def test_write_window_and_concat(tmp_path):
    src = tone_wav(tmp_path / "a.wav", [(2, True), (1, False), (2, True)])
    w = audio.write_window(src, 1.0, 3.5, tmp_path / "w.wav")
    assert abs(audio.wav_duration(w) - 2.5) < 0.01
    joined = audio.concat_wavs([w, w], tmp_path / "j.wav", gap_s=0.5)
    assert abs(audio.wav_duration(joined) - 5.5) < 0.02
    with wave.open(str(joined), "rb") as fh:
        assert fh.getframerate() == 16000


@needs_ffmpeg
def test_silence_midpoints_detects_the_gap(tmp_path):
    src = tone_wav(tmp_path / "a.wav", [(3, True), (1, False), (3, True)])
    mids = audio.silence_midpoints(src)
    assert mids and abs(mids[0] - 3.5) < 0.3
