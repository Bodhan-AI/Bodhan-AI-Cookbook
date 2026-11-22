"""Whole-item blind annotation, including clean negatives, bound to exact outputs."""

import argparse
import csv
import json
import math
import wave
from pathlib import Path

from .artifacts import content_fingerprint, read_items
from .judge import CATEGORIES
from .models import canonical, digest
from .runner import validate

FIELDS = ["id", "lang", "parent_id", "content_sha256", "source", "translation", "audio", "transcript", "decision", "labels_json", "annotator", "notes"]


def ensure_distinct(dataset, output):
    if Path(dataset).resolve() == Path(output).resolve():
        raise ValueError("input and output must differ")


def review_identity(row):
    return digest(canonical([row["id"], row["lang"]]).encode())


def export_queue(dataset, output):
    ensure_distinct(dataset, output)
    dataset, output = Path(dataset).resolve(), Path(output)
    rows = read_items(dataset)
    queue = []
    for row in rows:
        if not isinstance(row.get("translation"), str):
            raise ValueError(f"{row['id']}: missing translation; generate before reviewing")
        queue.append(
            {
                "id": review_identity(row),
                "lang": row["lang"],
                "parent_id": digest((row.get("parent_id") or row["id"]).encode()),
                "content_sha256": content_fingerprint(row, dataset.parent),
                "source": row["source"],
                "translation": row["translation"],
                "audio": str((dataset.parent / row["audio"]).resolve()) if row.get("audio") else "",
                "transcript": row.get("transcript") or "",
            }
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(sorted(queue, key=lambda r: r["id"]))
    return {"exported_items": len(queue), "selection": "all dataset items, not just flagged candidates"}


def validate_human_labels(labels, row, directory):
    if not isinstance(labels, list):
        raise ValueError("labels_json must be a JSON array")
    validate({**row, "labels": labels})
    for label in labels:
        if label["type"] not in CATEGORIES or label.get("severity") not in ("minor", "major", "critical"):
            raise ValueError("unknown human category or severity")
        if ("span" in label) == ("time_span" in label):
            raise ValueError("each human error requires exactly one text span or time_span")
        if "span" in label:
            if label.get("span_field", "translation") not in ("source", "translation", "reference"):
                raise ValueError("invalid human text field")
            if label["span"][0] == label["span"][1]:
                raise ValueError("human text span must be nonempty")
        else:
            if not row.get("audio"):
                raise ValueError("audio label without audio")
            with wave.open(str(Path(directory) / row["audio"]), "rb") as audio:
                duration = audio.getnframes() / audio.getframerate()
            if not math.isfinite(duration) or label["time_span"][1] > duration + 1e-6:
                raise ValueError("human time_span exceeds reviewed audio")


def import_answers(dataset, queue, output):
    ensure_distinct(dataset, output)
    ensure_distinct(queue, output)
    dataset, output = Path(dataset).resolve(), Path(output)
    items = {(review_identity(r), r["lang"]): r for r in read_items(dataset)}
    annotations, seen, deferred = [], set(), 0
    with Path(queue).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not set(FIELDS).issubset(reader.fieldnames or []):
            raise ValueError("not a blind annotation CSV")
        for number, answer in enumerate(reader, 2):
            key = (answer["id"], answer["lang"])
            if key in seen:
                raise ValueError(f"duplicate annotation row {number}")
            seen.add(key)
            if key not in items:
                raise ValueError(f"unknown annotation identity at row {number}")
            row = items[key]
            if answer["content_sha256"] != content_fingerprint(row, dataset.parent):
                raise ValueError(f"stale annotation at row {number}: reviewed output changed")
            # Edited display text must not conceal a changed review target.
            if answer["source"] != row["source"] or answer["translation"] != row.get("translation"):
                raise ValueError(f"stale or edited annotation display at row {number}")
            decision = answer["decision"].strip()
            if decision in ("", "defer"):
                deferred += 1
                continue
            annotator = answer["annotator"].strip()
            if not annotator or decision not in ("clean", "errors"):
                raise ValueError(f"row {number}: require annotator and clean/errors/defer decision")
            labels = json.loads(answer["labels_json"] or "[]")
            if decision == "clean" and labels != []:
                raise ValueError(f"row {number}: clean decision conflicts with error labels")
            if decision == "errors" and (not isinstance(labels, list) or not labels):
                raise ValueError(f"row {number}: errors decision requires nonempty labels")
            validate_human_labels(labels, row, dataset.parent)
            annotations.append(
                {
                    "id": row["id"],
                    "lang": key[1],
                    "labels": labels,
                    "label_origin": "human:" + annotator,
                    "reviewed_content_sha256": answer["content_sha256"],
                    "parent_id": row.get("parent_id") or row["id"],
                }
            )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in annotations), encoding="utf-8")
    return {"annotated_items": len(annotations), "deferred_items": deferred, "unreturned_items": len(items) - len(seen)}


def apply_answers(dataset, labels_file, output, clean_only=False):
    """Materialize reviewed parents without manually copying labels between files."""
    ensure_distinct(dataset, output)
    ensure_distinct(labels_file, output)
    dataset, output = Path(dataset).resolve(), Path(output)
    answers = {}
    for line in Path(labels_file).read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        answer = json.loads(line)
        key = (answer["id"], answer["lang"])
        if key in answers:
            raise ValueError("duplicate human annotation identity")
        answers[key] = answer
    selected, seen = [], set()
    for row in read_items(dataset):
        key = (row["id"], row["lang"])
        seen.add(key)
        answer = answers.get(key)
        if answer:
            if not answer.get("label_origin", "").startswith("human:") or answer.get("reviewed_content_sha256") != content_fingerprint(row, dataset.parent):
                raise ValueError("stale or unattributable human annotation")
            validate_human_labels(answer["labels"], row, dataset.parent)
            row = {**row, **{name: answer[name] for name in ("labels", "label_origin", "reviewed_content_sha256")}}
        if clean_only and (answer is None or row.get("labels") != []):
            continue
        if row.get("audio"):
            row["audio"] = str((dataset.parent / row["audio"]).resolve())
        selected.append(row)
    if set(answers) - seen:
        raise ValueError("human annotations contain unknown dataset identities")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in selected), encoding="utf-8")
    return {"selected_items": len(selected), "clean_only": clean_only}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    export = subparsers.add_parser("export")
    export.add_argument("--dataset", required=True)
    export.add_argument("--out", required=True)
    review = subparsers.add_parser("import")
    review.add_argument("--dataset", required=True)
    review.add_argument("--csv", required=True)
    review.add_argument("--out", required=True)
    apply = subparsers.add_parser("apply")
    apply.add_argument("--dataset", required=True)
    apply.add_argument("--labels", required=True)
    apply.add_argument("--out", required=True)
    apply.add_argument("--clean-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.operation == "export":
            result = export_queue(args.dataset, args.out)
        elif args.operation == "import":
            result = import_answers(args.dataset, args.csv, args.out)
        else:
            result = apply_answers(args.dataset, args.labels, args.out, args.clean_only)
    except (OSError, ValueError, KeyError, wave.Error) as exc:
        parser.exit(1, f"Annotation failed: {exc}\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
