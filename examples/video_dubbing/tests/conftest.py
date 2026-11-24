"""
Pytest configuration and shared fixtures for bodhan_dub tests.
"""

import sys
from pathlib import Path

import pytest

# Ensure src/ is on sys.path
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from bodhan_dub.config import Settings
from bodhan_dub.gateway.cache import DiskCache
from bodhan_dub.gateway.cost import CostLedger
from bodhan_dub.logging import PipelineLogger


@pytest.fixture
def test_settings():
    return Settings(
        bodhan_base_url="https://api.bodhan.ai/v1",
        bodhan_transcribe_key="sk-test-transcribe-key",
        bodhan_translate_key="sk-test-translate-key",
        bodhan_speak_key="sk-test-speak-key",
        llm_api_key="sk-test-llm-key",
        semaphore_asr=2,
        semaphore_translate=2,
        semaphore_tts=2,
        semaphore_llm=2,
    )


@pytest.fixture
def test_cache(tmp_path):
    return DiskCache(cache_dir=tmp_path / "cache", version="test-v1")


@pytest.fixture
def test_ledger():
    return CostLedger()


@pytest.fixture
def test_logger(tmp_path):
    return PipelineLogger(
        name="test_logger",
        run_id="test_run_001",
        log_file=tmp_path / "test.log.jsonl",
    )
