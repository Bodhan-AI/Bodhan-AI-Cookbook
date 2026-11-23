"""Dataset and calibration-file loading: bad lines are counted, never fatal (TESTING.md §1)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from bodhan_eval.dataset import LoadReport, apply_calibration_file, load_dataset


def write_jsonl(path: Path, rows: list[Any]) -> Path:
    """Write rows to a JSONL file; a ``str`` row is written verbatim (to plant broken lines)."""
    lines = [r if isinstance(r, str) else json.dumps(r, ensure_ascii=False) for r in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def item(iid: str, lang: str = "hi", **kw: Any) -> dict:
    return {"id": iid, "source": f"Source sentence {iid}.", "lang": lang, **kw}


# ---------------------------------------------------------------------------
# load_dataset
# ---------------------------------------------------------------------------


def test_valid_items_load_with_optional_fields(tmp_path: Path) -> None:
    ds = write_jsonl(tmp_path / "d.jsonl", [item("a", translation="अनुवाद", reference="संदर्भ"), item("b", "ta")])
    items, rep = load_dataset(ds, None)
    assert [it.key for it in items] == ["a::hi", "b::ta"]
    assert items[0].translation == "अनुवाद" and items[0].reference == "संदर्भ"
    assert items[1].translation is None and items[1].labels is None
    assert rep.bad == [] and rep.notes == []


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        pytest.param('{"id": "x", "source": ', "invalid JSON", id="broken-json"),
        pytest.param("[1, 2, 3]", "not a JSON object", id="non-object"),
        pytest.param({"id": "x", "lang": "hi"}, "missing or empty required field", id="missing-source"),
        pytest.param({"id": "x", "source": "   ", "lang": "hi"}, "missing or empty required field", id="blank-source"),
        pytest.param({"source": "Text.", "lang": "hi"}, "missing or empty required field", id="missing-id"),
        pytest.param(
            {"id": "x", "source": "Text.", "lang": 7}, "missing or empty required field", id="non-string-lang"
        ),
    ],
)
def test_bad_line_is_counted_and_run_continues(tmp_path: Path, row: Any, reason: str) -> None:
    ds = write_jsonl(tmp_path / "d.jsonl", [item("ok1"), row, item("ok2")])
    items, rep = load_dataset(ds, None)
    assert [it.id for it in items] == ["ok1", "ok2"]
    assert len(rep.bad) == 1
    assert rep.bad[0]["line"] == 2
    assert reason in rep.bad[0]["reason"]


def test_blank_lines_are_ignored(tmp_path: Path) -> None:
    ds = tmp_path / "d.jsonl"
    ds.write_text(json.dumps(item("a")) + "\n\n   \n" + json.dumps(item("b")) + "\n", encoding="utf-8")
    items, rep = load_dataset(ds, None)
    assert len(items) == 2 and rep.bad == []


def test_duplicate_id_and_lang_is_bad_but_other_lang_is_fine(tmp_path: Path) -> None:
    ds = write_jsonl(tmp_path / "d.jsonl", [item("a", "hi"), item("a", "ta"), item("a", "hi")])
    items, rep = load_dataset(ds, None)
    assert [it.key for it in items] == ["a::hi", "a::ta"]
    assert rep.bad[0]["line"] == 3 and "duplicate" in rep.bad[0]["reason"]


def test_langs_filter_counts_filtered_items(tmp_path: Path) -> None:
    ds = write_jsonl(tmp_path / "d.jsonl", [item("a", "hi"), item("b", "ta"), item("c", "bn")])
    items, rep = load_dataset(ds, {"hi", "bn"})
    assert [it.lang for it in items] == ["hi", "bn"]
    assert rep.filtered_out == 1


def test_unknown_language_is_kept(tmp_path: Path) -> None:
    ds = write_jsonl(tmp_path / "d.jsonl", [item("a", "xx")])
    items, rep = load_dataset(ds, {"xx"})
    assert items[0].lang == "xx" and rep.bad == []


def test_region_codes_are_normalised_with_one_warning_per_code(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ds = write_jsonl(
        tmp_path / "d.jsonl", [item("a", "hi-IN"), item("b", "hi-IN"), item("c", "TA"), item("d", "ks-Deva")]
    )
    with caplog.at_level(logging.WARNING, logger="bodhan_eval.dataset"):
        items, rep = load_dataset(ds, {"hi", "ta", "ks-Deva"})
    assert [it.key for it in items] == ["a::hi", "b::hi", "c::ta", "d::ks-Deva"]
    assert rep.lang_aliases == {"hi-IN": "hi", "TA": "ta"}
    assert len(caplog.records) == 2


def test_normalised_codes_collide_as_duplicates(tmp_path: Path) -> None:
    ds = write_jsonl(tmp_path / "d.jsonl", [item("a", "hi"), item("a", "hi-IN")])
    items, rep = load_dataset(ds, None)
    assert len(items) == 1 and "duplicate" in rep.bad[0]["reason"]


def test_relative_audio_path_resolves_against_dataset_folder(tmp_path: Path) -> None:
    (tmp_path / "audio").mkdir()
    wav = tmp_path / "audio" / "a.wav"
    wav.write_bytes(b"RIFF")
    ds = write_jsonl(tmp_path / "d.jsonl", [item("a", audio="audio/a.wav")])
    items, rep = load_dataset(ds, None)
    assert items[0].audio == str(wav.resolve())
    assert rep.notes == []


def test_missing_audio_is_noted_and_item_kept(tmp_path: Path) -> None:
    ds = write_jsonl(tmp_path / "d.jsonl", [item("a", audio="audio/missing.wav")])
    items, rep = load_dataset(ds, None)
    assert items[0].audio is None
    assert rep.notes == [{"item": "a::hi", "reason": "audio file not found: audio/missing.wav"}]


def test_labels_unknown_types_counted_and_spans_validated(tmp_path: Path) -> None:
    labels = [
        {"type": "Number", "severity": "high", "span": [3, 7]},
        {"type": "vibes", "span": [0, 2]},
        {"type": "omission", "span": [9, 4]},  # reversed span -> dropped, label kept
        "not-a-dict",
    ]
    ds = write_jsonl(tmp_path / "d.jsonl", [item("a", translation="x" * 20, labels=labels)])
    items, rep = load_dataset(ds, None)
    labs = items[0].labels
    assert labs is not None and len(labs) == 3
    assert (labs[0].category, labs[0].severity, labs[0].span) == ("number", "critical", (3, 7))
    assert labs[1].category is None and labs[1].raw_type == "vibes"
    assert labs[2].span is None
    assert rep.unknown_label_types == {"vibes": 1}


def test_empty_labels_list_means_labelled_clean(tmp_path: Path) -> None:
    ds = write_jsonl(tmp_path / "d.jsonl", [item("a", labels=[]), item("b")])
    items, _ = load_dataset(ds, None)
    assert items[0].labels == [] and items[1].labels is None


@pytest.mark.parametrize(
    ("label", "default_on", "expected"),
    [
        pytest.param({"type": "omission", "span": [1, 2]}, "translation", ("translation", (1, 2)), id="default"),
        pytest.param({"type": "omission", "span": [1, 2]}, "source", ("source", (1, 2)), id="cli-default-source"),
        pytest.param(
            {"type": "omission", "span": [1, 2], "span_on": "SOURCE"}, "translation", ("source", (1, 2)), id="per-label"
        ),
        pytest.param(
            {"type": "omission", "source_span": [4, 6]}, "translation", ("source", (4, 6)), id="source-span-key"
        ),
        pytest.param(
            {"type": "omission", "span_on": "weird", "span": [1, 2]},
            "translation",
            ("translation", (1, 2)),
            id="unknown-side",
        ),
    ],
)
def test_label_span_on(tmp_path: Path, label: dict, default_on: str, expected: tuple) -> None:
    ds = write_jsonl(tmp_path / "d.jsonl", [item("a", labels=[label])])
    items, _ = load_dataset(ds, None, label_span_on=default_on)
    lab = items[0].labels[0]  # type: ignore[index]
    assert (lab.span_on, lab.span) == expected


def test_labels_not_a_list_is_noted(tmp_path: Path) -> None:
    ds = write_jsonl(tmp_path / "d.jsonl", [item("a", labels={"type": "omission"})])
    items, rep = load_dataset(ds, None)
    assert items[0].labels is None
    assert "not a list" in rep.notes[0]["reason"]


# ---------------------------------------------------------------------------
# apply_calibration_file: the three line shapes (K4)
# ---------------------------------------------------------------------------


@pytest.fixture
def dataset(tmp_path: Path) -> tuple[list, LoadReport]:
    ds = write_jsonl(tmp_path / "d.jsonl", [item("a", "hi"), item("a", "ta"), item("b", "hi"), item("c", "bn")])
    return load_dataset(ds, None)


NUM = [{"type": "number", "span": [0, 3]}]


def test_shape_full_item_in_dataset_attaches_labels(tmp_path: Path, dataset: tuple) -> None:
    items, rep = dataset
    cal = write_jsonl(tmp_path / "cal.jsonl", [item("b", "hi", labels=NUM)])
    added = apply_calibration_file(cal, items, None, rep, "translation")
    assert added == []
    assert items[2].labels is not None and items[2].labels[0].category == "number"


def test_shape_full_item_not_in_dataset_becomes_calibration_only(tmp_path: Path, dataset: tuple) -> None:
    items, rep = dataset
    cal = write_jsonl(tmp_path / "cal.jsonl", [item("z", "ta", translation="மொழிபெயர்ப்பு", labels=[])])
    added = apply_calibration_file(cal, items, None, rep, "translation")
    assert [(it.key, it.calibration_only, it.labels) for it in added] == [("z::ta", True, [])]


def test_shape_id_lang_labels_matches_exact_item(tmp_path: Path, dataset: tuple) -> None:
    items, rep = dataset
    cal = write_jsonl(tmp_path / "cal.jsonl", [{"id": "a", "lang": "ta-IN", "labels": NUM}])
    apply_calibration_file(cal, items, None, rep, "translation")
    assert items[0].labels is None  # a::hi untouched
    assert items[1].labels is not None and items[1].labels[0].category == "number"


def test_shape_id_lang_labels_with_unknown_pair_does_not_fall_back_to_id(tmp_path: Path, dataset: tuple) -> None:
    items, rep = dataset
    cal = write_jsonl(tmp_path / "cal.jsonl", [{"id": "c", "lang": "hi", "labels": NUM}])
    apply_calibration_file(cal, items, None, rep, "translation")
    assert items[3].labels is None
    assert "cannot match" in rep.notes[0]["reason"]


def test_shape_id_labels_matches_unique_id(tmp_path: Path, dataset: tuple) -> None:
    items, rep = dataset
    cal = write_jsonl(tmp_path / "cal.jsonl", [{"id": "c", "labels": []}])
    apply_calibration_file(cal, items, None, rep, "translation")
    assert items[3].labels == []


def test_shape_id_labels_ambiguous_id_is_noted(tmp_path: Path, dataset: tuple) -> None:
    items, rep = dataset
    cal = write_jsonl(tmp_path / "cal.jsonl", [{"id": "a", "labels": NUM}])
    apply_calibration_file(cal, items, None, rep, "translation")
    assert items[0].labels is None and items[1].labels is None
    assert rep.notes[0] == {"item": "a", "reason": "calibration line 1: cannot match to exactly one dataset item"}


def test_calibration_bad_lines_are_notes_not_fatal(tmp_path: Path, dataset: tuple) -> None:
    items, rep = dataset
    cal = write_jsonl(tmp_path / "cal.jsonl", ["{broken", {"id": "b"}, {"id": "c", "lang": "bn", "labels": NUM}])
    apply_calibration_file(cal, items, None, rep, "translation")
    assert items[3].labels is not None
    assert [n["item"] for n in rep.notes] == ["calibration line 1", "calibration line 2"]
    assert rep.bad == []  # bad counts are for the dataset only


def test_calibration_respects_langs_filter(tmp_path: Path, dataset: tuple) -> None:
    items, rep = dataset
    cal = write_jsonl(tmp_path / "cal.jsonl", [item("z", "te", labels=[]), {"id": "q", "lang": "te", "labels": []}])
    added = apply_calibration_file(cal, items, {"hi", "ta", "bn"}, rep, "translation")
    assert added == [] and rep.notes == []


def test_calibration_file_is_read_once(tmp_path: Path, dataset: tuple, monkeypatch: pytest.MonkeyPatch) -> None:
    items, rep = dataset
    cal = write_jsonl(tmp_path / "cal.jsonl", [item("z", "hi", labels=[]), {"id": "c", "labels": NUM}])
    opened: list[str] = []
    real_open = Path.open

    def counting_open(self: Path, *a: Any, **kw: Any) -> Any:
        opened.append(self.name)
        return real_open(self, *a, **kw)

    monkeypatch.setattr(Path, "open", counting_open)
    apply_calibration_file(cal, items, None, rep, "translation")
    assert opened.count("cal.jsonl") == 1
