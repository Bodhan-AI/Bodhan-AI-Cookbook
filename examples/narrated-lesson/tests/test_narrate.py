import json

import pytest

from bodhan_lesson import config
from bodhan_lesson.clients import llm
from bodhan_lesson.models import FigureDescription
from bodhan_lesson.stages.assemble import assemble
from bodhan_lesson.stages.narrate import narrate, validate
from bodhan_lesson.stages.narrate.grouping import EXAMPLE, EXERCISE, group_elements, make_batches
from bodhan_lesson.text import speech


class ScriptedClient:
    """Stands in for LlmClient: returns queued answers (or raises queued errors) in order."""

    settings = config.LlmSettings("http://x", "m", "k", None)

    def __init__(self, *answers):
        self.answers, self.sent = list(answers), []

    def complete(self, messages, **kwargs):
        self.sent.append(messages)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, str) else json.dumps(answer)


def units(*pairs):
    return {"units": [{"element_ids": ids, "text": text} for ids, text in pairs]}


def lesson_of(make_block, make_page, *blocks):
    return assemble([make_page(1, list(blocks))])


@pytest.fixture
def chapter(make_block, make_page):
    """A heading, a definition and an equation."""
    return lesson_of(
        make_block, make_page,
        make_block("6.4 Pairs of Angles", type="SectionHeader", label="Section-title", order=0),
        make_block("Two angles are adjacent if they share a common vertex.", order=1),
        make_block(r"$$\angle ABC = 90^\circ$$", type="Equation", order=2),
    )


# ---- grouping --------------------------------------------------------------------
def test_examples_and_problems_absorb_what_follows_until_the_next_group(make_block, make_page):
    lesson = lesson_of(
        make_block, make_page,
        make_block("6.4 Pairs", type="SectionHeader", label="Section-title", order=0),
        make_block("Theory.", order=1),
        make_block("Example 1 : find x.", label="Solved-example", order=2),
        make_block("Solution : x = 5", order=3),
        make_block(r"$$x = 5$$", type="Equation", order=4),
        make_block("Example 2 : find y.", label="Solved-example", order=5),
        make_block("EXERCISE 6.1", type="SectionHeader", label="Section-title", order=6),
        make_block("1. Find z.", label="Question", order=7),
        make_block("2. Find w.", label="Question", order=8),
    )
    groups = group_elements(lesson.elements)
    assert [(g.kind, len(g.elements)) for g in groups] == [
        ("heading", 1), ("explanation", 1), (EXAMPLE, 3), (EXAMPLE, 1), ("heading", 1), (EXERCISE, 1), (EXERCISE, 1)]


def test_batches_never_split_a_group(make_block, make_page):
    lesson = lesson_of(make_block, make_page, make_block("Example 1 : " + "word " * 100, label="Solved-example", order=0),
                       make_block("Solution " + "text " * 100, order=1),
                       make_block("Next", type="SectionHeader", label="Section-title", order=2))
    batches = make_batches(group_elements(lesson.elements), max_chars=100)
    assert [len(b.groups) for b in batches] == [1, 1]
    assert len(batches[0].groups[0].elements) == 2


# ---- validation -------------------------------------------------------------------
def test_check_flags_symbols_missing_elements_and_unknown_ids(chapter):
    batch = make_batches(group_elements(chapter.elements), 5000)[0]
    ids = batch.ids
    found, issues = validate.check_batch(units(([ids[0]], "Now (let us) learn."), (["nope"], "Ghost.")), batch)
    text = " ".join(issues)
    assert "symbols" in text and "not narrated" in text and "unknown element ids" in text


def test_check_flags_numbers_lost_from_an_equation(make_block, make_page):
    lesson = lesson_of(make_block, make_page, make_block(r"$$\angle POQ = 180^\circ$$", type="Equation", order=0))
    batch = make_batches(group_elements(lesson.elements), 5000)[0]
    _, issues = validate.check_batch(units((batch.ids, "Angle P O Q is a straight angle.")), batch)
    assert any("180" in i for i in issues)
    _, issues = validate.check_batch(units((batch.ids, "Angle P O Q equals one hundred and eighty degrees.")), batch)
    assert issues == []


def test_exercise_problem_over_the_word_cap_is_flagged(make_block, make_page):
    lesson = lesson_of(make_block, make_page, make_block("1. Find x.", label="Question", order=0))
    batch = make_batches(group_elements(lesson.elements), 5000)[0]
    _, issues = validate.check_batch(units((batch.ids, " ".join(["word"] * (config.EXERCISE_MAX_WORDS + 5)))), batch)
    assert any("exercise problem" in i and "maximum" in i for i in issues)


# ---- narrate end to end ------------------------------------------------------------
def test_good_answer_is_used_as_is(chapter):
    ids = [e.id for e in chapter.elements]
    answer = units(([ids[0]], "Now let us learn about pairs of angles."),
                   ([ids[1]], "Two angles are adjacent if they share a common vertex."),
                   ([ids[2]], "Angle A B C equals ninety degrees."))
    sections, stats = narrate(chapter, {}, ScriptedClient(answer))
    assert [u.origin for u in sections[0].units] == ["llm"] * 3
    assert [u.kind for u in sections[0].units] == ["heading", "explanation", "explanation"]
    assert stats.llm_retries == 0 and stats.fallback_units == 0
    assert sections[0].units[0].id == "s01.u01"


def test_problems_are_fed_back_and_the_corrected_answer_wins(chapter):
    ids = [e.id for e in chapter.elements]
    bad = units((ids, "Angle ABC = 90°."))
    good = units((ids, "Let us start. Angle A B C equals ninety degrees."))
    client = ScriptedClient(bad, good)
    sections, stats = narrate(chapter, {}, client)
    assert stats.llm_retries == 1 and sections[0].units[0].text.startswith("Let us start")
    feedback = client.sent[1][-1]["content"]
    assert "symbols" in feedback and client.sent[1][-2]["role"] == "assistant"


def test_unrepairable_llm_falls_back_to_rule_based_speech(chapter):
    errors = [llm.LlmError("no json")] * (1 + config.NARRATION_RETRIES)
    sections, stats = narrate(chapter, {}, ScriptedClient(*errors))
    texts = [u.text for u in sections[0].units]
    assert all(u.origin == "fallback" for u in sections[0].units)
    assert "Two angles are adjacent if they share a common vertex." in texts
    assert "angle A B C equals 90 degrees." in texts
    assert all(speech.forbidden_symbols(t) == [] for t in texts) and stats.batches_with_fallback == 1


def test_rejected_request_is_not_retried(chapter):
    client = ScriptedClient(llm.LlmError("bad request", status=400))
    sections, stats = narrate(chapter, {}, client)
    assert len(client.sent) == 1 and stats.llm_failures and sections[0].units[0].origin == "fallback"


def test_no_client_means_rule_based_narration(chapter):
    sections, stats = narrate(chapter, {}, None)
    assert all(u.origin == "fallback" for u in sections[0].units) and stats.llm_units == 0


def test_leftover_symbols_are_scrubbed_after_retries_run_out(chapter):
    ids = [e.id for e in chapter.elements]
    bad = units((ids, "Angle ABC = 90° (right angle)."))
    sections, _ = narrate(chapter, {}, ScriptedClient(bad, bad, bad))
    unit = sections[0].units[0]
    assert speech.forbidden_symbols(unit.text) == [] and "symbols scrubbed" in unit.warnings


def test_missing_elements_get_fallback_narration_in_lesson_order(chapter):
    ids = [e.id for e in chapter.elements]
    partial = units(([ids[2]], "Angle A B C equals ninety degrees."))
    sections, _ = narrate(chapter, {}, ScriptedClient(partial, partial, partial))
    assert [u.element_ids for u in sections[0].units] == [[ids[0]], [ids[1]], [ids[2]]]
    assert [u.origin for u in sections[0].units] == ["fallback", "fallback", "llm"]


def test_exercise_problem_is_trimmed_to_the_cap_when_the_llm_ignores_it(make_block, make_page):
    lesson = lesson_of(make_block, make_page, make_block("1. Find x.", label="Question", order=0))
    ids = [lesson.elements[0].id]
    long_text = " ".join(["Problem one, use the linear pair axiom."] * 20)
    sections, _ = narrate(lesson, {}, ScriptedClient(*[units((ids, long_text))] * 3))
    unit = sections[0].units[0]
    assert speech.word_count(unit.text) <= config.EXERCISE_MAX_WORDS and unit.kind == EXERCISE
    assert any("trimmed" in w for w in unit.warnings)


def test_doubtful_elements_are_skipped_and_never_sent(make_block, make_page):
    bad = make_block("garbled text", order=1)
    bad.doubtful, bad.doubtful_reason = True, "latex_unbalanced"
    lesson = lesson_of(make_block, make_page, make_block("Good sentence.", order=0), bad)
    client = ScriptedClient(units(([lesson.elements[0].id], "A good sentence.")))
    sections, _ = narrate(lesson, {}, client)
    assert "garbled" not in json.dumps(client.sent)
    assert sections[0].skipped[0]["element"] == lesson.elements[1].id


def test_figure_description_reaches_the_prompt_and_decorative_figures_are_dropped(make_block, make_page):
    fig = make_block("", type="Figure", label="Diagram", order=0, bbox=(0, 100, 200, 300))
    cap = make_block("Fig. 6.11", type="Caption", label="Image-caption", order=1, bbox=(0, 310, 200, 340))
    lesson = lesson_of(make_block, make_page, fig, cap)
    figure_id = lesson.elements[0].id
    described = {figure_id: FigureDescription(figure_id, [], "f.png", "Fig. 6.11", "Four rays start from point O.", False, [], "vision")}
    client = ScriptedClient(units(([figure_id], "Look at Figure six point eleven. Four rays start from point O.")))
    sections, _ = narrate(lesson, described, client)
    sent = client.sent[0][1]["content"]
    assert "Four rays start from point O." in sent and "Figure 6.11" in sent
    assert sections[0].units[0].origin == "llm"
    decorative = {figure_id: FigureDescription(figure_id, [], "f.png", "", "", True, [], "vision")}
    sections, _ = narrate(lesson, decorative, None)
    assert sections[0].skipped and sections[0].skipped[0]["reason"] == "decorative figure"


def test_first_batch_asks_for_an_opening_and_later_batches_get_continuity(make_block, make_page):
    blocks = [make_block("Sentence one. Sentence two.", order=0), make_block("x" * 3000, order=1)]
    lesson = lesson_of(make_block, make_page, *blocks)
    ids = [e.id for e in lesson.elements]
    client = ScriptedClient(units(([ids[0]], "Hello everyone. Sentence one. Sentence two.")), units(([ids[1]], "More.")))
    narrate(lesson, {}, client)
    first, second = (json.loads(m[1]["content"]) for m in client.sent)
    assert first["opening"] and first["previous_narration"] is None
    assert second["opening"] is None and "Sentence two." in second["previous_narration"]


def test_request_too_large_splits_the_batch_and_retries_each_half(chapter):
    ids = [e.id for e in chapter.elements]
    client = ScriptedClient(
        llm.LlmError("Request too large", status=413),
        units(([ids[0]], "Now let us learn about pairs of angles.")),
        units(([ids[1]], "Two angles are adjacent if they share a common vertex."),
              ([ids[2]], "Angle A B C equals ninety degrees.")),
    )
    sections, stats = narrate(chapter, {}, client)
    assert stats.splits == 1 and stats.llm_failures == []
    assert [u.origin for u in sections[0].units] == ["llm", "llm", "llm"]
    second_half = json.loads(client.sent[2][1]["content"])
    assert second_half["opening"] is None and "pairs of angles" in second_half["previous_narration"]


def test_single_group_too_large_falls_back_and_is_reported(make_block, make_page):
    lesson = lesson_of(make_block, make_page, make_block("A lone sentence.", order=0))
    sections, stats = narrate(lesson, {}, ScriptedClient(llm.LlmError("Request too large", status=413)))
    assert sections[0].units[0].origin == "fallback" and stats.llm_failures


def test_a_truncated_answer_splits_the_batch_like_a_too_large_request(chapter):
    ids = [e.id for e in chapter.elements]
    client = ScriptedClient(
        llm.TruncatedError("no content (finish_reason=length)"),
        units(([ids[0]], "Now let us learn about pairs of angles.")),
        units(([ids[1]], "Two angles are adjacent if they share a common vertex."),
              ([ids[2]], "Angle A B C equals ninety degrees.")),
    )
    sections, stats = narrate(chapter, {}, client)
    assert stats.splits == 1 and [u.origin for u in sections[0].units] == ["llm", "llm", "llm"]


def test_exercise_problem_that_states_a_computed_answer_is_flagged(make_block, make_page):
    lesson = lesson_of(make_block, make_page, make_block(r"6. Given $\angle XYZ = 64^\circ$, find angle XYQ.", label="Question", order=0))
    batch = make_batches(group_elements(lesson.elements), 5000)[0]
    _, issues = validate.check_batch(units((batch.ids, "Problem six. So angle X Y Q is thirty two degrees.")), batch)
    assert any("not in its text" in i and "32" in i for i in issues)
    method_only = "Problem six. Angle X Y Z is sixty four degrees. Find angle X Y Q: first use the linear pair, then halve."
    assert validate.check_batch(units((batch.ids, method_only)), batch)[1] == []


def test_numbers_from_the_figure_description_count_as_source(make_block, make_page):
    fig = make_block("", type="Figure", label="Diagram", order=1, bbox=(0, 100, 200, 300))
    lesson = lesson_of(make_block, make_page, make_block("1. Find x.", label="Question", order=0), fig)
    batch = make_batches(group_elements(lesson.elements), 5000, {lesson.elements[1].id: "Angle A O C is sixty degrees."})[0]
    assert validate.check_batch(units((batch.ids, "Problem one. The figure shows sixty degrees; find x.")), batch)[1] == []


def test_figures_move_to_the_first_text_that_mentions_them(make_block, make_page):
    from bodhan_lesson.stages.narrate.grouping import place_figures_at_first_reference
    lesson = lesson_of(make_block, make_page,
                       make_block("See Fig. 6.7 for adjacent angles.", order=0),
                       make_block("A second paragraph.", order=1),
                       make_block("A third paragraph.", order=2),
                       make_block("", type="Figure", label="Diagram", order=3, bbox=(0, 100, 200, 300)))
    fig = lesson.elements[3]
    placed = place_figures_at_first_reference(lesson.elements, {fig.id: "Figure 6.7"})
    assert [e.id for e in placed][:2] == [lesson.elements[0].id, fig.id]
    assert [e.id for e in place_figures_at_first_reference(lesson.elements, {})] == [e.id for e in lesson.elements]


def test_exercise_batches_use_the_focused_prompt_with_known_results(make_block, make_page):
    from bodhan_lesson.stages.narrate import prompts
    lesson = lesson_of(make_block, make_page,
                       make_block("**Axiom 6.1 :** If a ray stands on a line, the angles add up to 180 degrees.", order=0),
                       make_block("1. Find x.", label="Question", order=1))
    client = ScriptedClient(units(([lesson.elements[0].id], "The axiom."), ([lesson.elements[1].id], "Problem one. Find x.")))
    narrate(lesson, {}, client)
    system = client.sent[0][0]["content"]
    user = json.loads(client.sent[0][1]["content"])
    assert system.startswith("You are an experienced Indian school teacher recording an audio lesson for Class 9. For each practice")
    assert user["known_results"] and user["known_results"][0].startswith("Axiom 6.1")
