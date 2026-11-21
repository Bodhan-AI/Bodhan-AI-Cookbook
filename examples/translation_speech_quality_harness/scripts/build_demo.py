import argparse
import importlib
import json
import os
from pathlib import Path
import sys
from typing import Any

import httpx
from dotenv import load_dotenv


RECIPE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = RECIPE_DIR.parents[1]
sys.path.insert(0, str(RECIPE_DIR / "src"))
HF_LANGUAGE_CODES = importlib.import_module(
    "bodhan_eval.languages"
).HF_DATASET_LANGUAGE_CODES

DATASET_IDS = {
    "in22-gen": "ai4bharat/IN22-Gen",
    "in22-conv": "ai4bharat/IN22-Conv",
}
PAGE_SIZE = 100


class DatasetAccessError(RuntimeError):
    pass


def load_local_environment(repo_root: Path = REPO_ROOT) -> str | None:
    load_dotenv(Path(repo_root) / ".env", override=False)
    return os.getenv("HF_TOKEN")


def _validate_api_base_url(api_base_url: str, *, is_mocked: bool) -> None:
    if not is_mocked and not api_base_url.startswith("https://"):
        raise ValueError("The dataset-server URL must use HTTPS.")


def fetch_rows(
    api_base_url: str,
    dataset_id: str,
    token: str,
    limit: int,
    *,
    transport: httpx.BaseTransport | None = None,
) -> list[dict[str, Any]]:
    _validate_api_base_url(api_base_url, is_mocked=transport is not None)
    rows: list[dict] = []
    with httpx.Client(
        base_url=api_base_url.rstrip("/"),
        timeout=30,
        transport=transport,
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        while len(rows) < limit:
            length = min(PAGE_SIZE, limit - len(rows))
            response = client.get(
                "/rows",
                params={
                    "dataset": dataset_id,
                    "config": "default",
                    "split": "test",
                    "offset": len(rows),
                    "length": length,
                },
            )
            if response.status_code in {401, 403}:
                raise DatasetAccessError(
                    "Hugging Face denied dataset access. Sign in, accept the "
                    "dataset access terms, and provide a read token in HF_TOKEN."
                )
            if response.status_code >= 400:
                details = ""
                if response.status_code == 404:
                    details = (
                        " The dataset-server currently exposes IN22-Gen as "
                        "config=default, split=test; this selected dataset or "
                        "split may not be available through that API."
                    )
                raise DatasetAccessError(
                    f"Hugging Face dataset server returned HTTP {response.status_code}."
                    f"{details}"
                )
            try:
                payload = response.json()
            except ValueError as error:
                raise DatasetAccessError(
                    "Hugging Face dataset server returned invalid JSON."
                ) from error
            if not isinstance(payload, dict):
                raise DatasetAccessError(
                    "Hugging Face dataset server returned an invalid response."
                )
            page = payload.get("rows")
            if not isinstance(page, list):
                raise DatasetAccessError(
                    "Hugging Face dataset response did not contain a rows list."
                )
            for entry in page:
                row = entry.get("row") if isinstance(entry, dict) else None
                if isinstance(row, dict):
                    copied_row = dict(row)
                    if "id" not in copied_row and isinstance(entry, dict):
                        copied_row["id"] = entry.get("row_idx", len(rows))
                    rows.append(copied_row)
            if not page or len(page) < length:
                break
    return rows


def rows_to_items(
    rows: list[dict[str, Any]],
    languages: list[str],
    dataset_name: str,
) -> list[dict]:
    records: list[dict] = []
    missing: set[str] = set()
    slug = dataset_name.lower()
    for row_index, row in enumerate(rows):
        source = row.get("eng_Latn", row.get("sentence_eng_Latn"))
        if not isinstance(source, str) or not source.strip():
            missing.add("eng_Latn")
            continue
        row_id = row.get("id", row_index)
        for language in languages:
            language_code = HF_LANGUAGE_CODES[language]
            translation = row.get(
                language_code,
                row.get(f"sentence_{language_code}"),
            )
            if not isinstance(translation, str) or not translation.strip():
                missing.add(language)
                continue
            record = {
                "id": f"{slug}-{row_id}-{language}",
                "lang": language,
                "source": source,
                "translation": translation,
                "reference": translation,
                "dataset": dataset_name,
                "dataset_license": "CC BY 4.0",
                "dataset_row_id": row_id,
            }
            for field_name in (
                "domain",
                "source",
                "context",
                "topic",
                "scenario",
                "speaker",
                "turn",
            ):
                value = row.get(field_name)
                if isinstance(value, (str, int, float)):
                    record[f"dataset_{field_name}"] = value
            records.append(record)
    if missing:
        languages_text = ", ".join(sorted(missing))
        available_fields = ", ".join(sorted(rows[0])) if rows else "(no row fields)"
        raise DatasetAccessError(
            "The downloaded rows do not match the paired-language schema "
            f"(missing {languages_text}; available fields: {available_fields}). "
            "No output was written."
        )
    expected = len(rows) * len(languages)
    if len(records) != expected:
        raise DatasetAccessError(
            f"Expected {expected} aligned rows but found {len(records)}; no output was written."
        )
    return sorted(records, key=lambda record: (record["lang"], record["id"]))


def write_jsonl(path: Path, records: list[dict], *, force: bool) -> None:
    path = Path(path)
    if path.exists() and not force:
        raise FileExistsError(
            f"Output already exists: {path}. Choose a new --out path or pass --force."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        for record in sorted(records, key=lambda record: (record.get("lang", ""), record["id"]))
    )
    path.write_text(content, encoding="utf-8", newline="\n")


def _parse_languages(value: str) -> list[str]:
    languages = sorted(
        {part.strip().lower() for part in value.split(",") if part.strip()}
    )
    if not languages:
        raise ValueError("Provide at least one target language with --langs.")
    unsupported = sorted(set(languages) - set(HF_LANGUAGE_CODES))
    if unsupported:
        raise ValueError(
            "Unsupported IN22 target language(s): " + ", ".join(unsupported)
        )
    return languages


def main() -> int:
    token = load_local_environment()
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--source",
        choices=("fixture", "in22-gen", "in22-conv"),
        default="fixture",
    )
    parser.add_argument("--langs", default="hi,bn,ta")
    parser.add_argument("--limit-per-language", type=int, default=100)
    parser.add_argument("--api-base-url", default=os.getenv("HF_DATASETS_SERVER_URL"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.limit_per_language < 1:
        parser.error("--limit-per-language must be at least 1")
    if args.source == "fixture":
        output = args.out or RECIPE_DIR / "data" / "demo.jsonl"
        samples = [
            {"id": "demo-1", "lang": "hi", "source": "The meeting starts at 10 AM.", "translation": "बैठक सुबह 10 बजे शुरू होती है।"},
            {"id": "demo-2", "lang": "ta", "source": "The report has three sections.", "translation": "அறிக்கையில் மூன்று பகுதிகள் உள்ளன."},
            {"id": "demo-3", "lang": "bn", "source": "Water is available in the hall.", "translation": "হলে পানি পাওয়া যাবে।"},
        ]
        if args.dry_run:
            print("Estimated translate calls: 3")
            print("Estimated speech calls: 0")
            print("Estimated maximum cost for this dry run: 0 INR")
            return 0
        try:
            write_jsonl(output, samples, force=args.force)
        except OSError as error:
            parser.error(str(error))
        print(f"Wrote {len(samples)} fixture rows to {output}")
        return 0

    try:
        languages = _parse_languages(args.langs)
    except ValueError as error:
        parser.error(str(error))
    dataset_id = DATASET_IDS[args.source]
    dataset_name = "IN22-Gen" if args.source == "in22-gen" else "IN22-Conv"
    output = args.out or RECIPE_DIR / "data" / f"{args.source.replace('-', '_')}.jsonl"
    if args.dry_run:
        requests = (args.limit_per_language + PAGE_SIZE - 1) // PAGE_SIZE
        print(f"Dataset: {args.source}")
        print(f"Target languages: {','.join(languages)}")
        print(f"Rows per language: {args.limit_per_language}")
        print(f"Estimated dataset download requests: {requests}")
        print("Bodhan model calls: 0 (uses supplied clean references)")
        print("Estimated speech calls: 0")
        print("Estimated model cost: 0 INR")
        print(f"Output: {output}")
        return 0
    if not token:
        parser.error(
            "HF_TOKEN is not set. Accept the Hugging Face dataset terms and set a read token."
        )
    if not args.api_base_url:
        parser.error(
            "Set HF_DATASETS_SERVER_URL or pass --api-base-url with the Hugging Face dataset-server URL."
        )
    try:
        rows = fetch_rows(
            args.api_base_url,
            dataset_id,
            token,
            args.limit_per_language,
        )
        if len(rows) < args.limit_per_language:
            raise DatasetAccessError(
                f"Requested {args.limit_per_language} aligned rows but the "
                f"server returned {len(rows)} from default/test; no output was written."
            )
        records = rows_to_items(rows, languages, dataset_name)
        write_jsonl(output, records, force=args.force)
    except (DatasetAccessError, httpx.RequestError, OSError, ValueError) as error:
        parser.error(str(error))
    print(
        f"Wrote {len(records)} clean reference rows "
        f"({len(languages)} languages) to {output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
