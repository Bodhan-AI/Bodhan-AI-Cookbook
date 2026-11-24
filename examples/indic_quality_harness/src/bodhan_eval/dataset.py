import json
from pathlib import Path

REQUIRED_FIELDS = {"id", "source_text", "language"}


def load_dataset(path: str, languages: list[str] | None = None) -> list[dict]:
    """Load and validate test cases from a JSONL file."""
    dataset_path = Path(path)

    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset not found: {dataset_path}")

    records = []

    with dataset_path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON on line {line_number}: {exc}"
                ) from exc

            missing = REQUIRED_FIELDS - record.keys()
            if missing:
                raise ValueError(
                    f"Line {line_number} is missing fields: {sorted(missing)}"
                )

            if languages and record["language"] not in languages:
                continue

            records.append(record)

    return records
