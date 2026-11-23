import json

import pytest

from bodhan_eval.dataset import content_type, load_dataset, normalize_lang


def write_lines(path, lines):
    path.write_bytes(b"\n".join(l if isinstance(l, bytes) else l.encode("utf-8") for l in lines) + b"\n")
    return path


def j(**obj):
    return json.dumps(obj, ensure_ascii=False)


@pytest.fixture
def messy(tmp_path):
    (tmp_path / "audio").mkdir()
    (tmp_path / "audio" / "a1.wav").write_bytes(b"RIFF")
    return write_lines(
        tmp_path / "data.jsonl",
        [
            "﻿" + j(id="a1", source="Water boils at 100 °C.", lang="HI", audio="audio/a1.wav", meta={"domain": "sci"}),
            "",  # blank lines are ignored, not counted
            j(id="a1", source="Water boils at 100 °C.", lang="ta"),  # same id, other language: fine
            j(id="a1", source="dup", lang="hi"),  # duplicate (id, lang)
            "{not json",
            "[1, 2]",
            j(id="b", lang="hi"),  # no source
            j(id="c", source="   ", lang="hi"),  # empty source
            j(id="d", source="x", lang="hindi"),  # bad lang code
            j(id=True, source="x", lang="hi"),  # bool is not an id
            b'{"id": "e", "source": "\xff", "lang": "hi"}',  # invalid UTF-8
            j(id=7, source="Int ids become strings.", lang="bn"),
            j(id="f", source="x", lang="bn", translation=5, audio="nope.wav", reference="ref"),
            j(id="g", source="Some text here.", lang="bn", translation="", labels=[
                {"type": "number", "severity": "major", "span": [0, 4]},
                {"type": "omission", "span": [3, 1]},
                {"type": "omission", "span": [0, 999]},
                {"severity": "minor"},
                {"type": "garbled", "span": [1.5, 30.0]},  # seconds: not bounded by text length
                "not a label",
            ]),
            j(id="h", source="x", lang="mni-beng", labels="oops"),
            j(id="i", source="Excluded by --langs.", lang="te"),
        ],
    )


def test_bad_lines_are_skipped_and_counted(messy):
    data = load_dataset(messy, ["hi", "ta", "bn", "mni-Beng"])
    assert [(i.id, i.lang) for i in data.items] == [
        ("a1", "hi"), ("a1", "ta"), ("7", "bn"), ("f", "bn"), ("g", "bn"), ("h", "mni-Beng")
    ]
    reasons = {s.line: s.reason for s in data.skipped}
    assert len(reasons) == 8
    assert "duplicate" in reasons[4] and "line 1" in reasons[4]
    assert reasons[5].startswith("invalid JSON")
    assert reasons[6] == "line is not a JSON object"
    assert "source" in reasons[7] and "source" in reasons[8]
    assert "lang" in reasons[9] and "id" in reasons[10]
    assert reasons[11] == "not valid UTF-8"
    assert data.filtered_out == 1


def test_fields_are_kept_or_dropped(messy):
    items = {i.key: i for i in load_dataset(messy).items}
    a1 = items[("a1", "hi")]
    assert a1.audio == messy.parent / "audio" / "a1.wav"  # relative to the dataset file
    assert a1.extra == {"meta": {"domain": "sci"}} and a1.warnings == []

    f = items[("f", "bn")]
    assert f.translation is None and f.audio is None and f.reference == "ref"
    assert len(f.warnings) == 2

    g = items[("g", "bn")]
    assert g.translation == ""  # an empty output is still an output
    assert [l["type"] for l in g.labels] == ["number", "garbled"]
    assert len(g.warnings) == 4

    assert items[("h", "mni-Beng")].labels == [] and len(items[("h", "mni-Beng")].warnings) == 1


def test_text_is_not_modified(tmp_path):
    src = "  Café́  with  spaces\n"
    data = load_dataset(write_lines(tmp_path / "d.jsonl", [j(id="x", source=src, lang="hi")]))
    assert data.items[0].source == src


@pytest.mark.parametrize(
    "code, expected",
    [("hi", "hi"), (" HI ", "hi"), ("mni-beng", "mni-Beng"), ("KS-deva", "ks-Deva"), ("kok", "kok"),
     ("hindi", None), ("h", None), ("hi-Latn-x", None), ("", None)],
)
def test_normalize_lang(code, expected):
    assert normalize_lang(code) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Water boils at 100 °C.", "plain"),
        ("It costs $5 and $10 today.", "plain"),
        ("## Results\n\nSome text.", "markdown"),
        ("| a | b |\n|---|---|\n| 1 | 2 |", "markdown"),
        ("Use the `print` function.", "markdown"),
        ("See [docs](https://example.org).", "markdown"),
        ("The energy is $E = mc^2$.", "latex"),
        ("Area: \\frac{1}{2}bh", "latex"),
        ("## Energy\n\n$$E = mc^2$$", "mixed"),
    ],
)
def test_content_type(text, expected):
    assert content_type(text) == expected
