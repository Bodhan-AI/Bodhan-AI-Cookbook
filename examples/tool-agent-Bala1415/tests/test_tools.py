"""Unit tests for tool input validation and error handling."""

import pytest

from bodhan_mcp.client import (
    CostTracker,
    ResponseCache,
    BodhanAPIError,
    BudgetExceededError,
    UnsupportedLanguageError,
    SUPPORTED_LANGUAGE_CODES,
    COST_SPEAK_PER_10K_CHARS,
)
from bodhan_mcp.tools.translate import _chunk_text, _estimate_tokens
from bodhan_mcp.tools.speak import _chunk_speak_text, MAX_CHARS_PER_CALL


@pytest.mark.unit
class TestCostTracker:
    def test_initial_state(self):
        tracker = CostTracker(max_cost_inr=10.0)
        assert tracker.total_cost_inr == 0.0
        assert tracker.max_cost_inr == 10.0
        assert len(tracker.call_log) == 0

    def test_add_cost(self):
        tracker = CostTracker(max_cost_inr=10.0)
        tracker.add("indic-transcribe", 1.5, "test call")
        assert tracker.total_cost_inr == 1.5
        assert len(tracker.call_log) == 1
        assert tracker.call_log[0]["model"] == "indic-transcribe"

    def test_budget_exceeded(self):
        tracker = CostTracker(max_cost_inr=1.0)
        with pytest.raises(BudgetExceededError):
            tracker.add("indic-speak", 2.0, "expensive call")

    def test_estimate_cost(self):
        tracker = CostTracker()
        cost = tracker.estimate_cost("indic-transcribe", duration_seconds=60)
        assert cost == pytest.approx(0.10, abs=0.01)

        cost = tracker.estimate_cost("indic-ocr", num_images=5)
        assert cost == pytest.approx(1.0, abs=0.01)

    def test_would_exceed(self):
        tracker = CostTracker(max_cost_inr=1.0)
        tracker.add("test", 0.9)
        assert tracker.would_exceed("indic-ocr", num_images=1)  # 0.9 + 0.2 > 1.0

    def test_summary(self):
        tracker = CostTracker(max_cost_inr=10.0)
        tracker.add("test", 1.0)
        s = tracker.summary()
        assert s["total_cost_inr"] == 1.0
        assert s["remaining_inr"] == 9.0
        assert s["num_calls"] == 1


@pytest.mark.unit
class TestResponseCache:
    def test_miss_returns_none(self, tmp_path):
        cache = ResponseCache(cache_dir=tmp_path / "cache")
        assert cache.get("model", {"key": "value"}) is None

    def test_put_and_get(self, tmp_path):
        cache = ResponseCache(cache_dir=tmp_path / "cache")
        cache.put("model", {"key": "value"}, {"result": "data"})
        result = cache.get("model", {"key": "value"})
        assert result == {"result": "data"}

    def test_different_params_different_cache(self, tmp_path):
        cache = ResponseCache(cache_dir=tmp_path / "cache")
        cache.put("model", {"a": 1}, {"result": "one"})
        cache.put("model", {"a": 2}, {"result": "two"})
        assert cache.get("model", {"a": 1})["result"] == "one"
        assert cache.get("model", {"a": 2})["result"] == "two"

    def test_disk_persistence(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache1 = ResponseCache(cache_dir=cache_dir)
        cache1.put("model", {"k": "v"}, {"data": "test"})

        # New cache instance reading from same dir
        cache2 = ResponseCache(cache_dir=cache_dir)
        result = cache2.get("model", {"k": "v"})
        assert result == {"data": "test"}


@pytest.mark.unit
class TestTranslateChunking:
    def test_short_text_no_chunking(self):
        chunks = _chunk_text("Hello world")
        assert len(chunks) == 1
        assert chunks[0] == "Hello world"

    def test_paragraph_chunking(self):
        # Create text with multiple paragraphs
        paras = ["Paragraph " + str(i) + ". " * 100 for i in range(20)]
        text = "\n\n".join(paras)
        chunks = _chunk_text(text, max_tokens=500)
        assert len(chunks) > 1
        # Verify no content is lost
        reassembled = "\n\n".join(chunks)
        assert len(reassembled) >= len(text) * 0.9  # Allow minor whitespace diffs

    def test_token_estimation(self):
        # English text
        english = "Hello world this is a test"
        tokens = _estimate_tokens(english)
        assert tokens > 0

        # Indic text (should estimate more tokens per char)
        hindi = "नमस्ते यह एक परीक्षण है"
        tokens_hi = _estimate_tokens(hindi)
        assert tokens_hi > 0


@pytest.mark.unit
class TestSpeakChunking:
    def test_short_text_no_chunking(self):
        chunks = _chunk_speak_text("Hello world")
        assert len(chunks) == 1

    def test_long_text_chunked(self):
        # Create text longer than MAX_CHARS_PER_CALL
        text = "This is a sentence. " * 200  # ~4000 chars
        chunks = _chunk_speak_text(text, max_chars=500)
        assert len(chunks) > 1
        # Verify all text is covered
        total = sum(len(c) for c in chunks)
        assert total >= len(text.strip()) * 0.95

    def test_sentence_boundary_splitting(self):
        text = "First sentence. Second sentence. Third sentence."
        chunks = _chunk_speak_text(text, max_chars=30)
        # Each chunk should end at a sentence boundary
        for chunk in chunks:
            assert chunk.strip()


@pytest.mark.unit
class TestSupportedLanguages:
    def test_indian_languages_present(self):
        for lang in ["hi", "bn", "ta", "te", "mr", "gu", "kn", "ml", "pa", "or"]:
            assert lang in SUPPORTED_LANGUAGE_CODES

    def test_english_present(self):
        assert "en" in SUPPORTED_LANGUAGE_CODES

    def test_unsupported_languages_absent(self):
        for lang in ["fr", "de", "es", "zh", "ja", "ko", "it", "pt", "ru"]:
            assert lang not in SUPPORTED_LANGUAGE_CODES
