"""Pure-logic tests: splitting, chunk planning, normalisation, structure checks, language handling."""
import pytest

from bodhan_mcp import audio, verify
from bodhan_mcp.errors import ToolFailure
from bodhan_mcp.langs import guess_language, normalize_lang, pick_voice
from bodhan_mcp.textsplit import chunk_blocks, chunk_for_tts, split_blocks, split_in_half


# ---------------------------------------------------------------- text splitting
def test_tts_chunks_respect_limit_and_keep_sentences_whole():
    text = " ".join(f"This is sentence number {i} of the test." for i in range(40))
    chunks = chunk_for_tts(text, 120)
    assert all(len(c) <= 120 for c in chunks)
    assert all(c.endswith(".") for c in chunks)            # nothing cut mid-sentence
    assert " ".join(chunks) == text


def test_tts_hard_splits_a_single_giant_sentence():
    giant = ", ".join(f"clause {i}" for i in range(100)) + "."
    chunks = chunk_for_tts(giant, 100)
    assert all(len(c) <= 100 for c in chunks) and len(chunks) > 1


def test_hindi_danda_is_a_sentence_boundary():
    chunks = chunk_for_tts("यह पहला वाक्य है। यह दूसरा वाक्य है।", 25)
    assert chunks == ["यह पहला वाक्य है।", "यह दूसरा वाक्य है।"]


def test_blocks_keep_tables_math_and_html_tables_whole():
    md = "# Title\n\npara one\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n$$\nx = 1\n\ny = 2\n$$\n\n<table>\n<tr><td>1</td></tr>\n\n<tr><td>2</td></tr>\n</table>\n\nend"
    blocks = split_blocks(md)
    assert any(b.startswith("| a | b |") and b.endswith("| 1 | 2 |") for b in blocks)
    assert any("x = 1" in b and "y = 2" in b for b in blocks)
    assert any("<tr><td>1" in b and "<tr><td>2" in b for b in blocks)


def test_chunk_blocks_never_splits_a_table_even_if_too_big():
    table = "\n".join(f"| row{i} | value |" for i in range(50))
    chunks = chunk_blocks("intro\n\n" + table + "\n\nouter", 200)
    assert any(table in c for c in chunks)


def test_split_in_half_always_makes_progress():
    for s in ["a b c d e f", "one. two. three. four.", "para1\n\npara2", "x" * 50]:
        halves = split_in_half(s)
        assert len(halves) == 2 and all(h for h in halves)


# ---------------------------------------------------------------- audio chunk planning
def test_plan_chunks_cover_all_speech_and_stay_under_window():
    sil = [(9.5, 10.5), (21.0, 22.0), (40.0, 41.0), (55.0, 56.0)]
    chunks = audio.plan_chunks(70.0, sil, max_len=28.0)
    assert chunks[0][0] == 0.0 and chunks[-1][1] == 70.0
    assert all(b - a <= 28.0 + 1e-6 for a, b in chunks)
    assert all(chunks[i][1] <= chunks[i + 1][0] + 1e-6 for i in range(len(chunks) - 1))   # ordered, no overlap
    for t in (5.0, 15.0, 30.0, 45.0, 60.0, 69.0):                                        # every speech second is covered
        assert any(a <= t <= b for a, b in chunks)


def test_plan_chunks_cut_inside_silences_not_inside_words():
    sil = [(20.0, 22.0), (45.0, 47.0)]
    chunks = audio.plan_chunks(60.0, sil, max_len=28.0)
    assert len(chunks) == 3
    for (a, b), (c, d) in zip(chunks, chunks[1:]):
        assert any(s_ <= b <= e_ + 0.01 and s_ - 0.01 <= c <= e_ for s_, e_ in sil)


def test_plan_chunks_hard_cut_without_silence_is_even():
    chunks = audio.plan_chunks(65.0, [], max_len=28.0)
    assert len(chunks) == 3 and all(b - a <= 28.0 + 1e-6 for a, b in chunks) and chunks[-1][1] == 65.0


def test_long_silence_is_never_sent():
    chunks = audio.plan_chunks(50.0, [(5.0, 45.0)], max_len=28.0)
    assert len(chunks) == 2 and sum(b - a for a, b in chunks) < 12
    assert audio.plan_chunks(30.0, [(0.0, 30.0)]) == []          # all silence -> nothing to transcribe


def test_short_audio_is_one_chunk_and_zero_length_is_empty():
    assert audio.plan_chunks(12.0, []) == [(0.0, 12.0)]
    assert audio.plan_chunks(0.0, []) == []


def test_silence_fraction():
    assert audio.silence_fraction(0, 10, [(0, 9.8)]) == pytest.approx(0.98)
    assert audio.silence_fraction(0, 10, []) == 0


# ---------------------------------------------------------------- normalisation and CER
def test_normalisation_maps_indic_digits_and_ignores_punctuation():
    assert verify.normalize("२५ किलो!") == verify.normalize("25 किलो")
    assert verify.normalize("১২৩") == "123"
    assert verify.normalize("A\u200bB, C.") == "ab c"


def test_cer_zero_for_identical_and_one_for_unrelated():
    assert verify.cer("வணக்கம் நண்பா", "வணக்கம்   நண்பா!") == 0
    assert verify.cer("abcdef", "uvwxyz") == 1.0


def test_speech_verdict_flags_repetition_and_omission():
    ref = "the cat sat on the mat and then it went to sleep in the warm sun"
    looped = "the cat sat on the mat and then it went to sleep in the warm sun the warm sun the warm sun"
    v = verify.speech_verdict(ref, looped, 0.4, 10)
    assert v["repetitions"] and not v["ok"]
    cut = "the cat sat on the mat"
    v = verify.speech_verdict(ref, cut, 0.4, 5)
    assert v["omissions"] and not v["ok"]
    assert verify.speech_verdict(ref, ref, 0.4, 10)["ok"]


# ---------------------------------------------------------------- translation checks
SRC = "# Heading\n\nWater boils at 100 degrees. There are 12 rows.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\nThe value is $x+y$ here. More text follows. Even more text follows. And one more."


def _ta(s):
    from conftest import fake_translate_text
    return fake_translate_text(s)


def test_good_translation_passes():
    assert verify.check_translation(SRC, _ta(SRC), "ta")["ok"]


def test_dropped_table_row_and_number_are_flagged():
    bad = _ta(SRC).replace("| 1 | 2 |", "").replace("100", "")
    r = verify.check_translation(SRC, bad, "ta")
    assert not r["ok"] and any("table_rows" in i for i in r["issues"])


def test_untranslated_output_is_flagged_as_wrong_script():
    r = verify.check_translation(SRC, SRC, "ta")
    assert not r["ok"]


def test_truncated_flag_and_empty():
    assert not verify.check_translation(SRC, _ta(SRC), "ta", truncated=True)["ok"]
    assert not verify.check_translation(SRC, "  ", "ta")["ok"]


def test_numbers_match_across_scripts():
    r = verify.check_translation("It costs 25 rupees. Total 1,000. " * 3, "இது ௨௫ ரூபாய். மொத்தம் 1000. " * 3, "ta")
    assert "numbers_missing_fraction" in r["metrics"] and r["metrics"]["numbers_missing_fraction"] == 0


# ---------------------------------------------------------------- languages
def test_language_names_and_codes():
    assert normalize_lang("Tamil", "speak") == "ta"
    assert normalize_lang("ta", "transcribe") == "ta"
    assert normalize_lang("BENGALI", "translate") == "bn"
    assert normalize_lang("ks-deva", "translate") == "ks-Deva"


def test_unsupported_languages_fail_with_a_hint():
    with pytest.raises(ToolFailure) as e:
        normalize_lang("French", "translate")
    assert e.value.code == "unsupported_language" and "French" in e.value.hint
    with pytest.raises(ToolFailure):
        normalize_lang("bho", "translate")      # speech list has it, translate does not
    with pytest.raises(ToolFailure):
        normalize_lang("mai-xx", "speak")
    with pytest.raises(ToolFailure):
        normalize_lang(None, "speak")


def test_voice_picking():
    assert pick_voice("ta", None, "male")[0] == "Arun"
    assert pick_voice("en", None, None)[0] == "Kavya"       # English falls back to a Hindi voice
    assert pick_voice("ta", "kavya", None)[0] == "Kavya"
    with pytest.raises(ToolFailure):
        pick_voice("ta", "Nobody", None)
    with pytest.raises(ToolFailure):
        pick_voice("ta", None, "robot")


def test_script_guessing():
    assert guess_language("இது ஒரு சோதனை")["language"] == "ta"
    assert guess_language("This is English")["language"] == "en"
    hi = guess_language("मैं आज बाजार जा रहा हूँ और यह सब ठीक है")
    assert hi["language"] == "hi" and hi["confidence"] in ("medium", "low")
    mr = guess_language("मी आज बाजारात जात आहे आणि हे सर्व ठीक आहे")
    assert mr["language"] == "mr"
    assert guess_language("123 !!!")["language"] is None
