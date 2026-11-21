import json

import httpx
import pytest

from bodhan_eval.cache import Cache
from bodhan_eval.judge_stage import run_judge_consistency, run_judge_stage
from bodhan_eval.models import Item


class ScriptedJudge:
    def __init__(self, replies: list[str | Exception]) -> None:
        self.replies = iter(replies)
        self.calls: list[tuple[str, str, float]] = []

    async def chat(self, system: str, user: str, temperature: float = 0.0) -> str:
        self.calls.append((system, user, temperature))
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _record(*flags: dict) -> dict:
    return {
        "id": "item-1",
        "lang": "hi",
        "terminal_state": "scored",
        "checks_run": ["text_length"],
        "flags": list(flags),
        "decisions": [],
        "failed_checks": [],
        "skipped_reason": None,
    }


def _candidate_flag() -> dict:
    return {
        "category": "omission",
        "severity": "minor",
        "modality": "text",
        "span": None,
        "source_span": [0, 10],
        "audio_span_s": None,
        "evidence": {"length_ratio": 0.2},
        "detectors": ["text_length"],
        "confidence": 0.55,
        "suppressed_reason": "single_noisy_signal",
    }


@pytest.mark.asyncio
async def test_candidate_runs_blind_and_confirmation_passes_without_downgrading() -> None:
    deterministic = _candidate_flag()
    item = Item(
        id="item-1",
        lang="hi",
        source="Value 100",
        translation="Value 200",
        reference="Reference 200",
    )
    judge = ScriptedJudge([
        json.dumps({"findings": []}),
        json.dumps({"findings": []}),
    ])
    records = [_record(deterministic)]

    await run_judge_stage([item], records, judge, enabled=True)

    assert len(judge.calls) == 2
    assert all(call[2] == 0.0 for call in judge.calls)
    assert records[0]["flags"] == [deterministic]
    assert records[0]["judge"]["status"] == "complete"
    assert "candidate_hints:" in judge.calls[1][1]
    assert "omission" in judge.calls[1][1]


@pytest.mark.asyncio
async def test_judge_only_flag_requires_blind_and_confirmation_agreement() -> None:
    item = Item(
        id="item-1",
        lang="hi",
        source="Value 100",
        translation="Value 200",
    )
    finding = {
        "category": "mistranslation",
        "severity": "major",
        "target_quote": "200",
        "source_quote": "100",
        "rationale": "The number differs.",
    }
    judge = ScriptedJudge([
        json.dumps({"findings": [finding]}),
        json.dumps({"findings": [finding]}),
    ])
    records = [_record(_candidate_flag())]

    await run_judge_stage([item], records, judge, enabled=True)

    accepted = [
        flag for flag in records[0]["flags"]
        if "llm_judge" in flag["detectors"]
    ]
    assert len(accepted) == 1
    assert accepted[0]["category"] == "mistranslation"
    assert accepted[0]["span"] == [6, 9]
    assert records[0]["judge"]["consensus_findings"] == 1


@pytest.mark.asyncio
async def test_unavailable_judge_preserves_deterministic_flags_and_marks_unchecked() -> None:
    deterministic = _candidate_flag()
    judge = ScriptedJudge([
        httpx.ReadTimeout("mock timeout"),
        httpx.ReadTimeout("mock timeout"),
    ])
    records = [_record(deterministic)]

    await run_judge_stage(
        [Item("item-1", "hi", "Source", "Translation")],
        records,
        judge,
        enabled=True,
    )

    assert records[0]["flags"] == [deterministic]
    assert records[0]["terminal_state"] == "failed_checks"
    assert "judge_unavailable" in records[0]["failed_checks"]
    assert records[0]["judge"]["status"] == "unavailable"


@pytest.mark.asyncio
async def test_disabled_judge_marks_candidate_unchecked() -> None:
    deterministic = _candidate_flag()
    records = [_record(deterministic)]

    await run_judge_stage(
        [Item("item-1", "hi", "Source", "Translation")],
        records,
        None,
        enabled=False,
    )

    assert records[0]["judge"]["status"] == "disabled"
    assert records[0]["terminal_state"] == "failed_checks"
    assert records[0]["flags"] == [deterministic]


@pytest.mark.asyncio
async def test_judge_cache_stores_only_validated_english_findings(tmp_path) -> None:
    item = Item(
        id="item-1",
        lang="hi",
        source="Value 100",
        translation="Value 200",
    )
    finding = {
        "category": "mistranslation",
        "severity": "major",
        "target_quote": "200",
        "source_quote": "100",
        "rationale": "The number differs \u0939\u093f\u0902\u0926\u0940.",
        "corrected": "\u0938\u0941\u0927\u093e\u0930",
    }
    cache = Cache(tmp_path)
    records = [_record(_candidate_flag())]
    first_client = ScriptedJudge([
        json.dumps({"findings": [finding]}),
        json.dumps({"findings": [finding]}),
    ])

    await run_judge_stage(
        [item],
        records,
        first_client,
        enabled=True,
        cache=cache,
        model="mock",
        base_url="https://mock.invalid/v1",
    )
    cached_content = b"".join(path.read_bytes() for path in tmp_path.rglob("*.json"))

    second_client = ScriptedJudge([])
    second_records = [_record(_candidate_flag())]
    await run_judge_stage(
        [item],
        second_records,
        second_client,
        enabled=True,
        cache=cache,
        model="mock",
        base_url="https://mock.invalid/v1",
    )

    assert len(first_client.calls) == 2
    assert second_client.calls == []
    assert b"corrected" not in cached_content
    assert b"\xe0\xa4" not in cached_content
    assert second_records[0]["judge"]["status"] == "complete"


@pytest.mark.asyncio
async def test_judge_consistency_bypasses_cache_and_reports_agreement(tmp_path) -> None:
    items = [
        Item("item-1", "hi", "Source one", "Translation one"),
        Item("item-2", "ta", "Source two", "Translation two"),
    ]
    records = [
        _record(_candidate_flag()),
        _record(_candidate_flag()),
    ]
    records[1]["id"] = "item-2"
    records[1]["lang"] = "ta"
    client = ScriptedJudge([
        json.dumps({"findings": []}),
        json.dumps({"findings": []}),
        json.dumps({"findings": []}),
        json.dumps({"findings": []}),
        json.dumps({"findings": []}),
        json.dumps({"findings": []}),
    ])

    result = await run_judge_consistency(
        items,
        records,
        client,
        Cache(tmp_path),
        model="mock",
        base_url="https://mock.invalid/v1",
        sample_size=2,
        runs=3,
    )

    assert result["status"] == "ok"
    assert result["sample_size"] == 2
    assert result["flag_agreement"] == 1.0
    assert result["category_agreement"] == 1.0
