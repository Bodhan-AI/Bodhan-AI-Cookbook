from bodhan_mcp.audio import plan_chunks
from bodhan_mcp.chunking import split_for_tts, split_markdown
from bodhan_mcp.voices import pick_voice


def test_tts_chunks_stay_short_and_keep_every_word():
    text = ("This is a sentence. " * 3) + ("word " * 120).strip() + ". Last one!"
    chunks = split_for_tts(text, max_chars=200)
    assert all(len(c) <= 200 for c in chunks)
    assert " ".join(chunks).split() == text.split()


def test_tts_splits_on_hindi_danda():
    chunks = split_for_tts("पहला वाक्य। दूसरा वाक्य। तीसरा वाक्य।")
    assert len(chunks) == 3


def test_markdown_table_is_never_cut_in_the_middle():
    text = "# Title\n\nSome text.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n\nThe end."
    chunks = split_markdown(text, max_chars=3000)
    assert len(chunks) == 1
    assert "| 3 | 4 |" in chunks[0]


def test_markdown_chunks_respect_the_limit_and_lose_nothing():
    paragraphs = [f"Paragraph number {i} " + "x" * 80 for i in range(10)]
    chunks = split_markdown("\n\n".join(paragraphs), max_chars=250)
    assert all(len(c) <= 250 for c in chunks)
    joined = "\n\n".join(chunks)
    assert all(p in joined for p in paragraphs)


def test_audio_windows_never_exceed_25_seconds_and_cut_at_silence():
    windows = plan_chunks(60.0, [20.0, 40.0])
    assert windows == [(0.0, 20.0), (20.0, 40.0), (40.0, 60.0)]
    assert all(end - start <= 25.0 for start, end in windows)


def test_audio_windows_without_silence_use_fixed_cuts():
    windows = plan_chunks(70.0, [])
    assert windows[0][0] == 0.0 and windows[-1][1] == 70.0
    assert all(end - start <= 25.0 for start, end in windows)
    assert all(a[1] == b[0] for a, b in zip(windows, windows[1:]))   # no gaps, no overlaps


def test_short_audio_is_a_single_window():
    assert plan_chunks(10.0, []) == [(0.0, 10.0)]


def test_voice_choice_and_errors():
    assert pick_voice("hi", "female") == ("Kavya", None)
    assert pick_voice("ta", "male")[0] == "Arun"
    assert pick_voice("fr")[0] is None
    assert pick_voice("ta", voice="Amit")[0] is None      # Amit is not a Tamil voice