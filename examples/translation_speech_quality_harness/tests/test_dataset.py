import json

import pytest

from bodhan_eval.config import normalize_lang
from bodhan_eval.dataset import DatasetError, load


def write(tmp_path, lines, raw=False):
    p = tmp_path / "d.jsonl"
    p.write_bytes(b"\xef\xbb\xbf" + "\r\n".join(lines if raw else [json.dumps(x, ensure_ascii=False) for x in lines]).encode("utf-8"))
    return p


def test_broken_rows_are_skipped_not_fatal(tmp_path):
    p = write(tmp_path, [
        '{"id": "a", "source": "Hi.", "lang": "hi"}',
        "not json",
        "[1, 2]",
        '{"source": "x", "lang": "hi"}',
        '{"id": true, "source": "x", "lang": "hi"}',
        '{"id": "b", "source": "   ", "lang": "hi"}',
        '{"id": "c", "source": "x"}',
        '{"id": "d", "source": "x", "lang": "fr"}',
        '{"id": "a", "source": "again", "lang": "hi"}',
        "",
        '{"id": 7, "source": "x", "lang": "hi_IN", "translation": "", "extra": 1}',
    ], raw=True)
    items, skipped = load(p)
    assert [(i["id"], i["lang"]) for i in items] == [("a", "hi"), ("7", "hi")]
    assert items[1]["translation"] is None  # empty translation is treated as absent
    reasons = [s["reason"] for s in skipped]
    assert reasons == ["invalid JSON", "not a JSON object", "missing or invalid id", "missing or invalid id",
                       "missing or empty source", "missing lang", "unsupported lang 'fr'", "duplicate id and lang"]


def test_same_id_in_two_languages_is_two_items(tmp_path):
    items, _ = load(write(tmp_path, [{"id": "x", "source": "s", "lang": "hi"}, {"id": "x", "source": "s", "lang": "ta"}]))
    assert len(items) == 2


def test_langs_filter_counts_separately(tmp_path):
    items, skipped = load(write(tmp_path, [{"id": "x", "source": "s", "lang": "hi"}, {"id": "y", "source": "s", "lang": "te"}]), {"hi"})
    assert [i["id"] for i in items] == ["x"] and skipped[0]["reason"] == "lang not in --langs"


def test_audio_resolved_relative_to_dataset(tmp_path):
    (tmp_path / "a.wav").write_bytes(b"RIFF")
    items, _ = load(write(tmp_path, [{"id": "x", "source": "s", "lang": "hi", "audio": "a.wav"},
                                     {"id": "y", "source": "s", "lang": "hi", "audio": "nope.wav"}]))
    assert items[0]["audio"] == str(tmp_path / "a.wav")
    assert items[1]["audio"] is None and "audio file not found" in items[1]["notes"][0]


def test_labels_parsed_leniently(tmp_path):
    items, _ = load(write(tmp_path, [
        {"id": "x", "source": "s", "lang": "hi", "labels": [{"type": "number", "span": [3, 1]}, {"severity": "major"}, {"type": "format", "span": [0, 2]}]},
        {"id": "y", "source": "s", "lang": "hi", "labels": []},
        {"id": "z", "source": "s", "lang": "hi"},
    ]))
    assert items[0]["labels"] == [{"type": "number", "span": None}, {"type": "format", "span": [0, 2]}]
    assert items[1]["labels"] == [] and items[2]["labels"] is None


def test_invalid_utf8_is_replaced_and_noted(tmp_path):
    p = tmp_path / "d.jsonl"
    p.write_bytes(b'{"id": "x", "source": "bad \xff byte", "lang": "hi"}\n')
    items, _ = load(p)
    assert "�" in items[0]["source"] and items[0]["notes"]


def test_missing_dataset_is_fatal(tmp_path):
    with pytest.raises(DatasetError):
        load(tmp_path / "nope.jsonl")


@pytest.mark.parametrize("value,code", [("hi", "hi"), ("HI", "hi"), ("hi-in", "hi"), ("hi_IN", "hi"), ("Hindi", "hi"),
                                        ("ks-Deva", "ks-Deva"), ("mni-beng", "mni-Beng"), ("Tamil", "ta"), ("xx", None), ("", None), (5, None)])
def test_normalize_lang(value, code):
    assert normalize_lang(value) == code
