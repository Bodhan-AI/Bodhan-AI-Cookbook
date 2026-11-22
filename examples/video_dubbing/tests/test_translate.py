import pytest

from bodhan_dub import translate as T


class FakeResp:
    def __init__(self, text, finish="stop", status=200):
        self.status_code, self.ok, self.text = status, status < 400, "err"
        self._j = {"choices": [{"message": {"content": text}, "finish_reason": finish}], "usage": {"completion_tokens": 7}}

    def json(self):
        return self._j


def units(n):
    return [{"id": i + 1, "sentence_id": i + 1, "text": f"Sentence number {i + 1}.", "start": i, "end": i + 1} for i in range(n)]


@pytest.fixture(autouse=True)
def fake_key(monkeypatch):
    monkeypatch.setattr(T, "get_key", lambda s: "k")


def test_script_share_and_repetition():
    assert T.script_share("यह एक परीक्षण है", "hi") > 0.9
    assert T.script_share("this is english", "hi") == 0.0
    assert T.has_repetition("a b c a b c a b c a b c") and T.has_repetition("हाँ हाँ हाँ हाँ")
    assert not T.has_repetition("a b c a b c a b d")


def test_check_translation_flags():
    ok = T.check_translation("Hello there friend.", "नमस्ते मेरे दोस्त।", "hi", "stop")
    assert ok["failures"] == []
    bad = T.check_translation("Hello there friend.", "Hello there friend.", "hi", "length")
    assert set(bad["failures"]) >= {"finish_ok", "script_ok"}
    assert "nonempty_ok" in T.check_translation("Hi.", "", "hi", "stop")["failures"]


def test_glossary_check():
    g = {"mitochondria": {"hi": "माइटोकॉन्ड्रिया"}}
    good = T.check_translation("The mitochondria work.", "माइटोकॉन्ड्रिया काम करते हैं।", "hi", "stop", g)
    bad = T.check_translation("The mitochondria work.", "सूत्रकणिका काम करती है।", "hi", "stop", g)
    assert good["checks"]["glossary_ok"] and "glossary_ok" in bad["failures"]


def test_unverified_language_rejected():
    for code in ("sat", "xx"):   # Santali is Indic-Speak preview tier, not production
        with pytest.raises(ValueError):
            T.lang_name(code)


def test_unit_mode_and_cache(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(T, "post_with_retry", lambda *a, **k: calls.append(1) or FakeResp("यह एक वाक्य है।"))
    segs = T.translate_units(units(3), "hi", tmp_path, mode="unit")
    assert [s["status"] for s in segs] == ["ok"] * 3 and len(calls) == 3
    T.translate_units(units(3), "hi", tmp_path, mode="unit")
    assert len(calls) == 3  # second run fully cached
    assert (tmp_path / "hi" / "segments.hi.json").is_file()


def test_batch_mode_aligns_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "post_with_retry", lambda *a, **k: FakeResp("एक वाक्य।\nदो वाक्य।\nतीन वाक्य।"))
    segs = T.translate_units(units(3), "hi", tmp_path, mode="batch", batch_size=3)
    assert [s["translation"] for s in segs] == ["एक वाक्य।", "दो वाक्य।", "तीन वाक्य।"]
    assert {s["via"] for s in segs} == {"batch"}


def test_batch_falls_back_when_line_count_wrong(tmp_path, monkeypatch):
    def post(url, key, json=None):
        content = json["messages"][0]["content"]
        return FakeResp("केवल एक पंक्ति।" if "\n\nSentence number 1.\n" in content else "एक इकाई।")

    monkeypatch.setattr(T, "post_with_retry", post)
    segs = T.translate_units(units(3), "hi", tmp_path, mode="batch", batch_size=3)
    assert {s["via"] for s in segs} == {"unit_fallback"} and len(segs) == 3


def test_api_failure_is_recorded_not_raised(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "post_with_retry", lambda *a, **k: FakeResp("", status=500))
    segs = T.translate_units(units(2), "hi", tmp_path, mode="unit")
    assert [s["status"] for s in segs] == ["failed", "failed"] and "api_error" in segs[0]["failures"]
