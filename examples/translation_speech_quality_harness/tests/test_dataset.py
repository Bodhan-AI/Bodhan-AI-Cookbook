import json

from bodhan_eval.categories import canonical_category, canonical_severity
from bodhan_eval.dataset import load_dataset
from bodhan_eval.languages import load_languages, resolve_langs


def _write(tmp_path, rows):
    p = tmp_path / "d.jsonl"
    p.write_text(
        "\n".join(r if isinstance(r, str) else json.dumps(r, ensure_ascii=False) for r in rows),
        encoding="utf-8",
    )
    return p


def test_bad_items_are_skipped_not_fatal(tmp_path):
    p = _write(
        tmp_path,
        [
            {"id": "a", "source": "Hi.", "lang": "hi"},
            "{not json",
            {"id": "b", "lang": "hi"},  # no source
            {"id": "a", "source": "dup", "lang": "hi"},
            {"id": "c", "source": "x", "lang": "xx"},  # unknown language
            {"id": "d", "source": "Yo.", "lang": "ta", "audio": "missing.wav"},
            [1, 2],
        ],
    )
    res = load_dataset(p, load_languages())
    assert [i.id for i in res.items] == ["a", "d"]
    assert res.items[1].audio is None  # missing audio dropped, item kept
    fatal = [pr for pr in res.problems if pr.fatal]
    assert len(fatal) == 5


def test_lang_filter_and_case(tmp_path):
    p = _write(
        tmp_path,
        [
            {"id": "a", "source": "s", "lang": "hi"},
            {"id": "b", "source": "s", "lang": "TA"},
            {"id": "c", "source": "s", "lang": "bn"},
        ],
    )
    res = load_dataset(p, load_languages(), ["hi", "ta"])
    assert [(i.id, i.lang) for i in res.items] == [("a", "hi"), ("b", "ta")]
    assert res.filtered_out == 1


def test_labels_parsed_and_aliased(tmp_path):
    p = _write(
        tmp_path,
        [
            {
                "id": "a",
                "source": "s",
                "lang": "hi",
                "labels": [
                    {"type": "Number or unit", "severity": "major", "span": [14, 20]},
                    {"type": "omission", "area": "speech", "severity": "high"},
                    {"type": "bogus"},
                ],
            },
            {"id": "b", "source": "s", "lang": "hi", "labels": []},
            {"id": "c", "source": "s", "lang": "hi"},
        ],
    )
    res = load_dataset(p, load_languages())
    a, b, c = res.items
    assert [(lab.category, lab.severity, lab.span) for lab in a.labels] == [
        ("number", "major", (14, 20)),
        ("speech_omission", "critical", None),
    ]
    assert b.labels == [] and b.is_labelled
    assert c.labels is None and not c.is_labelled


def test_category_aliases():
    assert canonical_category("Term or entity") == "term"
    assert canonical_category("wrong-script") == "wrong_language"
    assert canonical_category("truncation") == "speech_omission"
    assert canonical_category("speech_repetition") == "speech_repetition"
    assert canonical_category("omission") == "omission"
    assert canonical_category("omission", area="audio") == "speech_omission"
    assert canonical_category("nonsense") is None
    assert canonical_severity(None) == "major"


def test_registry_covers_speak_languages_and_resolves():
    reg = load_languages()
    speak = "as bn brx doi en gu hi kn kok ks mai ml mni mr ne or pa sa sat sd ta te ur".split()
    assert set(speak) <= set(reg)
    assert resolve_langs("hi, TA ,ks-deva", reg) == ["hi", "ta", "ks-Deva"]
    assert reg["ks-Deva"].speech_code == "ks"
    try:
        resolve_langs("hi,zz", reg)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "zz" in str(e)


def test_languages_have_default_voices():
    from bodhan_eval.languages import load_languages

    r = load_languages()
    assert (r["hi"].voice, r["ta"].voice, r["bn"].voice) == ("Kavya", "Anitha", "Ishita")
    assert r["en"].voice == "Kavya"  # falls back to defaults.voice
