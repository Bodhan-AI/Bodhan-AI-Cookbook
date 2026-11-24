import copy
import unittest

from bodhan_eval.calibration import matching, report, score, tune
from bodhan_eval.inject import mutations, perturb_audio, split_for
from bodhan_eval.judge import validate_judgment
from bodhan_eval.models import ModelError
from test_models import wav


def finding(confidence=0.8):
    return {"category": "number", "severity": "major", "span_field": "translation", "span": [0, 2], "check": "judge", "confidence": confidence}


class JudgeTests(unittest.TestCase):
    def test_strict_existing_quotes_and_no_indic_rewrite(self):
        item = {"translation": "25"}
        flag = {
            "category": "number",
            "severity": "major",
            "span_field": "translation",
            "span": [0, 2],
            "quote": "25",
            "explanation": "A changed quantity.",
            "confidence": 0.95,
            "uncertain": False,
        }
        self.assertEqual(len(validate_judgment({"flags": [flag]}, item)), 1)
        recovered = validate_judgment({"flags": [{**flag, "span": [0, 99]}]}, item)
        self.assertEqual(recovered[0]["span"], [0, 2])
        with self.assertRaisesRegex(ModelError, "judge_quote_not_exact"):
            validate_judgment({"flags": [{**flag, "span": [0, 99]}]}, {"translation": "25 25"})
        for change in (
            {"quote": "26"},
            {"explanation": "२५"},
            {"confidence": float("nan")},
            {"category": []},
            {"uncertain": "false"},
            {"correction": "26"},
        ):
            with self.subTest(change=change), self.assertRaises(ModelError):
                validate_judgment({"flags": [{**flag, **change}]}, item)


class CalibrationTests(unittest.TestCase):
    def test_one_to_one_matching(self):
        label = {"type": "number", "span": [0, 2]}
        self.assertEqual(len(matching([finding(), finding()], [label])), 1)
        result = score([{"labels": [label], "flags": [finding(), finding()], "label_origin": "human:fixture"}])
        self.assertEqual(result["categories"]["number"]["tp"], 1)
        self.assertEqual(result["categories"]["number"]["fp"], 1)

    def test_tuning_uses_labels_and_holds_out_siblings(self):
        rows = [
            {"id": "positive", "lang": "hi", "parent_id": "p", "labels": [{"type": "number", "span": [0, 2]}], "flags": [finding(0.9)], "review": []},
            {"id": "negative", "lang": "hi", "parent_id": "n", "labels": [], "flags": [finding(0.2)], "review": []},
            {"id": "sibling", "lang": "ta", "parent_id": "p", "labels": [], "flags": [], "review": []},
            {"id": "held", "lang": "bn", "parent_id": "h", "labels": [], "flags": [], "review": []},
        ]
        for row in rows:
            row["label_origin"] = "human:fixture"
        self.assertEqual(tune(rows[:2])["thresholds"]["number"], 0.9)
        result = report(copy.deepcopy(rows), {("positive", "hi"), ("negative", "hi")})
        self.assertEqual(result["evaluation"]["annotated_items"], 1)
        self.assertEqual(result["development_items"], 2)

    def test_unknown_is_not_clean_and_time_localization(self):
        rows = [
            {"labels": None, "flags": []},
            {"labels": [], "flags": []},
            {"labels": [{"type": "garbled_audio", "time_span": [0, 2]}], "flags": [{"category": "garbled_audio", "time_span": [0, 2]}]},
        ]
        result = score(rows)
        self.assertEqual(result["annotated_items"], 2)
        self.assertEqual(result["clean_items"], 1)
        self.assertEqual(result["localization_overlap"], 1)


class InjectorTests(unittest.TestCase):
    def test_mutations_have_exact_labels_and_lineage(self):
        clean = {"id": "test", "source": "25", "lang": "hi", "translation": "२५"}
        variants = mutations(clean)
        number = next(item for item in variants if item["labels"][0]["type"] == "number")
        self.assertEqual(number["translation"], "३५")
        self.assertEqual(number["parent_id"], "test")
        self.assertNotEqual(number["label_origin"], "human")
        for item in variants:
            label = item["labels"][0]
            start, end = label["span"]
            self.assertTrue(item[label["span_field"]][start:end])
        self.assertEqual(clean["translation"], "२५")
        self.assertEqual(split_for("same"), split_for("same"))

    def test_audio_perturbations(self):
        data = wav(4)
        shortened, omission = perturb_audio(data, "speech_omission")
        repeated, repetition = perturb_audio(data, "speech_repetition")
        self.assertLess(len(shortened), len(data))
        self.assertGreater(len(repeated), len(data))
        self.assertEqual(omission, [3, 4])
        self.assertEqual(repetition, [4, 5])


if __name__ == "__main__":
    unittest.main()
