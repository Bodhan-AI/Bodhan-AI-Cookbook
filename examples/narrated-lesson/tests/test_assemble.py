from bodhan_lesson.stages.assemble import assemble
from bodhan_lesson.stages.assemble.elements import pair_captions, unwrap_lines
from bodhan_lesson.stages.assemble.sections import MAX_SECTION_CHARS, slugify
from bodhan_lesson.stages.assemble.tables import join_split_tables
from bodhan_lesson.models import Element


def heading(make_block, text, label="Section-title", page=1, order=0, type="SectionHeader"):
    return make_block(text, type=type, label=label, page=page, order=order)


# ---- elements -----------------------------------------------------------------
def test_heading_levels_come_from_labels(make_block, make_page):
    page = make_page(1, [
        heading(make_block, "6.1 Intro", order=0),
        heading(make_block, "Sub", label="Sub-section-title", order=1),
        make_block("Body text.", order=2),
    ])
    lesson = assemble([page])
    assert [e.markdown for e in lesson.elements[:2]] == ["## 6.1 Intro", "### Sub"]


def test_consecutive_chapter_titles_merge(make_block, make_page):
    page = make_page(1, [
        heading(make_block, "CHAPTER 6", label="Chapter-title", type="Title", order=0),
        heading(make_block, "LINES AND ANGLES", label="Chapter-title", type="Title", order=1),
    ])
    lesson = assemble([page])
    assert lesson.elements[0].markdown == "# CHAPTER 6 LINES AND ANGLES"
    assert lesson.elements[0].block_ids == ["p001.b00", "p001.b01"]


def test_paragraph_unwraps_hard_lines_but_keeps_list_items():
    assert unwrap_lines("one two\nthree") == "one two three"
    assert unwrap_lines("(a) first :\n(i) x (ii) y\n(b) second") == "(a) first :\n(i) x (ii) y\n(b) second"


def test_equation_without_delimiters_is_wrapped_and_dollars_kept(make_block, make_page):
    page = make_page(1, [make_block(r"x = 1", type="Equation", order=0),
                         make_block(r"$$ a = b $$", type="Equation", order=1)])
    assert [e.markdown for e in assemble([page]).elements] == ["$$\nx = 1\n$$", "$$ a = b $$"]


def test_roles_and_doubtful_marker(make_block, make_page):
    q = make_block("1. Find x.", label="Question", order=0)
    d = make_block("garbled", order=1)
    d.doubtful, d.doubtful_reason = True, "latex_unbalanced"
    elements = assemble([make_page(1, [q, d])]).elements
    assert elements[0].role == "question"
    assert elements[1].markdown.startswith("<!-- doubtful p001.b01: latex_unbalanced -->") and elements[1].doubtful


def test_empty_text_blocks_are_skipped(make_block, make_page):
    assert assemble([make_page(1, [make_block("   ", order=0), make_block("Hi.", order=1)])]).elements[0].markdown == "Hi."


# ---- figures and captions ---------------------------------------------------
def fig(make_block, order, y0, y1, x0=100, x1=400):
    return make_block("", type="Figure", label="Diagram", order=order, bbox=(x0, y0, x1, y1))


def cap(make_block, text, order, y0, y1, x0=150, x1=350):
    return make_block(text, type="Caption", label="Image-caption", order=order, bbox=(x0, y0, x1, y1))


def test_caption_follows_its_figure(make_block, make_page):
    page = make_page(1, [fig(make_block, 0, 100, 300), cap(make_block, "Fig. 6.2 : Adjacent", 1, 310, 340)])
    elements = assemble([page]).elements
    assert [e.kind for e in elements] == ["figure", "caption"]
    assert elements[1].markdown == "*Fig. 6.2 : Adjacent*" and elements[1].paired_with == elements[0].id


def test_one_figure_can_own_several_captions_in_reading_order(make_block, make_page):
    page = make_page(1, [
        fig(make_block, 0, 100, 500),
        cap(make_block, "(ii) right", 1, 400, 430, x0=250, x1=350),
        cap(make_block, "(i) acute", 2, 402, 432, x0=100, x1=200),
        cap(make_block, "Fig. 6.1", 3, 505, 535),
    ])
    assert [e.markdown for e in assemble([page]).elements[1:]] == ["*(i) acute*", "*(ii) right*", "*Fig. 6.1*"]


def test_caption_between_stacked_figures_goes_to_the_figure_above(make_block, make_page):
    page = make_page(1, [
        fig(make_block, 0, 100, 300), cap(make_block, "Fig. A", 1, 312, 340),
        fig(make_block, 2, 341, 540), cap(make_block, "Fig. B", 3, 552, 580),
    ], height=1500)
    pairs = pair_captions(page)
    assert [c.text for c in pairs["p001.b00"]] == ["Fig. A"]
    assert [c.text for c in pairs["p001.b02"]] == ["Fig. B"]


def test_far_or_misaligned_caption_stays_in_place(make_block, make_page):
    page = make_page(1, [fig(make_block, 0, 100, 300), cap(make_block, "Far", 1, 900, 930),
                         cap(make_block, "Side", 2, 310, 340, x0=600, x1=700)])
    assert pair_captions(page) == {}
    assert [e.kind for e in assemble([page]).elements] == ["figure", "caption", "caption"]


def test_figure_link_is_relative_to_lesson(make_block, make_page):
    figure = fig(make_block, 0, 100, 300)
    figure.figure_path = "out\\source\\figures\\p001_b00.png"
    assert assemble([make_page(1, [figure])]).elements[0].markdown == "![](figures/p001_b00.png)"


# ---- tables -------------------------------------------------------------------
def table(markdown, page, id_="e1"):
    return Element(id=id_, kind="table", markdown=markdown, page=page, block_ids=[f"p{page}.b0"])


def test_table_rows_continue_across_pages():
    first = table("| a | b |\n|---|---|\n| 1 | 2 |", 1)
    second = table("| a | b |\n|---|---|\n| 3 | 4 |", 2, "e2")
    elements = [first, second]
    assert join_split_tables(elements) == 1 and len(elements) == 1
    assert elements[0].markdown == "| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |"


def test_headless_continuation_joins_but_different_table_does_not():
    elements = [table("| a | b |\n|---|---|\n| 1 | 2 |", 1), table("| 3 | 4 |", 2, "e2")]
    assert join_split_tables(elements) == 1
    other = [table("| a | b |\n|---|---|\n| 1 | 2 |", 1), table("| x | y |\n|---|---|\n| 3 | 4 |", 2, "e2")]
    assert join_split_tables(other) == 0
    same_page = [table("| a | b |\n|---|---|\n| 1 | 2 |", 1), table("| a | b |\n|---|---|\n| 3 | 4 |", 1, "e2")]
    assert join_split_tables(same_page) == 0


# ---- sections -------------------------------------------------------------------
def test_chapter_title_and_first_section_share_a_section(make_block, make_page):
    page = make_page(1, [
        heading(make_block, "CHAPTER 6", label="Chapter-title", type="Title", order=0),
        heading(make_block, "6.1 Introduction", order=1),
        make_block("Body.", order=2),
        heading(make_block, "6.2 Terms", order=3),
        make_block("More.", order=4),
    ])
    sections = assemble([page]).sections
    assert [s.title for s in sections] == ["6.1 Introduction", "6.2 Terms"]
    assert sections[0].slug == "01-6-1-introduction"


def test_content_before_first_heading_becomes_introduction(make_block, make_page):
    text = "A real opening paragraph that is long enough to stand as its own section of the lesson."
    page = make_page(1, [make_block(text, order=0), heading(make_block, "1. Start", order=1), make_block("Body.", order=2)])
    assert [s.title for s in assemble([page]).sections] == ["Introduction", "1. Start"]


def test_tiny_orphan_before_first_heading_is_folded_in(make_block, make_page):
    page = make_page(1, [make_block("$0962$", order=0), heading(make_block, "1. Start", order=1), make_block("Body.", order=2)])
    lesson = assemble([page])
    assert [s.title for s in lesson.sections] == ["1. Start"]


def test_long_section_is_split_into_parts(make_block, make_page):
    blocks = [heading(make_block, "Big", order=0)] + [
        make_block("word " * 300, order=i) for i in range(1, 6)  # ~1,500 chars each
    ]
    sections = assemble([make_page(1, blocks)]).sections
    assert len(sections) >= 2
    assert sections[1].title == "Big (part 2)"
    assert all(s.chars <= MAX_SECTION_CHARS for s in sections)


def test_footnotes_move_to_end_of_section(make_block, make_page):
    page = make_page(1, [
        heading(make_block, "S", order=0),
        make_block("Note text.", label="Footnote", order=1),
        make_block("Body.", order=2),
    ])
    assert [e.kind for e in assemble([page]).elements] == ["heading", "paragraph", "footnote"]


def test_slugify():
    assert slugify("6.4 Pairs of Angles!") == "6-4-pairs-of-angles"
    assert slugify("कोण") == "section"


# ---- rendering and traceability ---------------------------------------------
def test_line_map_matches_the_rendered_file(make_block, make_page):
    page = make_page(1, [heading(make_block, "6.1 Intro", order=0), make_block("a\n\nb", order=1), make_block("Last.", order=2)])
    lesson = assemble([page])
    lines = lesson.markdown.split("\n")
    for entry in lesson.element_map():
        start, end = entry["lines"]
        element = next(e for e in lesson.elements if e.id == entry["id"])
        assert "\n".join(lines[start - 1:end]) == element.markdown
