import pytest

from bodhan_lesson.text import speech


@pytest.mark.parametrize("n, words", [(0, "zero"), (7, "seven"), (13, "thirteen"), (40, "forty"), (90, "ninety"),
                                      (180, "one hundred and eighty"), (360, "three hundred and sixty"),
                                      (1500, "one thousand five hundred")])
def test_number_to_words(n, words):
    assert speech.number_to_words(n) == words


def test_forbidden_symbols_lists_what_a_listener_would_hear_as_noise():
    assert speech.forbidden_symbols("angle A B C equals ninety degrees.") == []
    assert speech.forbidden_symbols("x = (a + b)") == ["(", ")", "+", "="]
    assert speech.forbidden_symbols(r"$\angle$ 90° **bold**") == ["$", "*", "\\", "°"]


def test_trim_keeps_whole_sentences():
    text = "One two three. Four five six. Seven eight nine."
    assert speech.trim_to_words(text, 7) == "One two three. Four five six."
    assert speech.trim_to_words(text, 100) == text


def test_trim_cuts_a_single_long_sentence():
    assert speech.trim_to_words("a b c d e f g", 3) == "a b c."


def test_significant_numbers_skip_single_digits_and_latex_commands():
    assert speech.significant_numbers(r"\angle 1 + 180^\circ = 3.2 \times 2 \frac{1}{2} 40") == ["180", "3.2", "40"]


def test_missing_numbers_accepts_digits_or_words():
    source = r"\angle POQ = 180^\circ and 3.2"
    assert speech.missing_numbers(source, "angle P O Q is 180 degrees and 3.2") == []
    assert speech.missing_numbers(source, "one hundred and eighty degrees, three point two") == []
    assert speech.missing_numbers(source, "one hundred eighty degrees") == ["3.2"]
    assert speech.missing_numbers(source, "nothing here") == ["180", "3.2"]


def test_missing_numbers_does_not_match_inside_longer_numbers():
    assert speech.missing_numbers("90", "the angle is 190 degrees") == ["90"]


@pytest.mark.parametrize("latex, spoken", [
    (r"\angle \text{ABC} = \angle \text{ABD} + \angle \text{DBC}", "angle A B C equals angle A B D plus angle D B C"),
    (r"180^\circ", "180 degrees"),
    (r"\angle \text{TOP} + \angle \text{POQ} = 180^{\circ}", "angle T O P plus angle P O Q equals 180 degrees"),
    (r"\frac{x}{2} + 90^\circ - \frac{x}{2}", "x over 2 plus 90 degrees minus x over 2"),
    (r"\angle \text{ROS} = \frac{1}{2} \times \angle \text{POS}", "angle R O S equals 1 over 2 times angle P O S"),
    (r"x^2 + y^2 = r^2", "x squared plus y squared equals r squared"),
    (r"AB \parallel CD", "A B is parallel to C D"),
    (r"\overline{AB} \perp \overline{CD}", "line segment A B is perpendicular to line segment C D"),
    (r"\left( x + 1 \right)", "x plus 1"),
])
def test_latex_to_speech(latex, spoken):
    assert speech.latex_to_speech(latex)[0] == spoken


def test_latex_to_speech_counts_unknown_commands_and_never_leaves_symbols():
    text, unknown = speech.latex_to_speech(r"\weirdcommand x + (y) $z$")
    assert unknown == 1 and speech.forbidden_symbols(text) == []


def test_plain_speech_strips_markdown_and_speaks_math():
    markdown = "**Axiom 6.1 :** If a ray stands on a line, then $\\angle \\text{AOC} + \\angle \\text{BOC} = 180^\\circ$ (1) in Fig. 6.2."
    spoken = speech.plain_speech(markdown)
    assert spoken == ("Axiom 6.1 : If a ray stands on a line, then angle A O C plus angle B O C equals "
                      "180 degrees equation 1 in Figure 6.2.")
    assert speech.forbidden_symbols(spoken) == []


def test_plain_speech_drops_comments_images_and_headings_markers():
    assert speech.plain_speech("## 6.4 Pairs of Angles") == "6.4 Pairs of Angles"
    assert speech.plain_speech("<!-- doubtful p1.b1: x -->\n![](figures/a.png)") == ""


def test_scrub_symbols_speaks_or_drops_leftovers():
    cleaned = speech.scrub_symbols("x = 90° (right) *now*")
    assert speech.forbidden_symbols(cleaned) == [] and "equals" in cleaned and "degrees" in cleaned


@pytest.mark.parametrize("text, expected", [
    ("one hundred and eighty degrees", {"180"}),
    ("angle is forty degrees and thirty two more", {"40", "32"}),
    ("two hundred and twenty eight", {"228"}),
    ("one thousand five hundred", {"1500"}),
    ("Figure six point one three", {"6.13"}),
    ("Figure six point thirteen", {"6.13"}),
    ("five twelfths of seventy five", {"5", "75"}),
    ("angle 90 degrees, 3.2 cm", {"90", "3.2"}),
    ("sixty four and thirty", {"64", "30"}),
    ("no numbers here", set()),
])
def test_spoken_numbers(text, expected):
    assert speech.spoken_numbers(text) == expected


def test_spoken_numbers_round_trips_number_to_words():
    for n in (0, 7, 13, 40, 90, 101, 180, 360, 999, 1500, 20000):
        assert speech.spoken_numbers(speech.number_to_words(n)) == {str(n)}


def test_unexpected_numbers_catches_invented_results():
    source = r"If $\angle XYZ = 64^\circ$ and ray YQ bisects angle ZYP, find angle XYQ in Fig. 6.16."
    assert speech.unexpected_numbers(source, "Angle X Y Z is sixty four degrees; see Figure six point sixteen.") == []
    assert speech.unexpected_numbers(source, "So angle X Y Q is thirty two degrees and reflex is two hundred and twenty eight") == ["228", "32"]
    assert speech.unexpected_numbers(source, "Use the angle bisector property, then half of the angle.") == []


def test_spell_angle_labels_spaces_three_letter_labels_only():
    assert speech.spell_angle_labels("find BOE and reflex COE, then A O B") == "find B O E and reflex C O E, then A O B"
    assert speech.spell_angle_labels("Let OR be a ray") == "Let OR be a ray"
