import json

import pytest
from bodhan_eval.cli import main
from bodhan_eval.judge import judgment
from bodhan_eval.providers import Unavailable
from bodhan_eval.runner import process_item


def test_ten_items_from_cli_are_deterministic_and_diffable(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    records = [{"id": str(i), "source": "Value 25", "lang": "en",
                "reference": "Value 25", "translation": "Value 35" if i % 2 else "Value 25"}
               for i in range(10)]
    dataset.write_text("\n".join(json.dumps(r) for r in records))
    out = tmp_path / "report"
    base = ["--dataset", str(dataset), "--langs", "en", "--out", str(out),
            "--offline", "--no-judge", "--no-roundtrips"]
    assert main(base) == 0
    first = {name: (out / name).read_bytes() for name in ("summary.json", "items.jsonl", "calibration.json")}
    assert main(base + ["--baseline", str(out)]) == 0
    for name, data in first.items():
        assert (out / name).read_bytes() == data
    assert "No changes" in (out / "diff.md").read_text()
    assert json.loads((out / "summary.json").read_text())["flagged_items"] == 5


def test_judge_rejects_invented_evidence_and_localizes_exact_quotes():
    class Client:
        def judge(self, messages, repeat):
            return {"errors": [
                {"type": "number", "severity": "major", "field": "translation", "quote": "35",
                 "explanation": "Source says 25", "confidence": 0.98, "checker_uncertain": False},
                {"type": "addition", "severity": "major", "field": "translation", "quote": "invented",
                 "explanation": "fake", "confidence": 1, "checker_uncertain": False},
            ]}
    accepted, review, rejected = judgment(Client(), {"source": "25", "translation": "35", "lang": "hi"})
    assert accepted[0]["span"] == [0, 2]
    assert not review
    assert len(rejected) == 1


def test_supplied_long_audio_is_chunked_below_30_seconds(tmp_path, monkeypatch):
    import numpy as np
    import soundfile as sf
    from bodhan_eval.runner import audio_check
    calls = []
    class Client:
        def transcribe(self, path, language, duration):
            calls.append(duration)
            return "one two"
    path = tmp_path / "sample.wav"
    sf.write(path, np.full(65 * 8000, 0.05), 8000)
    details, _ = audio_check(Client(), {"translation": "one two", "lang": "en"}, path)
    assert len(calls) == 3 and max(calls) <= 28
    assert details["stats"]["duration_seconds"] == 65


def test_unique_missing_source_quote_is_localized_without_inventing_text():
    class Client:
        def judge(self, messages, repeat):
            return {"errors": [{"type": "omission", "severity": "major", "field": "translation",
                                "quote": "after the meeting", "explanation": "A source clause is missing",
                                "confidence": 0.99, "checker_uncertain": False}]}
    findings, _, _ = judgment(Client(), {"source": "Call after the meeting", "translation": "Call", "lang": "hi"})
    assert findings[0]["field"] == "source"
    assert findings[0]["span"] == [5, 22]
    assert findings[0]["evidence"]["source_field_repaired"]


def test_reference_conflict_stays_visible_for_review(tmp_path):
    class Client:
        def judge(self, messages, repeat):
            return {"errors": [{"type": "addition", "severity": "major", "field": "translation",
                                "quote": "hello", "explanation": "Disputed addition",
                                "confidence": 1, "checker_uncertain": False}]}
    result = process_item({"id": "unseen", "source": "Hi", "translation": "hello", "reference": "hello", "lang": "en"},
                          client=Client(), dataset_dir=tmp_path, out=tmp_path, generate=False,
                          roundtrips=False, judge=True, judge_repeat=False, speech_selected=False)
    assert result["candidates"][0]["review_only"]
    assert result["candidates"][0]["evidence"]["quote"] == "hello"


def test_malformed_judge_schema_is_unavailable():
    class Client:
        def judge(self, messages, repeat):
            return []
    with pytest.raises(Unavailable):
        judgment(Client(), {"source": "hello", "translation": "hello", "lang": "en"})


@pytest.mark.parametrize("budget", ["nan", "inf", "-1"])
def test_invalid_budget_never_starts_a_run(tmp_path, budget):
    with pytest.raises(SystemExit) as error:
        main(["--dataset", "unused.jsonl", "--out", str(tmp_path), "--max-cost-inr", budget])
    assert error.value.code == 2


def test_generation_is_opt_in_and_missing_output_is_partial(tmp_path):
    class Client:
        def translate(self, text, language):
            return "generated fixture"
    item = {"id": "missing", "source": "Source text", "lang": "hi"}
    options = dict(client=Client(), dataset_dir=tmp_path, out=tmp_path, roundtrips=False,
                   judge=False, judge_repeat=False, speech_selected=False)
    assert process_item(item, generate=False, **options)["status"] == "partial"
    generated = process_item(item, generate=True, **options)
    assert generated["translation"] == "generated fixture"
    assert generated["checks"]["generation"] == "Bodhan Indic-Translate"


def test_bad_media_does_not_stop_other_items(tmp_path):
    from bodhan_eval.runner import run_items
    path = tmp_path / "bad.bin"
    path.write_bytes(b"not an audio stream")
    items = [{"id": "bad", "source": "Hello", "lang": "en", "translation": "Hello", "audio": str(path)},
             {"id": "good", "source": "Hello", "lang": "en", "translation": "Hello"}]
    rows = run_items(items, client=object(), dataset_dir=tmp_path, out=tmp_path,
                     generate=False, roundtrips=False, judge=False)
    assert [r["status"] for r in rows] == ["partial", "complete"]


def test_waveform_evidence_survives_recognizer_outage(tmp_path):
    import numpy as np
    import soundfile as sf
    from bodhan_eval.runner import audio_check
    class Client:
        def transcribe(self, *args):
            raise Unavailable("recognizer offline")
    path = tmp_path / "silent.wav"
    sf.write(path, np.zeros(8000), 8000)
    details, findings = audio_check(Client(), {"translation": "hello", "lang": "en"}, path)
    assert details["stats"]["silence_fraction"] == 1
    assert details["transcript"] is None
    assert findings[0]["type"] == "garbled_audio"


def test_embedded_human_labels_are_used_without_entering_check_inputs(tmp_path):
    dataset = tmp_path / "labelled.jsonl"
    dataset.write_text(json.dumps({"id": "human", "source": "Value 25", "translation": "Value 35", "lang": "en",
                                  "labels": [{"type": "number", "severity": "major", "span": [6, 8]}]}) + "\n")
    assert main(["--dataset", str(dataset), "--langs", "en", "--out", str(tmp_path / "report"),
                 "--offline", "--no-judge", "--no-roundtrips"]) == 0
    assert json.loads((tmp_path / "report/calibration.json").read_text())["human_items"] == 1
