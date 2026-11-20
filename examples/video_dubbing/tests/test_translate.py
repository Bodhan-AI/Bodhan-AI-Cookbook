"""Translation: batching with halving, placeholder protection of kept terms, and the code checks."""

from bodhan_dub.translate import check, protect, restore, translate, translate_text


class FakeBodhan:
    """Echoes text back, tagged; optionally merges two lines of any multi-line batch, or drops placeholders."""

    def __init__(self, merge=False, drop_placeholders=False):
        self.merge, self.drop, self.calls = merge, drop_placeholders, []

    def translate(self, text, lang, script="native"):
        self.calls.append(text)
        lines = text.split("\n")
        if self.merge and len(lines) > 1:
            lines = [lines[0] + " " + lines[1]] + lines[2:]
        out = "\n".join(f"<{lang}>{line}" for line in lines)
        return out.replace("⟦0⟧", "") if self.drop and "\n" in text else out


UNITS = [{"id": i, "start": i, "end": i + 1, "text": t} for i, t in enumerate(
    ["Bodhan builds models.", "Angle AOC is 70 degrees.", "Write it.", "The end."])]


def test_protect_and_restore_round_trip():
    text, slots = protect("Bodhan and NumPy, not Bodhanx", ["Bodhan", "NumPy"])
    assert text == "⟦0⟧ and ⟦1⟧, not Bodhanx"
    assert restore("<hi>⟦0⟧ और ⟦1⟧ஐ", slots) == "<hi>Bodhan और NumPyஐ"
    assert restore("<hi>⟦0⟧ only", slots) is None


def test_one_batch_when_lines_survive():
    fake = FakeBodhan()
    rows = translate(UNITS, [{"term": "Bodhan", "keep": True}], "hi", fake)
    assert len(fake.calls) == 1
    assert [r["translation"] for r in rows][0] == "<hi>Bodhan builds models."


def test_merged_lines_split_the_batch_until_aligned():
    fake = FakeBodhan(merge=True)
    rows = translate(UNITS, [], "ta", fake)
    assert [r["translation"] for r in rows] == [f"<ta>{u['text']}" for u in UNITS]


def test_lost_placeholders_fall_back_to_plain_translation():
    fake = FakeBodhan(drop_placeholders=True)
    rows = translate(UNITS, [{"term": "Bodhan", "keep": True}], "hi", fake)
    assert rows[0]["translation"] == "<hi>Bodhan builds models."


def test_check_flags_changed_numbers_and_lost_labels():
    assert check("Angle AOC is 70 degrees.", "कोण 70 डिग्री है।", []) == ["missing kept terms: ['AOC']"]
    assert "numbers differ" in check("Angle AOC is 70 degrees.", "कोण AOC 7 डिग्री है।", [])[0]


def test_single_text_translation_protects_kept_terms_too():
    assert translate_text("The Voyager probe.", ["Voyager"], "te", FakeBodhan()) == "<te>The Voyager probe."


class NoCalls:
    def translate(self, *args, **kwargs):
        raise AssertionError("no API call expected")


def test_nothing_to_translate_makes_no_call():
    assert translate([], [], "hi", NoCalls()) == []


def test_english_is_revoiced_without_translation():
    units = [{"id": 0, "start": 0.0, "end": 1.0, "text": "Two lines cross at O."}]
    assert translate(units, [], "en", NoCalls())[0]["translation"] == "Two lines cross at O."
