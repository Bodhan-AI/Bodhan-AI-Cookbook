import json

from bodhan_lesson.translate_stage import (normalise_placeholders, protect, restore, run_translate,
                                           translate_table, translate_text, validate)


class FakeClient:
    """Pretends to translate by wrapping text in 'T(...)'. `mangle` can damage the result."""

    def __init__(self, mangle=None):
        self.mangle = mangle
        self.calls = []

    def translate(self, text, lang):
        self.calls.append(text)
        out = f"T({text})"
        if self.mangle:
            out = self.mangle(text, out)
        return {"text": out, "finish_reason": "stop", "truncated": False}


def test_math_is_protected_and_restored_exactly():
    text = r"Since $\angle AOB = 180^\circ$ and $$x + y = 5$$ we get $x$."
    masked, spans = protect(text)
    assert "$" not in masked and len(spans) == 3
    assert restore(masked, spans) == text


def test_spacing_damage_to_placeholders_is_repaired():
    assert normalise_placeholders("a ⟦ M2 ⟧ b ⟦M1⟧") == "a ⟦M2⟧ b ⟦M1⟧"


def test_validate_catches_missing_duplicated_and_changed_numbers():
    masked, spans = protect("Angle $x$ is 180 degrees and $y$ is 5.")
    assert validate(masked, "⟦M1⟧ 180 ⟦M2⟧ 5", len(spans)) == []
    assert "placeholder_2_count_0" in validate(masked, "⟦M1⟧ 180 5", len(spans))
    assert "numerals_changed" in validate(masked, "⟦M1⟧ 18 ⟦M2⟧ 5", len(spans))
    assert validate(masked, "⟦M1⟧ ⟦M1⟧ 180 ⟦M2⟧ 5", len(spans))


def test_native_digits_count_as_the_same_numbers():
    masked, spans = protect("The sum is 180 degrees.")
    assert validate(masked, "योग १८० डिग्री है", 0) == []


def test_good_translation_passes_through():
    out, issues = translate_text(FakeClient(), "Find $x$ if the sum is 90.", "hi")
    assert out == "T(Find $x$ if the sum is 90.)" and issues == []


def test_sentence_fallback_when_a_placeholder_is_lost():
    def drop_math_in_long_text(text, out):
        return out.replace("⟦M1⟧", "") if text.count("⟦") > 1 else out   # only multi-math text is damaged

    client = FakeClient(drop_math_in_long_text)
    out, issues = translate_text(client, "First $a$ holds. Second $b$ holds.", "hi")
    assert issues == [] and "$a$" in out and "$b$" in out


def test_unfixable_sentence_stays_english_and_is_reported():
    client = FakeClient(lambda text, out: out.replace("⟦M1⟧", ""))
    out, issues = translate_text(client, "Find $x$.", "hi")
    assert out == "Find $x$." and issues and issues[0].startswith("kept_in_english")


def test_tables_translate_words_not_markup():
    md = "| Angle | Value |\n|---|---|\n| acute | less than 90 |"
    out, issues = translate_table(FakeClient(), md, "hi")
    assert out.splitlines()[1] == "|---|---|" and "T(acute)" in out and issues == []
    html = "<table><tr><td>acute</td><td>90</td></tr></table>"
    out, _ = translate_table(FakeClient(), html, "hi")
    assert out.startswith("<table><tr><td>T(acute)</td>") and out.endswith("</tr></table>")


def test_run_translate_keeps_structure_and_equations(tmp_path):
    elements = [
        {"type": "title", "page": 1, "bbox": [0, 0, 1, 1], "flags": [], "text": "CHAPTER 6"},
        {"type": "heading", "page": 1, "bbox": [0, 0, 1, 1], "flags": [], "text": "6.1 Introduction"},
        {"type": "paragraph", "page": 1, "bbox": [0, 0, 1, 1], "flags": [], "text": "Angles add to $180^\\circ$."},
        {"type": "equation", "page": 1, "bbox": [0, 0, 1, 1], "flags": [], "text": "$$a + b = c$$"},
        {"type": "figure", "page": 1, "bbox": [0, 0, 1, 1], "flags": [], "caption": "Fig. 6.1", "labels": ["(i) acute"]},
    ]
    src = tmp_path / "source"
    src.mkdir()
    (src / "lesson.structure.json").write_text(json.dumps({"elements": elements, "sections": []}), encoding="utf-8")
    rep = run_translate(FakeClient(), tmp_path, "hi")
    assert rep["structure"]["same_order_and_types"] and rep["structure"]["equations_identical"]
    md = (tmp_path / "hi" / "lesson.hi.md").read_text(encoding="utf-8")
    assert "$$a + b = c$$" in md and "T(6.1 Introduction)" in md and "T(Fig. 6.1)" in md