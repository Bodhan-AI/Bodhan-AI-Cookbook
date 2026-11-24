import json
from hashlib import sha256
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageDraw, ImageFont

from backend.ocr.preprocess import PreprocessSettings, map_box_to_original, normalize_page
from bodhan_lesson import main


def read_record(out: Path, page_id: str = "p001") -> dict:
    path = out / "work" / "images" / f"{page_id}.transform.json"
    return json.loads(path.read_text(encoding="utf-8"))


def run_preprocess(inputs: Path, out: Path) -> None:
    assert main([
        "--input", str(inputs), "--langs", "hi", "--out", str(out),
        "--until", "preprocess", "--source-lang", "en",
    ]) == 0


def test_exif_orientation_is_recorded_and_mappable(tmp_path):
    path = tmp_path / "rotated.jpg"
    with Image.new("RGB", (120, 90), "white") as image:
        ImageDraw.Draw(image).rectangle((20, 15, 29, 19), fill="red")
        exif = image.getexif()
        exif[274] = 6
        image.save(path, exif=exif, quality=100, subsampling=0)

    with Image.open(path) as original:
        pixels, transform = normalize_page(original, PreprocessSettings("en"))
    with pixels:
        assert pixels.size == (90, 120)
        assert pixels.getpixel((72, 24))[0] > 220
        assert pixels.getexif().get(274) is None
    assert transform.original_size == (120, 90)
    assert transform.operations[0].name == "exif"
    assert transform.operations[0].applied
    assert transform.operations[0].exif_orientation == 6
    assert map_box_to_original((70, 20, 75, 30), transform) == (20, 15, 30, 20)


@pytest.mark.parametrize("turn", [Image.Transpose.ROTATE_90, Image.Transpose.ROTATE_270])
def test_landscape_english_requires_consistent_baseline_direction(turn):
    with Image.new("RGB", (700, 900), "white") as upright:
        draw = ImageDraw.Draw(upright)
        for y in range(140, 760, 56):
            for slot, x in enumerate(range(80, 620, 22)):
                draw.rectangle((x, y + (slot % 5) * 2, x + 10, y + 12), fill="black")
        with upright.transpose(turn) as sideways:
            pixels, transform = normalize_page(sideways, PreprocessSettings("en"))
        with pixels:
            assert pixels.size == upright.size
            assert ImageChops.difference(pixels, upright).getbbox() is None
    assert transform.operations[1].applied
    assert transform.operations[1].degrees_ccw == (
        -90 if turn == Image.Transpose.ROTATE_90 else 90
    )


def marker_center(image: Image.Image) -> tuple[float, float]:
    with ImageChops.subtract(image.getchannel("R"), image.getchannel("G")) as red_only:
        with red_only.point(lambda value: 255 if value > 70 else 0) as marker:
            box = marker.getbbox()
    assert box is not None
    left, top, right, bottom = box
    return (left + right) / 2, (top + bottom) / 2


def test_deskew_maps_a_known_marker_back_within_one_pixel():
    with Image.new("RGB", (720, 840), "white") as upright:
        draw = ImageDraw.Draw(upright)
        for y in range(115, 725, 40):
            for x in range(85, 635, 38):
                draw.rectangle((x, y, x + 9, y + 3), fill="black")
        draw.rectangle((530, 692, 544, 706), fill=(255, 160, 160))
        with upright.rotate(3, Image.Resampling.BICUBIC, fillcolor="white") as skewed:
            original_x, original_y = marker_center(skewed)
            pixels, transform = normalize_page(skewed, PreprocessSettings())
        with pixels:
            normalized_x, normalized_y = marker_center(pixels)

    deskew = next(operation for operation in transform.operations if operation.name == "deskew")
    assert deskew.applied
    assert deskew.degrees_ccw == pytest.approx(-3, abs=0.5)
    left, top, right, bottom = map_box_to_original(
        (normalized_x, normalized_y, normalized_x, normalized_y), transform
    )
    assert (left + right) / 2 == pytest.approx(original_x, abs=1)
    assert (top + bottom) / 2 == pytest.approx(original_y, abs=1)


def test_uncertain_landscape_keeps_margin_caption():
    with Image.new("RGB", (640, 420), "white") as original:
        draw = ImageDraw.Draw(original)
        draw.text((4, 395), "Figure 1. Two adjacent angles", fill="black",
                  font=ImageFont.load_default(size=20))
        pixels, transform = normalize_page(original, PreprocessSettings("en"))
        with pixels:
            assert pixels.size == original.size
            assert ImageChops.difference(pixels, original).getbbox() is None
    for operation in transform.operations:
        assert not operation.applied
        assert operation.reason
    assert map_box_to_original((-3, -2, 643, 423), transform) == (0, 0, 640, 420)


def test_same_inputs_and_settings_emit_identical_artifacts(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    with Image.new("RGB", (120, 160), "white") as image:
        image.save(inputs / "page.png")
    first_out, second_out = tmp_path / "first", tmp_path / "second"

    run_preprocess(inputs, first_out)
    run_preprocess(inputs, second_out)

    record = read_record(first_out)
    assert record == read_record(second_out)
    assert record["page_id"] == "p001"
    assert record["status"] == "ok"
    for path_key, hash_key in (("original_path", "input_sha256"),
                               ("normalized_path", "output_sha256")):
        relative_path = Path(record[path_key])
        assert not relative_path.is_absolute()
        first_image = (first_out / relative_path).read_bytes()
        assert first_image == (second_out / relative_path).read_bytes()
        assert record[hash_key] == sha256(first_image).hexdigest()


def test_corrupt_image_has_failed_record_and_keeps_next_page(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "page-1.png").write_bytes(b"invalid PNG")
    with Image.new("RGB", (100, 120), "white") as image:
        image.save(inputs / "page-2.png")
    out = tmp_path / "out"

    run_preprocess(inputs, out)

    language = json.loads((out / "source" / "language.json").read_text(encoding="utf-8"))
    assert (language["code"], language["method"], language["attempts"]) == ("en", "override", [])
    failed = read_record(out)
    assert failed["page_id"] == "p001"
    assert failed["status"] == "failed"
    assert failed["reason"]
    assert failed["normalized_path"] is None
    assert failed["output_sha256"] is None
    assert failed["transform"] is None
    assert not (out / "work" / "images" / "p001.png").exists()
    following = read_record(out, "p002")
    assert following["status"] == "ok"
    assert (out / following["normalized_path"]).is_file()
