"""Assembly-stage tests built from real OCR output of NCERT Class 9 Maths, Lines and Angles."""
from bodhan_lesson.assemble import (assemble, attach_markdown_text, clean_text, continues_sentence,
                                    is_catalog_code, overlap_ratio, squash)

H = 2000


def blk(order, label, conf, bbox, text, flags=None):
    return {"order": order, "label": label, "conf": conf, "bbox_xyxy": bbox, "text": text, "flags": flags or []}


def page1():
    blocks = [
        blk(0, "Paragraph", 0.717, [100, 436, 300, 471], r"$0962\text{CH}06$"),
        blk(1, "Chapter-title", 0.691, [534, 534, 700, 616], "CHAPTER 6"),
        blk(2, "Chapter-title", 0.854, [180, 633, 700, 687], "LINES AND ANGLES"),
        blk(3, "Section-title", 0.89, [167, 756, 639, 808], "6.1 Introduction"),
        blk(4, "Paragraph", 0.981, [169, 814, 1230, 1117], "In Chapter 5, you have studied that a minimum of two points are required to draw a line."),
        blk(5, "Paragraph", 0.974, [169, 1458, 1230, 1762], "To find the height of a tower or to find the distance of a ship from the light house, one needs to know the angle"),
    ]
    md = "\n\n".join(b["text"] for b in blocks)
    return {"page": 1, "status": "ok", "height": H, "markdown": md, "blocks": blocks}


def page2():
    md = (
        "formed between the horizontal and the line of sight. Plenty of other examples can be\n"
        "given where lines and angles are used.\n\n"
        "6.2 Basic Terms and Definitions\n\n"
        "Recall that a part (or portion) of a line with two end points is called a **line-segment**\n"
        "and a part of a line with one end point is called a **ray**.\n\n"
        "(i) acute angle : $0^{\\circ} < x < 90^{\\circ}$\n\n"
        "(ii) right angle : $y = 90^\\circ$\n\n"
        "(iii) obtuse angle : $90^{\\circ} < z < 180^{\\circ}$\n\n"
        "(iv) straight angle : $s = 180^\\circ$\n\n"
        "Fig. 6.1 : Types of Angles\n\n"
        "(v) reflex angle : $180^{\\circ} < t < 360^{\\circ}$\n"
    )
    blocks = [
        blk(0, "Paragraph", 0.953, [169, 306, 1231, 466], "formed between the horizontal and the line of sight. Plenty of other examples can be given where lines and angles are used."),
        blk(1, "Section-title", 0.899, [167, 576, 639, 630], "6.2 Basic Terms and Definitions"),
        blk(2, "Paragraph", 0.975, [169, 634, 1230, 929], "Recall that a part (or portion) of a line with twoend points is called a **line-segment** and a part of a line with one end point is called a **ray**."),
        blk(3, "Image-caption", 0.544, [174, 1437, 505, 1486], "(i) acute angle : $0^{\\circ} < x < 90^{\\circ}$", ["low_confidence:0.54"]),
        blk(4, "Diagram", 0.897, [173, 1189, 1232, 1756], ""),
        blk(5, "Image-caption", 0.561, [542, 1436, 824, 1486], "(ii) right angle : $y = 90^\\circ$", ["low_confidence:0.56"]),
        blk(6, "Image-caption", 0.554, [845, 1435, 1231, 1487], "(iii) obtuse angle : $90^{\\circ} < z < 180^{\\circ}$", ["low_confidence:0.55"]),
        blk(7, "Image-caption", 0.534, [218, 1711, 548, 1763], "(iv) straight angle : $s = 180^\\circ$", ["low_confidence:0.53"]),
        blk(8, "Image-caption", 0.842, [542, 1763, 856, 1814], "Fig. 6.1 : Types of Angles"),
        blk(9, "Image-caption", 0.54, [768, 1709, 1196, 1763], "(v) reflex angle : $180^{\\circ} < t < 360^{\\circ}$", ["low_confidence:0.54"]),
    ]
    return {"page": 2, "status": "ok", "height": H, "markdown": md, "blocks": blocks}


def running_header_page(n):
    blocks = [
        blk(0, "Header", 0.56, [100, 238, 400, 289], "", ["empty_text"]),
        blk(1, "Chapter-title", 0.52, [100, 240, 500, 287], "LINES AND ANGLES", ["low_confidence:0.52"]),
        blk(2, "Paragraph", 0.95, [169, 305, 1230, 540], f"Body text of page {n}."),
    ]
    return {"page": n, "status": "ok", "height": H, "markdown": "LINES AND ANGLES\n\n" + f"Body text of page {n}.", "blocks": blocks}


def run():
    pages = [page1(), page2(), running_header_page(3), running_header_page(4), running_header_page(5)]
    return assemble(pages)


def test_squash_and_alignment_repairs_missing_spaces():
    blocks = [blk(0, "Paragraph", 0.9, [0, 0, 1, 1], "with twoend points")]
    attach_markdown_text("with two end points", blocks)
    assert blocks[0]["md_text"] == "with two end points"
    assert squash("a b\nc") == "abc"


def test_catalog_code_is_dropped():
    assert is_catalog_code(r"$0962\text{CH}06$")
    assert not is_catalog_code("CHAPTER 6")
    elements, _, md, report = run()
    assert any(d["reason"] == "catalog_code" for d in report["dropped"])
    assert "0962" not in md


def test_running_header_removed_but_chapter_title_kept():
    elements, _, md, report = run()
    assert md.count("LINES AND ANGLES") == 1          # the real title on page 1 only
    assert any(d["reason"] in {"running_header_or_footer", "header_or_footer_label"} for d in report["dropped"])


def test_figure_labels_grouped_in_row_order_with_caption():
    elements, _, md, _ = run()
    fig = next(e for e in elements if e["type"] == "figure")
    assert fig["caption"] == "Fig. 6.1 : Types of Angles"
    assert [x.split(" ")[0] for x in fig["labels"]] == ["(i)", "(ii)", "(iii)", "(iv)", "(v)"]
    assert not any(e["flags"] for e in elements if e["type"] == "figure")


def test_paragraph_continues_across_pages():
    elements, _, md, report = run()
    assert report["merged_across_pages"] == [{"from_page": 1, "to_page": 2}]
    assert "one needs to know the angle formed between the horizontal" in md


def test_text_comes_from_markdown_with_correct_spacing():
    _, _, md, _ = run()
    assert "with two end points" in md and "twoend" not in md


def test_sections_start_at_headings():
    _, sections, _, _ = run()
    assert [s["title"] for s in sections][:2] == ["Introduction", "6.1 Introduction"]


def test_overlap_ratio():
    assert overlap_ratio([0, 0, 10, 10], [0, 0, 20, 20]) == 1.0
    assert overlap_ratio([0, 0, 10, 10], [50, 50, 60, 60]) == 0.0


def test_website_banner_and_year_are_dropped():
    page = running_header_page(6)
    page["blocks"].append(blk(3, "Paragraph", 0.9, [80, 60, 840, 110], "For more FREE DOWNLOADS, visit www.aspirationsinstitute.com"))
    page["blocks"].append(blk(4, "Paragraph", 0.9, [650, 1920, 740, 1950], "2020-21"))
    page["markdown"] += "\n\nFor more FREE DOWNLOADS, visit www.aspirationsinstitute.com\n\n2020-21"
    _, _, md, report = assemble([page1(), page2(), page])
    reasons = {d["reason"] for d in report["dropped"]}
    assert {"website_banner", "page_number_or_year"} <= reasons
    assert "aspirationsinstitute" not in md and "2020-21" not in md


def test_italic_words_in_math_mode_become_italics_but_variables_stay_math():
    out = clean_text("the $distance$ $between$ lines $l$, $m$ and $AB$ with $x + y$")
    assert "*distance* *between*" in out
    assert "$l$" in out and "$AB$" in out and "$x + y$" in out


def test_wide_spacing_is_collapsed():
    assert clean_text("Why?                    (1)") == "Why? (1)"


def test_no_merge_across_a_missing_page():
    p7 = {"page": 7, "status": "ok", "height": H, "markdown": "Solution : so x is equal to", "blocks": [
        blk(0, "Solved-example", 0.9, [169, 1500, 1230, 1700], "Solution : so x is equal to")]}
    p9 = {"page": 9, "status": "ok", "height": H, "markdown": "2. In Fig. 6.14, lines XY and MN intersect.", "blocks": [
        blk(0, "Question", 0.9, [169, 306, 1230, 400], "2. In Fig. 6.14, lines XY and MN intersect.")]}
    elements, _, md, report = assemble([p7, p9])
    assert report["merged_across_pages"] == []
    assert [e["type"] for e in elements] == ["example", "question"]


def test_continuation_rule():
    assert continues_sentence("formed between the horizontal")
    assert continues_sentence("\u0915\u094b\u0923 \u092c\u0928\u0924\u093e")      # uncased script
    assert not continues_sentence("2. In Fig. 6.14")
    assert not continues_sentence("Therefore, x")


def test_caption_without_diagram_still_marks_a_figure():
    page = {"page": 1, "status": "ok", "height": H, "markdown": "Fig. 6.25", "blocks": [
        blk(0, "Image-caption", 0.9, [169, 900, 400, 950], "Fig. 6.25")]}
    elements, _, md, _ = assemble([page])
    assert elements[0]["type"] == "figure" and "Fig. 6.25" in md