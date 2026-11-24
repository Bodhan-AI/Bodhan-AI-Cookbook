"""Unit tests for disk-based caching."""

import shutil
import tempfile
import unittest
from pathlib import Path
from bodhan_mcp.cache import DiskCache


class TestCache(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.cache = DiskCache(cache_dir=self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_json_caching(self):
        key = self.cache.generate_key("indic-translate", "Hello world", target="hi")
        self.assertFalse(self.cache.has(key))

        payload = {"translated_text": "नमस्ते दुनिया"}
        self.cache.set_json(key, payload)

        self.assertTrue(self.cache.has(key))
        retrieved = self.cache.get_json(key)
        self.assertEqual(retrieved, payload)

    def test_bytes_caching(self):
        key = self.cache.generate_key("indic-speak", "Sample speech input")
        self.assertFalse(self.cache.has(key))

        fake_audio = b"RIFFFAKEWAVEDATA"
        self.cache.set_bytes(key, fake_audio)

        self.assertTrue(self.cache.has(key))
        retrieved = self.cache.get_bytes(key)
        self.assertEqual(retrieved, fake_audio)


if __name__ == "__main__":
    unittest.main()
