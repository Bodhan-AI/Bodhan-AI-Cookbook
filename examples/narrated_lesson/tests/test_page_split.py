from PIL import Image, ImageDraw

from bodhan_lesson.page_split import merge_halves, split_halves


def test_cut_lands_on_a_blank_row_not_through_text(tmp_path):
    img = Image.new("RGB", (400, 1000), "white")
    d = ImageDraw.Draw(img)
    for y in range(380, 620, 12):                 # dense text lines across the middle band
        d.rectangle([20, y, 380, y + 6], fill="black")
    d.rectangle([0, 0, 0, 0], fill="white")
    src = tmp_path / "page_008.jpg"
    img.save(src)
    top, bottom, cut = split_halves(src)
    assert 400 <= cut <= 600
    assert Image.open(top).size[1] == cut and Image.open(bottom).size[1] == 1000 - cut
    # the cut row itself is blank
    assert img.convert("L").getpixel((200, cut)) == 255


def test_merge_shifts_bottom_blocks_and_orders_them_after_top():
    top = {"markdown": "A", "blocks": [{"order": 0, "bbox_xyxy": [0, 10, 5, 20], "label": "Paragraph"},
                                        {"order": 1, "bbox_xyxy": [0, 30, 5, 40], "label": "Paragraph"}]}
    bottom = {"markdown": "B", "blocks": [{"order": 0, "bbox_xyxy": [0, 10, 5, 20], "label": "Paragraph"}]}
    out = merge_halves(top, bottom, cut=500)
    assert out["markdown"] == "A\n\nB"
    assert out["blocks"][2]["order"] == 2 and out["blocks"][2]["bbox_xyxy"] == [0, 510, 5, 520]