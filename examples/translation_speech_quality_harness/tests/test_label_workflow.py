import csv
import json
from pathlib import Path

import pytest

from bodhan_eval.dataset import read_dataset
from bodhan_eval.label_workflow import (
    build_label_queue,
    ingest_label_csv,
)


def test_build_label_queue_hindi_and_language_independent_mix(tmp_path: Path):
    items = [
        {
            "id": f"hi-{index}",
            "lang": "hi",
            "source": f"Source {index}",
            "translation": f"अनुवाद {index}",
        }
        for index in range(50)
    ]
    items += [
        {
            "id": f"ta-{index}",
            "lang": "ta",
            "source": f"Source ta {index}",
            "translation": f"மொழிபெயர்ப்பு {index}",
        }
        for index in range(20)
    ]
    items += [
        {
            "id": f"bn-{index}",
            "lang": "bn",
            "source": f"Source bn {index}",
            "translation": f"অনুবাদ {index}",
        }
        for index in range(20)
    ]

    queue = build_label_queue(items, seed=17, total=80)
    assert len(queue) == 80
    languages = {row["lang"] for row in queue}
    assert languages == {"hi", "ta", "bn"}
    assert sum(row["lang"] == "hi" for row in queue) == 40
    assert sum(row["lang"] in {"ta", "bn"} for row in queue) == 40


def test_ingest_label_csv_validates_type_span_and_writes_labels(tmp_path: Path):
    queue = tmp_path / "labels.csv"
    with queue.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "id",
                "lang",
                "source",
                "translation",
                "candidate_category",
                "candidate_severity",
                "type",
                "severity",
                "span_start",
                "span_end",
                "reviewed_clean",
                "notes",
            ],
        )
        writer.writeheader()
        writer.writerow({
            "id": "h1",
            "lang": "hi",
            "source": "The value is 100.",
            "translation": "मान 100 है।",
            "candidate_category": "number",
            "candidate_severity": "major",
            "type": "number",
            "severity": "major",
            "span_start": "2",
            "span_end": "5",
            "reviewed_clean": "false",
            "notes": "",
        })
        writer.writerow({
            "id": "h2",
            "lang": "hi",
            "source": "The sky is blue.",
            "translation": "आकाश नीला है।",
            "candidate_category": "",
            "candidate_severity": "",
            "type": "",
            "severity": "",
            "span_start": "",
            "span_end": "",
            "reviewed_clean": "true",
            "notes": "verified clean",
        })

    output = tmp_path / "labels_self.jsonl"
    summary = ingest_label_csv(queue, output)
    records = [
        json.loads(line)
        for line in output.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert summary["labelled_items"] == 2
    assert records[0]["labels"][0]["annotator"] == "self"
    assert records[0]["labels"][0]["span"] == [2, 5]
    assert records[1]["labels"] == []
    dataset = read_dataset(output)
    assert len(dataset.items) == 2
    assert dataset.quarantined == []


def test_ingest_label_csv_rejects_bad_category_or_span(tmp_path: Path):
    queue = tmp_path / "labels.csv"
    queue.write_text(
        "id,lang,source,translation,type,severity,span_start,span_end,reviewed_clean\n"
        "x,hi,Source,अनुवाद,unknown,major,0,1,false\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unsupported label type"):
        ingest_label_csv(queue, tmp_path / "out.jsonl")
