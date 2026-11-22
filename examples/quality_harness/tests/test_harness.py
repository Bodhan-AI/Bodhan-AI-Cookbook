import json
import tempfile
import unittest
import wave
from pathlib import Path

from bodhan_eval.audio import analyze_wav
from bodhan_eval.calibration import score
from bodhan_eval.checks import alignment, format_check, normalize, number_check, script_check, term_check
from bodhan_eval.runner import main

ROOT = Path(__file__).resolve().parents[1]


class CheckTests(unittest.TestCase):
    def test_normalization(self):
        self.assertEqual(normalize("२५ ২৫ ௨௫"), "25 25 25")
        self.assertEqual(normalize("e\u0301"), normalize("é"))

    def test_number_multiset_and_original_offsets(self):
        self.assertEqual(number_check("25 25", "२५ २५"), [])
        flags = number_check("25", "x २६")
        self.assertEqual(flags[0]["span"], [2, 4])
        self.assertEqual(flags[0]["evidence"]["unexpected"], "२६")
        missing = number_check("25 25", "२५")
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["span_field"], "source")

    def test_format(self):
        self.assertEqual(format_check("$x$", "$x$"), [])
        self.assertEqual(format_check("$x$", "x")[0]["category"], "format")
        self.assertTrue(format_check("https://example.org", ""))
        self.assertTrue(format_check(r"\frac{x}{y}", "x/y"))
        self.assertTrue(format_check("$$x$$", "$x$"))
        self.assertTrue(format_check("**Warning:**", "**Warning:"))
        table = "| A | B |\n|---|---|\n| 1 | 2 |"
        self.assertEqual(format_check(table, table), [])
        self.assertTrue(format_check(table, table.replace("| 1 | 2 |", "| 1 |")))

    def test_terms(self):
        item = {"source": "Use GPU", "translation": "GPU", "terms": {"GPU": "GPU"}}
        self.assertEqual(term_check(item), [])
        item["translation"] = "CPU"
        self.assertEqual(term_check(item)[0]["span"], [4, 7])

    def test_script_conservative(self):
        self.assertEqual(script_check("GPU", "hi"), [])
        self.assertTrue(script_check("This remains untranslated", "hi"))
        self.assertEqual(script_check("This remains untranslated", "en"), [])

    def test_alignment_offsets(self):
        result = alignment("a b c", "a c")
        self.assertEqual(result["differences"][0]["expected_span"], [2, 3])
        self.assertIsNone(result["differences"][0]["actual_span"])
        self.assertEqual(alignment("२५", "25")["token_similarity"], 1)

    def test_audio_silence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "silent.wav"
            with wave.open(str(path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(8000)
                audio.writeframes(bytes(16000))
            signal = analyze_wav(path)
            self.assertEqual(signal["duration_seconds"], 1)
            self.assertEqual(signal["silence_intervals_seconds"], [[0, 1]])
            self.assertEqual(signal["clipped_sample_fraction"], 0)

    def test_calibration_unlabelled_vs_clean(self):
        rows = [{"id": "a", "lang": "hi", "status": "evaluated", "flags": [{"category": "number"}]}]
        self.assertEqual(score(rows)["annotated_items"], 0)
        metrics = score([{**rows[0], "labels": []}])
        self.assertEqual(metrics["categories"]["number"]["fp"], 1)


class EndToEndTests(unittest.TestCase):
    def test_ten_items_reproducibility_and_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp) / "first", Path(tmp) / "second"
            base = ["--dataset", str(ROOT / "data/smoke.jsonl"), "--langs", "hi,ta,bn", "--offline", "--cache-dir", str(Path(tmp) / "cache")]
            self.assertEqual(main(base + ["--out", str(first)]), 0)
            self.assertEqual(main(base + ["--out", str(second), "--baseline", str(first)]), 0)
            for filename in ("items.jsonl", "summary.json", "summary.md", "calibration.json", "checks.jsonl"):
                self.assertEqual((first / filename).read_bytes(), (second / filename).read_bytes())
            self.assertEqual(json.loads((first / "summary.json").read_text())["items"], 10)
            self.assertIn("No changes.", (second / "diff.md").read_text())

    def test_partial_bad_records_and_missing_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            dataset, out = Path(tmp) / "input.jsonl", Path(tmp) / "out"
            dataset.write_text(
                "broken\n"
                + json.dumps({"id": "valid", "source": "25", "lang": "hi", "translation": "25", "audio": "missing.wav"})
                + "\n"
                + json.dumps({"id": "missing", "source": "25", "lang": "hi"}),
                encoding="utf-8",
            )
            main(["--dataset", str(dataset), "--langs", "hi", "--out", str(out), "--offline", "--cache-dir", str(Path(tmp) / "cache")])
            summary = json.loads((out / "summary.json").read_text())
            self.assertEqual(summary["invalid_items"], 1)
            self.assertEqual(summary["items"], 2)
            self.assertEqual(summary["languages"]["hi"]["unassessed_items"], 1)
            rows = [json.loads(line) for line in (out / "items.jsonl").read_text().splitlines()]
            self.assertEqual(rows[0]["checks"]["audio_signal"]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
