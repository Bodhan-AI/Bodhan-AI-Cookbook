"""Phase 3 falsification tests: evidence must fail closed when incomplete."""

import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bodhan_eval.annotations import apply_answers, export_queue, import_answers
from bodhan_eval.benchmark import validate_assessment
from bodhan_eval.calibration import score
from bodhan_eval.inject import main as inject_main
from bodhan_eval.models import Client, digest
from bodhan_eval.readiness import audit, receipt_errors
from bodhan_eval.runner import main as run_main
from test_models import wav


class AnnotationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.dataset = self.root / "items.jsonl"
        self.queue = self.root / "blind.csv"
        self.labels = self.root / "labels.jsonl"
        self.rows = [
            {"id": "a", "lang": "hi", "source": "25", "translation": "25"},
            {"id": "b", "lang": "ta", "source": "26", "translation": "27"},
        ]
        self.save_dataset()

    def save_dataset(self):
        self.dataset.write_text("".join(json.dumps(r) + "\n" for r in self.rows), encoding="utf-8")

    def complete(self, clean=True):
        export_queue(self.dataset, self.queue)
        with self.queue.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            fields, rows = reader.fieldnames, list(reader)
        target = next(r for r in rows if r["source"] == "25")
        target.update(decision="clean" if clean else "errors", annotator="reviewer-one", labels_json="[]")
        with self.queue.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        return fields, rows

    def test_all_items_exported_blind_and_blank_is_not_clean(self):
        export_queue(self.dataset, self.queue)
        with self.queue.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 2)
        self.assertNotIn("flags", rows[0])
        self.assertNotIn("labels", rows[0])
        self.assertNotIn(rows[0]["id"], ("a", "b"))
        self.assertEqual(import_answers(self.dataset, self.queue, self.labels)["annotated_items"], 0)
        self.assertEqual(self.labels.read_text(), "")

    def test_clean_answer_bound_to_original_content(self):
        self.complete()
        summary = import_answers(self.dataset, self.queue, self.labels)
        self.assertEqual(summary["annotated_items"], 1)
        answer = json.loads(self.labels.read_text())
        self.assertEqual(answer["labels"], [])
        self.assertEqual(answer["label_origin"], "human:reviewer-one")
        self.assertEqual(len(answer["reviewed_content_sha256"]), 64)
        self.rows[0]["translation"] = "28"
        self.save_dataset()
        with self.assertRaisesRegex(ValueError, "stale"):
            import_answers(self.dataset, self.queue, self.labels)

    def test_error_decision_requires_error_labels(self):
        self.complete(clean=False)
        with self.assertRaisesRegex(ValueError, "nonempty"):
            import_answers(self.dataset, self.queue, self.labels)

    def test_duplicate_answers_and_source_only_items_rejected(self):
        _, rows = self.complete()
        with self.queue.open("a", encoding="utf-8", newline="") as stream:
            csv.DictWriter(stream, fieldnames=list(rows[0])).writerow(rows[0])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            import_answers(self.dataset, self.queue, self.labels)
        self.rows[0].pop("translation")
        self.save_dataset()
        with self.assertRaisesRegex(ValueError, "translation"):
            export_queue(self.dataset, self.queue)

    def test_apply_clean_answers_then_inject_does_not_reuse_parent_review_hash(self):
        self.complete()
        import_answers(self.dataset, self.queue, self.labels)
        approved = self.root / "approved.jsonl"
        summary = apply_answers(self.dataset, self.labels, approved, clean_only=True)
        self.assertEqual(summary["selected_items"], 1)
        injected = self.root / "injected.jsonl"
        inject_main(["--dataset", str(approved), "--out", str(injected), "--require-human-clean"])
        rows = [json.loads(line) for line in injected.read_text(encoding="utf-8").splitlines()]
        self.assertIn("reviewed_content_sha256", rows[0])
        self.assertTrue(all("reviewed_content_sha256" not in r for r in rows[1:]))

    def test_audio_replacement_invalidates_review_even_with_same_filename(self):
        audio = self.root / "audio.wav"
        audio.write_bytes(wav())
        self.rows[0]["audio"] = "audio.wav"
        self.save_dataset()
        self.complete()
        audio.write_bytes(wav(2))
        with self.assertRaisesRegex(ValueError, "stale"):
            import_answers(self.dataset, self.queue, self.labels)

    def test_checker_transcript_change_is_not_a_new_system_output(self):
        self.complete()
        self.rows[0]["transcript"] = "a noisy checker changed its mind"
        self.save_dataset()
        self.assertEqual(import_answers(self.dataset, self.queue, self.labels)["annotated_items"], 1)

    def test_generation_cannot_reuse_a_human_clean_label_for_changed_output(self):
        self.complete()
        import_answers(self.dataset, self.queue, self.labels)
        approved, report = self.root / "approved.jsonl", self.root / "report"
        apply_answers(self.dataset, self.labels, approved, clean_only=True)

        def changed(original, directory, client, args):
            return {
                **original,
                "translation": "26",
                "status": "evaluated",
                "checks": {},
                "flags": [],
                "review": [],
                "transcript": None,
                "audio": None,
                "split": "calibration",
            }

        with patch("bodhan_eval.runner.process_item", side_effect=changed):
            run_main(["--dataset", str(approved), "--langs", "hi", "--out", str(report), "--generate", "--cache-dir", str(self.root / "cache")])
        result = json.loads((report / "items.jsonl").read_text())
        self.assertIsNone(result["labels"])
        self.assertEqual(result["label_origin"], "unreviewed_after_generation")
        self.assertEqual(result["checks"]["human_annotation"]["status"], "invalidated")


class EvidenceTests(unittest.TestCase):
    def test_nullable_optional_metadata_does_not_abort_evaluation(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            dataset = directory / "nullable.jsonl"
            dataset.write_text(
                json.dumps(
                    {
                        "id": "nullable",
                        "source": "25",
                        "lang": "hi",
                        "translation": "25",
                        "parent_id": None,
                        "label_origin": None,
                        "split": "calibration",
                        "labels": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            run_main(["--dataset", str(dataset), "--langs", "hi", "--out", str(directory / "report"), "--offline", "--cache-dir", str(directory / "cache")])
            row = json.loads((directory / "report" / "items.jsonl").read_text())
            self.assertEqual(row["parent_id"], "nullable")
            self.assertEqual(row["label_origin"], "unspecified")

    def test_source_only_and_failed_reports_are_not_assessed_benchmarks(self):
        summary = {"items": 10, "invalid_items": 0, "filtered_items": 0, "failed_checks": 0, "languages": {"hi": {"unassessed_items": 10}}}
        self.assertFalse(validate_assessment(summary, [{"status": "unassessed"}], require_live=False)["complete"])
        summary["languages"]["hi"]["unassessed_items"] = 0
        summary["failed_checks"] = 1
        self.assertFalse(validate_assessment(summary, [{"status": "evaluated"}], require_live=True)["complete"])

    def test_live_benchmark_requires_judge_and_roundtrip_coverage(self):
        summary = {"items": 1, "invalid_items": 0, "filtered_items": 0, "failed_checks": 0}
        row = {"status": "evaluated", "translation": "25", "audio": None, "checks": {}}
        self.assertFalse(validate_assessment(summary, [row], require_live=True)["complete"])
        row["checks"] = {name: {"status": "completed"} for name in ("judge", "back_translation")}
        self.assertTrue(validate_assessment(summary, [row], require_live=True)["complete"])

    def test_generation_capture_includes_cache_replays(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = Client(tmp, 1, send=lambda *a: b'{"translation":"25"}', env={"BODHAN_TRANSLATE_KEY": "fixture"})
            with client.capture() as first:
                client.translate("25", "hi")
            with client.capture() as replay:
                client.translate("25", "hi")
            self.assertEqual(first, replay)
            self.assertEqual(len(first), 1)
            self.assertEqual(client.calls, 1)

    def test_error_recall_does_not_count_partially_detected_item_as_full_recall(self):
        row = {"labels": [{"type": "number", "span": [0, 2]}, {"type": "entity", "span": [4, 7]}], "flags": [{"category": "number", "span": [0, 2]}]}
        metrics = score([row])
        self.assertEqual(metrics["detection_recall"], 0.5)
        self.assertEqual(metrics["item_detection_recall"], 1)
        self.assertEqual(metrics["seeded_errors"], 2)

    def test_localization_is_independent_of_category(self):
        row = {"labels": [{"type": "entity", "span": [0, 2]}], "flags": [{"category": "number", "span": [0, 2]}]}
        metrics = score([row])
        self.assertEqual(metrics["localization_overlap"], 1)
        self.assertEqual(metrics["category_accuracy"], 0)

    def test_source_only_demo_cannot_pass_readiness(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.jsonl"
            path.write_text(json.dumps({"id": "source-only", "lang": "hi", "source": "25"}) + "\n", encoding="utf-8")
            result = audit(path)
            self.assertFalse(result["ready"])
            checks = {c["check"]: c for c in result["checks"]}
            self.assertEqual(checks["demo_outputs"]["details"]["translated_records"], 0)
            self.assertNotEqual(checks["bodhan_generation_provenance"]["status"], "pass")

    def test_translation_receipt_reconciles_cache_and_rejects_changed_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            client = Client(directory, 1, send=lambda *a: b'{"translation":"25"}', env={"BODHAN_TRANSLATE_KEY": "fixture"})
            with client.capture() as keys:
                client.translate("25", "hi")
            row = {
                "id": "fixture",
                "lang": "hi",
                "source": "25",
                "translation": "25",
                "generation": {
                    "translation": {"model": "indic-translate", "input_sha256": digest(b"25"), "output_sha256": digest(b"25"), "request_keys": keys}
                },
            }
            self.assertEqual(receipt_errors(row, "translation", directory, directory), [])
            row["translation"] = "26"
            self.assertTrue(receipt_errors(row, "translation", directory, directory))


if __name__ == "__main__":
    unittest.main()
