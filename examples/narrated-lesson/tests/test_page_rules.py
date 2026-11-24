from PIL import Image

from bodhan_lesson.stages.cleanup import cleanup_pages
from bodhan_lesson.stages.cleanup import page_rules as pr


def _title(make_block, text, page, order=0, y=20.0):
    return make_block(text, type="Title", label="Section-title", page=page, order=order, bbox=(100, y, 700, y + 30))


def test_rule3_drops_furniture_by_type_and_label(make_block, make_page):
    page = make_page(1, [
        make_block("97", type="PageNumber", label="Page-number", order=0),
        make_block("www.example.com", label="Website-link", order=1),
        make_block("Body text.", order=2),
    ])
    pr.drop_furniture([page], set())
    assert [b.text for b in page.blocks] == ["Body text."]
    assert len(page.dropped) == 2


def test_rule3_drops_repeated_header_but_keeps_first(make_block, make_page):
    pages = [make_page(n, [_title(make_block, f"LINES AND ANGLES {90 + n}", n), make_block("Body.", page=n, order=1)])
             for n in (1, 2, 3)]
    headers = pr.find_running_headers(pages)
    pr.drop_furniture(pages, headers)
    assert len(pages[0].blocks) == 2  # real chapter title kept
    assert len(pages[1].blocks) == 1 and len(pages[2].blocks) == 1


def test_rule3_drops_header_on_every_page_when_real_title_is_below_band(make_block, make_page):
    first = make_page(1, [_title(make_block, "LINES AND ANGLES", 1, y=475.0)])  # real chapter title, below band
    later = [make_page(n, [_title(make_block, "LINES AND ANGLES", n)]) for n in (2, 3, 4)]
    pages = [first, *later]
    pr.drop_furniture(pages, pr.find_running_headers(pages))
    assert len(first.blocks) == 1
    assert all(len(p.blocks) == 0 for p in later)


def test_rule3_ignores_titles_outside_top_band(make_block, make_page):
    pages = [make_page(n, [_title(make_block, "Summary", n, y=600.0)]) for n in (1, 2)]
    assert pr.find_running_headers(pages) == set()


def test_rule8_crops_figure(tmp_path, make_block, make_page):
    image_path = tmp_path / "page.png"
    Image.new("RGB", (800, 1000), "white").save(image_path)
    figure = make_block("", type="Picture", label="Diagram", bbox=(100, 100, 300, 250))
    page = make_page(1, [figure], image_path=str(image_path))
    pr.crop_figures(page, tmp_path / "figs", describer=lambda path, block: "A diagram.")
    cleaned = page.blocks[0]
    with Image.open(cleaned.figure_path) as crop:
        assert crop.size == (216, 166)  # 200x150 plus 8px padding each side
    assert cleaned.figure_description == "A diagram."


def test_rule10_joins_sentence_split_across_pages(make_block, make_page):
    first = make_page(1, [make_block("Two angles are adjacent if they share a common", page=1)])
    second = make_page(2, [make_block("vertex and a common arm.", page=2), make_block("Next.", page=2, order=1)])
    assert pr.join_page_breaks([first, second]) == 1
    assert first.blocks[-1].text == "Two angles are adjacent if they share a common vertex and a common arm."
    assert [b.text for b in second.blocks] == ["Next."]


def test_rule10_does_not_join_finished_sentence_or_heading(make_block, make_page):
    done = make_page(1, [make_block("A complete sentence.", page=1)])
    heading = make_page(2, [_title(make_block, "New section", 2, y=50)])
    assert pr.join_page_breaks([done, heading]) == 0
    open_ended = make_page(3, [make_block("Unfinished sentence", page=3)])
    assert pr.join_page_breaks([open_ended, heading]) == 0  # next block is a heading, not Text


def test_cleanup_pages_end_to_end(tmp_path, make_block, make_page):
    image_path = tmp_path / "p.png"
    Image.new("RGB", (800, 1000), "white").save(image_path)
    page = make_page(1, [
        make_block("97", type="PageNumber", order=0),
        make_block("2 Find the angles formed by two lines.", order=1, bbox=(100, 200, 700, 240)),
        make_block("Length 3\\cdot2 cm for $XY$ here.", order=2, bbox=(100, 300, 700, 340)),
    ], image_path=str(image_path))
    summary = cleanup_pages([page], tmp_path / "figs")
    texts = [b.text for b in page.blocks]
    assert texts[0].startswith("**Example 2:**")
    assert "3.2 cm" in texts[1] and "$XY$" in texts[1]
    assert summary["blocks_dropped"] == 1 and summary["blocks_kept"] == 2
