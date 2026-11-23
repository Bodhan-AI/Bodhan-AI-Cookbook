import asyncio
from types import SimpleNamespace

import openai
import pytest

from bodhan_eval.cache import Cache
from bodhan_eval.concurrency import ConcurrencyLimiter
from bodhan_eval.cost import BudgetExceeded, CostLedger
from bodhan_eval.judge import OpenAIJudge
from bodhan_eval.logging_utils import StructuredLogger
from bodhan_eval.models import Item


def test_priced_judge_reserves_and_accounts_for_uncached_tokens(tmp_path, monkeypatch):
    calls = []

    class Completions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                content='{"verdict":"ok","errors":[]}'))],
                usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50))

    monkeypatch.setattr(openai, "AsyncOpenAI", lambda **_: SimpleNamespace(
        chat=SimpleNamespace(completions=Completions())))
    cache = Cache(tmp_path / "cache")
    ledger = CostLedger(max_inr=1)
    judge = OpenAIJudge("https://example.test", "test", "test-model", cache, ConcurrencyLimiter(1),
                        StructuredLogger(None), ledger=ledger, input_rate=10, output_rate=20)
    item = Item(id="x", source="Hello", lang="hi", translation="नमस्ते")
    assert asyncio.run(judge.judge(item, "Hindi")).verdict == "ok"
    assert abs(ledger.total - 0.002) < 1e-9
    assert asyncio.run(judge.judge(item, "Hindi")).verdict == "ok"
    assert len(calls) == 1 and abs(ledger.total - 0.002) < 1e-9

    blocked = OpenAIJudge("https://example.test", "test", "test-model", Cache(tmp_path / "other"),
                          ConcurrencyLimiter(1), StructuredLogger(None), ledger=CostLedger(max_inr=0),
                          input_rate=10, output_rate=20)
    with pytest.raises(BudgetExceeded):
        asyncio.run(blocked.judge(item, "Hindi"))
    assert len(calls) == 1
