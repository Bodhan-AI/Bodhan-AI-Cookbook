from bodhan_eval.__main__ import run_human_calibration
from pathlib import Path
import json
import tempfile

results = [
    {
        "id": "test_001",
        "errors": [{"category": "number_error"}],
        "checks": {}
    },
    {
        "id": "test_002",
        "errors": [{"category": "omission"}],
        "checks": {}
    },
    {
        "id": "test_003",
        "errors": [],
        "checks": {}
    },
    {
        "id": "test_004",
        "errors": [],
        "checks": {}
    }
]

labels = [
    {"id": "test_001", "category": "number_error"},
    {"id": "test_002", "category": "omission"},
    {"id": "test_003", "category": "none"},
    {"id": "test_004", "category": "none"}
]

with tempfile.TemporaryDirectory() as temp:
    temp = Path(temp)
    label_file = temp / "labels.jsonl"
    output_file = temp / "calibration.json"

    with label_file.open("w", encoding="utf-8") as f:
        for item in labels:
            f.write(json.dumps(item) + "\n")

    run_human_calibration(label_file, results, output_file)

    report = json.loads(output_file.read_text(encoding="utf-8"))

    print(json.dumps(report, indent=2))
    assert report["status"] == "completed"
    print("\nCALIBRATION TEST PASSED")
