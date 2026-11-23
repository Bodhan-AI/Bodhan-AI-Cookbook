import json
import subprocess

import numpy as np
import pytest
from fake_bodhan import FakeBodhan
from PIL import Image

from bodhan_dub import config, onscreen
from bodhan_dub.bodhan import BodhanClient


def frame_at(video, t, path):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(t), "-i", str(video), "-frames:v", "1", str(path)], check=True)
    return np.asarray(Image.open(path).convert("L")).astype(int)


def extract(video_text, tmp_path, fake=None, key="o", **kw):
    fake = fake or FakeBodhan()
    client = BodhanClient(config.Keys("a", "b", "c", key), tmp_path / "cache", max_concurrency=1, http=fake.client,
                          base_url="https://api.bodhan.ai", backoff=0.01)
    (tmp_path / "w").mkdir(exist_ok=True)
    return fake, onscreen.extract(video_text, client, tmp_path / "w", 18.0, "sha1", **kw)


def test_extract_tracks_text_over_time_and_skips_the_rest(video_text, tmp_path):
    fake, o = extract(video_text, tmp_path, interval=1.0)
    assert len(o["items"]) == 2
    items = {i["text"]: i for i in o["items"]}
    assert set(items) == {"Revenue grew", "Open source"}          # footer and picture are not translated
    a, b = items["Revenue grew"], items["Open source"]
    assert 3.0 <= a["start"] <= 4.1 and 8.9 <= a["end"] <= 10.1                       # visible 4-9 s
    assert 9.0 <= b["start"] <= 10.1 and 13.9 <= b["end"] <= 15.1                     # visible 10-14 s
    assert a["bbox"][0] < 0.2 and b["bbox"][0] > 0.5 and a["bg"][0] < 60 and a["fg"][0] > 200
    assert o["skipped"].get("furniture_or_picture", 0) >= 1
    assert o["ocr_calls"] < o["frames"]                    # unchanged frames are not sent to Indic-OCR again
    assert 1 <= fake.ocr_calls <= o["ocr_calls"]           # identical frames are also deduplicated by the cache
    # second call is served from the cache: no new OCR requests
    extract(video_text, tmp_path, fake=fake, interval=1.0)
    assert 1 <= fake.ocr_calls <= o["ocr_calls"]           # identical frames are also deduplicated by the cache


def test_missing_key_raises_a_clear_error(video_text, tmp_path):
    with pytest.raises(RuntimeError, match="BODHAN_OCR_KEY"):
        extract(video_text, tmp_path, key="")


def test_ocr_request_shape_and_502_retries_with_more_tokens(tmp_path):
    fake = FakeBodhan()
    fake.ocr_fail = 5                                      # first call: every attempt answers 502 -> retried with a bigger limit
    client = BodhanClient(config.Keys("a", "b", "c", "o"), None, max_attempts=5, backoff=0, http=fake.client, base_url="https://api.bodhan.ai")
    buf = tmp_path / "f.png"
    Image.new("RGB", (64, 64), "black").save(buf)
    r = client.ocr(buf.read_bytes(), max_tokens=1000)
    assert r["blocks"] == [] and [q["model"] for q in fake.ocr_requests][0] == "indic-ocr"
    assert fake.ocr_requests[0]["max_tokens"] == 1000 and fake.ocr_requests[-1]["max_tokens"] == 2000


def test_pipeline_burns_translated_text_over_the_original_for_the_same_time(run, video_text, tmp_path):
    r = run(langs=("hi",), src=video_text, ocr_key="o", ocr=True, ocr_interval=1.0, out=tmp_path / "o1")
    assert r.rc == 0
    d = json.loads((r.out / "hi" / "onscreen.hi.json").read_text())
    rows = {x["original"]: x for x in d["items"]}
    assert rows["Revenue grew"]["translation"] == "[hi] Revenue grew" and rows["Revenue grew"]["status"] in ("ok", "tight")
    assert r.report["languages"]["hi"]["onscreen"]["burned_in"] == 2 and r.report["onscreen"]["items"] == 2
    assert r.report["onscreen"]["ocr_calls"] >= 1
    assert "footer" not in json.dumps(d).lower()
    vid = r.out / "hi" / "video.hi.mp4"
    orig = lambda t: frame_at(video_text, t, tmp_path / "o.png")                       # noqa: E731
    out = lambda t: frame_at(vid, t, tmp_path / "d.png")                               # noqa: E731
    box = (slice(140, 200), slice(30, 330))                                            # where "Revenue grew" sits
    assert np.abs(orig(6.5)[box] - out(6.5)[box]).mean() > 3                           # the text was redrawn while visible
    assert np.abs(orig(1.5) - out(1.5)).mean() < 3 and np.abs(orig(16.5) - out(16.5)).mean() < 3     # untouched otherwise
    assert np.abs(orig(12.5)[(slice(240, 300), slice(350, 620))] - out(12.5)[(slice(240, 300), slice(350, 620))]).mean() > 3
    assert np.abs(orig(12.5)[box] - out(12.5)[box]).mean() < 3                          # first text is gone by then
    # cached: a second run asks Indic-OCR nothing new
    n = r.fake.ocr_calls
    r2 = run(langs=("hi",), src=video_text, ocr_key="o", ocr=True, ocr_interval=1.0, out=r.out, fake=r.fake)
    assert r2.fake.ocr_calls == n


def test_edit_hide_and_missing_key_never_break_the_dub(run, video_text, tmp_path):
    out = tmp_path / "o2"
    (out / "hi").mkdir(parents=True)
    (out / "hi" / "overrides.json").write_text(json.dumps({"o0": {"text": "मेरा शीर्षक"}, "o1": {"hide": True}}))
    r = run(langs=("hi",), src=video_text, ocr_key="o", ocr=True, ocr_interval=1.0, out=out)
    rows = {x["id"]: x for x in json.loads((out / "hi" / "onscreen.hi.json").read_text())["items"]}
    assert rows[0]["translation"] == "मेरा शीर्षक" and rows[1]["status"] == "hidden"
    assert r.report["languages"]["hi"]["onscreen"]["burned_in"] == 1 and r.rc == 0
    r = run(langs=("hi",), src=video_text, ocr_key="", ocr=True, out=tmp_path / "o3")        # no OCR key
    assert r.rc == 0 and "BODHAN_OCR_KEY" in r.report["onscreen"]["error"] and (r.out / "hi" / "video.hi.mp4").exists()
    assert not (r.out / "hi" / "onscreen.hi.json").exists()


def test_block_filtering():
    ok = {"label": "Text", "text": "## **Revenue** grew\n- by 20 percent", "conf": 0.9}
    assert onscreen.block_reason(ok) is None and onscreen.clean_text(ok["text"]) == "Revenue grew by 20 percent"
    for b, why in (({"label": "Page-footer", "text": "x y"}, "furniture_or_picture"), ({"label": "Text", "text": ""}, "no_text"),
                   ({"label": "Text", "text": "Hello there", "conf": 0.1}, "low_confidence"),
                   ({"label": "Table", "text": "<table><tr><td>a</td></tr></table>"}, "table_not_translated"),
                   ({"label": "Text", "text": "नमस्ते दुनिया"}, "already_not_english"), ({"label": "Text", "text": "12 / 14"}, "no_letters"),
                   ({"label": "Text", "text": "$x^2$ + y"}, "formula")):
        assert onscreen.block_reason({"conf": 0.9, **b}) == why


def test_overlay_covers_the_original_box_and_stays_inside_the_frame(tmp_path):
    item = {"text": "Open source", "bbox": [0.85, 0.9, 0.99, 0.98], "bg": [20, 20, 20], "fg": [255, 255, 255]}   # near a corner
    font, _ = onscreen.find_font("hi")
    info = onscreen.render_overlay(item, "यह एक बहुत लंबा अनुवादित वाक्य है जो मूल से अधिक चौड़ा है और कई पंक्तियों में आना चाहिए", font, 640, 360, tmp_path / "o.png")
    assert info["x"] >= 0 and info["y"] >= 0 and info["x"] + info["w"] <= 640 and info["y"] + info["h"] <= 360
    assert info["x"] <= 0.85 * 640 and info["y"] + info["h"] >= 0.98 * 360 - 1       # still covers the original text box
    assert Image.open(tmp_path / "o.png").size == (info["w"], info["h"])


def test_find_font_uses_windows_nirmala_when_no_noto(tmp_path, monkeypatch):
    from bodhan_dub import onscreen
    fonts = tmp_path / "Fonts"
    fonts.mkdir()
    (fonts / "Nirmala.ttf").write_bytes(b"x")
    monkeypatch.setenv("WINDIR", str(tmp_path))
    monkeypatch.setattr(onscreen, "FONT_DIRS", ())
    path, note = onscreen.find_font("ta")
    assert note == "windows_nirmala" and path.endswith("Nirmala.ttf")


def _any_indic_font():
    from bodhan_dub import onscreen
    path, note = onscreen.find_font("hi")
    return path if path and note != "no_font" else None


def test_harfbuzz_font_shapes_indic_and_matches_pillow_widths(monkeypatch):
    import pytest
    from PIL import Image, ImageFont, features

    from bodhan_dub import onscreen
    font_path = _any_indic_font()
    if not font_path or not onscreen.have_harfbuzz():
        pytest.skip("needs an Indic font and uharfbuzz")
    f = onscreen.ShapedFont(font_path, 40)
    text = "क्षत्रिय विद्यार्थी"
    assert f.getlength(text) > f.getlength("क्षत्रिय") > 0
    img = Image.new("RGBA", (500, 80), (255, 255, 255, 255))
    f.draw(img, (5, 5), text, (0, 0, 0, 255))
    assert img.convert("L").getextrema()[0] < 60                      # ink was drawn
    if features.check("raqm"):                                        # shaping agrees with Raqm where Raqm exists
        r = ImageFont.truetype(font_path, 40, layout_engine=ImageFont.Layout.RAQM)
        assert abs(f.getlength(text) - r.getlength(text)) < 2
    monkeypatch.setattr(features, "check", lambda name: False)        # a Pillow without Raqm (Windows) uses HarfBuzz
    assert isinstance(onscreen.load_font(font_path, 30), onscreen.ShapedFont)


def test_overlay_renders_without_raqm(tmp_path, monkeypatch):
    import pytest
    from PIL import Image, features

    from bodhan_dub import onscreen
    font_path = _any_indic_font()
    if not font_path or not onscreen.have_harfbuzz():
        pytest.skip("needs an Indic font and uharfbuzz")
    monkeypatch.setattr(features, "check", lambda name: False)
    item = {"bbox": [0.1, 0.1, 0.5, 0.2], "text": "Revenue grew", "bg": (255, 255, 255), "fg": (0, 0, 0)}
    info = onscreen.render_overlay(item, "राजस्व बढ़ा क्षेत्र", font_path, 1280, 720, tmp_path / "o.png")
    im = Image.open(info["png"]).convert("L")
    assert im.size == (info["w"], info["h"]) and im.getextrema()[0] < 80
