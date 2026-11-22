import json

from bodhan_core import ledger
from bodhan_core.audio import join_wavs, split_wav
from bodhan_core.cache import ResultCache
from bodhan_core.errors import ToolError
from test_media_tools import wav_bytes


def test_cache_counts_hits():
    c = ResultCache()
    c["a"] = 1
    assert "a" in c and "b" not in c and c["a"] == 1 and c.hits == 1
    c.clear()
    assert len(c) == 0 and c.hits == 0


def test_error_shape():
    assert ToolError(code="x", message="y").retryable is False


def test_wav_split_and_join_roundtrip():
    windows, seconds = split_wav(wav_bytes(7), 3)
    assert len(windows) == 3 and seconds == 7
    joined, joined_s = join_wavs(windows)
    assert joined_s == 7


def test_ledger_record(tmp_path):
    p = tmp_path / "l.json"
    p.write_text(json.dumps({"total_credit_inr": 10.0, "spent_inr": 0, "remaining_inr": 10.0, "entries": []}))
    assert ledger.record("test", "indic-ocr", 0.2, p) == 9.8
    assert ledger.remaining(p) == 9.8
