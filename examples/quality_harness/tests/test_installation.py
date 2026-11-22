"""Wheel integrity and exclusion rules, independent of live providers."""

import base64
import csv
import hashlib
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from bodhan_eval.installation import backend_requirement, verify_wheel


class WheelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "fixture.whl"
        self.source = {"bodhan_eval/__init__.py": b'__version__ = "0.1.0"\n', "bodhan_eval/__main__.py": b"pass\n"}
        self.hashes = {name: hashlib.sha256(content).hexdigest() for name, content in self.source.items()}

    def make_wheel(self, extra=None, corrupt_record=False):
        files = {
            **self.source,
            "bodhan_eval-0.1.0.dist-info/METADATA": b"Metadata-Version: 2.4\nName: bodhan-eval\nVersion: 0.1.0\n",
            "bodhan_eval-0.1.0.dist-info/WHEEL": b"Wheel-Version: 1.0\nTag: py3-none-any\n",
        }
        files.update(extra or {})
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        for name, content in files.items():
            digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).decode().rstrip("=")
            writer.writerow([name, "sha256=" + ("invalid" if corrupt_record else digest), len(content)])
        record = "bodhan_eval-0.1.0.dist-info/RECORD"
        writer.writerow([record, "", ""])
        files[record] = stream.getvalue().encode()
        with zipfile.ZipFile(self.path, "w") as archive:
            for name, content in files.items():
                archive.writestr(name, content)

    def test_complete_wheel_matches_source_and_record(self):
        self.make_wheel()
        result = verify_wheel(self.path, self.hashes)
        self.assertEqual(result["source_files"], 2)
        self.assertEqual(result["distribution"], "bodhan-eval")

    def test_private_or_generated_files_are_rejected(self):
        for name in (".env", "bodhan_eval/.env", "bodhan_eval/audio.wav", "outputs/result.json", "../outside.py"):
            with self.subTest(name=name):
                self.make_wheel({name: b"private"})
                with self.assertRaises(ValueError):
                    verify_wheel(self.path, self.hashes)

    def test_record_tampering_and_source_changes_are_rejected(self):
        self.make_wheel(corrupt_record=True)
        with self.assertRaisesRegex(ValueError, "RECORD"):
            verify_wheel(self.path, self.hashes)
        self.make_wheel({"bodhan_eval/__main__.py": b"changed\n"})
        with self.assertRaisesRegex(ValueError, "source"):
            verify_wheel(self.path, self.hashes)

    def test_configuration_values_are_not_allowed_in_wheel_contents(self):
        self.make_wheel({"bodhan_eval/__main__.py": b"private_test_value"})
        hashes = {**self.hashes, "bodhan_eval/__main__.py": hashlib.sha256(b"private_test_value").hexdigest()}
        with self.assertRaisesRegex(ValueError, "configuration"):
            verify_wheel(self.path, hashes, ("private_test_value",))

    def test_backend_must_be_exactly_pinned(self):
        self.assertEqual(backend_requirement('[build-system]\nrequires = ["setuptools==80.9.0"]\nbuild-backend = "setuptools.build_meta"'), "80.9.0")
        with self.assertRaises(ValueError):
            backend_requirement('[build-system]\nrequires = ["setuptools>=80"]')


if __name__ == "__main__":
    unittest.main()
