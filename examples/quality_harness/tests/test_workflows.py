import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bodhan_eval.calibration import merge_corroborating_flags, report, score
from bodhan_eval.checks import character_f1, evaluate
from bodhan_eval.demo import main as demo_main
from bodhan_eval.inject import main as inject_main
from bodhan_eval.inject import mutations
from bodhan_eval.models import Client, ModelError, load_environment
from bodhan_eval.review import main as review_main
from bodhan_eval.runner import main


class WorkflowTests(unittest.TestCase):
    def test_env_precedence_and_ignored_generic_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text('BODHAN_TRANSLATE_KEY="local"\nBODHAN_API_KEY=<unused>\nLLM_MODEL=fixture\n', encoding="utf-8")
            with patch.dict("os.environ", {"BODHAN_TRANSLATE_KEY": "process"}, clear=True):
                environment = load_environment(path)
            self.assertEqual(environment["BODHAN_TRANSLATE_KEY"], "process")
            self.assertNotIn("BODHAN_API_KEY", environment)

    def test_cache_corruption_fails_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = Client(tmp, 1, send=lambda *a: b'{"translation":"25"}', env={"BODHAN_TRANSLATE_KEY": "fixture"})
            client.translate("25", "hi")
            path = next(Path(tmp).glob("*.json"))
            path.write_text("not JSON", encoding="utf-8")
            with self.assertRaisesRegex(ModelError, "cache_corrupt"):
                client.translate("25", "hi")
            self.assertEqual(client.calls, 1)

    def test_120_original_demo_sources_and_no_fake_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            dataset = Path(tmp) / "demo.jsonl"
            demo_main(["--out", str(dataset)])
            rows = [json.loads(line) for line in dataset.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 120)
            self.assertEqual({r["lang"] for r in rows}, {"hi", "ta", "bn"})
            self.assertTrue(all("translation" not in r and "labels" not in r for r in rows))
            self.assertEqual(len({r["parent_id"] for r in rows}), 40)

    def test_injector_roundtrip_keeps_unreviewed_parents_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp) / "clean.jsonl", Path(tmp) / "injected.jsonl"
            source.write_text(json.dumps({"id": "test", "source": "There are 25 chairs.", "lang": "hi", "translation": "25"}) + "\n", encoding="utf-8")
            inject_main(["--dataset", str(source), "--out", str(output)])
            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertNotIn("labels", rows[0])
            self.assertTrue(all(r["parent_id"] == "test" for r in rows))
            self.assertTrue(all(r["split"] == rows[0]["split"] for r in rows))
            self.assertTrue(all(r["label_origin"] == "programmatic_injection" for r in rows[1:]))
        self.assertNotIn("wrong_language", {r["labels"][0]["type"] for r in mutations({"id": "n", "lang": "hi", "source": "25", "translation": "25"})})

    def test_duplicate_corrobation_is_one_defect(self):
        first = {"category": "number", "span_field": "translation", "span": [0, 2], "check": "numbers"}
        second = {**first, "check": "judge"}
        merged = merge_corroborating_flags([first, second])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["corroborating_flags"][0]["check"], "judge")
        metrics = score([{"labels": [{"type": "number", "span": [0, 2]}], "flags": merged}])
        self.assertEqual(metrics["categories"]["number"]["precision"], 1)

    def test_injected_labels_do_not_tune_human_thresholds(self):
        row = {
            "id": "x",
            "lang": "hi",
            "parent_id": "x",
            "split": "calibration",
            "label_origin": "programmatic_injection",
            "labels": [],
            "flags": [{"category": "number", "check": "judge", "confidence": 0.9}],
            "review": [],
        }
        result = report([row], {("x", "hi")})
        self.assertEqual(result["tuning"]["thresholds"], {})
        self.assertEqual(result["nonhuman_calibration_items_ignored"], 1)
        self.assertEqual(result["evaluation"]["annotated_items"], 0)

    def test_review_import_requires_attribution(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue, labels = Path(tmp) / "review.csv", Path(tmp) / "labels.jsonl"
            with queue.open("w", encoding="utf-8", newline="") as stream:
                fields = ["id", "lang", "span_field", "span", "human_type", "human_severity", "annotator"]
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerow(
                    {
                        "id": "x",
                        "lang": "hi",
                        "span_field": "translation",
                        "span": "[0, 2]",
                        "human_type": "number",
                        "human_severity": "major",
                        "annotator": "test-reviewer",
                    }
                )
            review_main(["--csv", str(queue), "--out", str(labels)])
            annotation = json.loads(labels.read_text())
            self.assertEqual(annotation["label_origin"], "human:test-reviewer")
            self.assertEqual(annotation["labels"][0]["span"], [0, 2])

    def test_empty_supplied_output_and_reference_metrics(self):
        flags, checks = evaluate({"source": "A sentence.", "translation": "", "lang": "hi"})
        self.assertEqual(flags[0]["category"], "omission")
        self.assertEqual(flags[0]["span_field"], "source")
        self.assertEqual(character_f1("२५", "25")["score"], 1)

    def test_unseen_production_languages_and_ci_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            dataset = path / "data.jsonl"
            languages = "en hi bn mr te ta gu kn ml or pa as ur".split()
            rows = [{"id": "one", "source": "25", "lang": lang, "translation": "25"} for lang in languages]
            dataset.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            arguments = ["--dataset", str(dataset), "--langs", ",".join(languages), "--offline", "--cache-dir", str(path / "cache")]
            main(arguments + ["--out", str(path / "base")])
            rows[0]["translation"] = "26"
            dataset.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            code = main(arguments + ["--out", str(path / "new"), "--baseline", str(path / "base"), "--regression-limit", "0"])
            self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
