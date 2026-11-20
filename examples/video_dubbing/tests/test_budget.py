import io
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from urllib.error import HTTPError
from urllib.request import OpenerDirector

import pytest
from bodhan_dub.budget import Budget, BudgetError, asr_cost, speak_cost, translation_cost
from bodhan_dub.providers import Provider, ProviderError


def test_published_rates():
    assert asr_cost(30) == 0.05
    assert speak_cost('क' * 100) == 0.06
    assert translation_cost(1000) == 0.02


def test_concurrent_requests_compete_for_one_persistent_budget(tmp_path):
    path = tmp_path / 'ledger.sqlite3'
    budget = Budget(path, cap_inr=1)
    workers = 8
    barrier = Barrier(workers)

    def send(number):
        instance = Budget(path)
        barrier.wait(timeout=10)
        try:
            return instance.reserve(0.3, stage='speak', language='ta', key=f'segment-{number}')
        except BudgetError:
            return None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        reservations = [result for result in pool.map(send, range(workers)) if result is not None]

    assert len(reservations) == 3
    report = budget.snapshot()
    assert report['prior_smoke_estimate_inr'] == 0.02
    assert report['lifetime']['uncertain_inr'] == 0.9
    assert report['available_inr'] == 0.08


def test_rejected_call_refunds_pool_and_success_commits_cache(tmp_path):
    path = tmp_path / 'ledger.sqlite3'
    budget = Budget(path, cap_inr=1, prior_inr=0)
    baseline = budget.hold(0.9)
    attempt = budget.reserve(0.4, stage='speak', language='bn', key='speech', pool=baseline)
    budget.finish(attempt, 'rejected', elapsed=0.1, detail='HTTP 429')
    retry = budget.reserve(0.4, stage='speak', language='bn', key='speech', pool=baseline, attempt=2)
    budget.finish(retry, 'estimated', elapsed=0.2, response=b'sample')
    with pytest.raises(BudgetError, match='available ₹0.100000'):
        budget.hold(0.2, label='repair')
    assert budget.snapshot()['held_inr'] == 0.5
    budget.release(baseline)

    resumed = Budget(path)
    assert resumed.cached('speech') == b'sample'
    resumed.cache_hit('speak', 'bn', 'speech')
    report = resumed.snapshot()
    assert report['available_inr'] == 0.6
    assert report['lifetime']['retries'] == 1
    assert report['run']['cache_hits'] == 1
    assert report['run']['spent_inr'] == 0


def test_crashed_pool_reclaims_unused_capacity_and_keeps_inflight_charge(tmp_path):
    path = tmp_path / 'ledger.sqlite3'
    source = Path(__file__).resolve().parents[1] / 'src'
    program = '''
import os
import sys
from bodhan_dub.budget import Budget

budget = Budget(sys.argv[1], cap_inr=1, prior_inr=0)
pool = budget.hold(0.95)
call_id = budget.reserve(0.25, stage='qa', language='hi', key='first', pool=pool)
print(call_id, flush=True)
os._exit(0)
'''
    crashed = subprocess.run([sys.executable, '-c', program, str(path)], check=True, capture_output=True, text=True,
                             env={**os.environ, 'PYTHONPATH': str(source)})
    resumed = Budget(path)
    report = resumed.snapshot()
    assert report['held_inr'] == 0
    assert report['lifetime']['uncertain_inr'] == 0.25
    assert report['available_inr'] == 0.75
    resumed.reconcile(crashed.stdout.strip(), 0.2)
    report = resumed.snapshot()
    assert report['lifetime']['uncertain_inr'] == 0
    assert report['lifetime']['measured_usage_inr'] == 0.2
    assert report['available_inr'] == 0.8


def test_rejected_rate_limit_retries_but_invalid_response_is_not_cached(tmp_path, monkeypatch):
    budget = Budget(tmp_path / 'ledger.sqlite3', cap_inr=1, prior_inr=0)
    provider = Provider(budget, transcribe_key='placeholder-stt', translate_key='placeholder-mt',
                        speak_key='placeholder-tts', bodhan_url='http://localhost:3000')
    requests = []

    class Reply(io.BytesIO):
        headers = {}

    def hosted_reply(_opener, request, **kwargs):
        requests.append(json.loads(request.data))
        if len(requests) == 1:
            error = io.BytesIO(b'{"error":{"code":"rate_limit_exceeded_error"}}')
            raise HTTPError(request.full_url, 429, 'Too Many Requests', {'Retry-After': '0'}, error)
        if len(requests) == 2:
            return Reply(b'{"choices": []}')
        return Reply(json.dumps({
            'choices': [{'message': {'content': 'नमस्ते'}, 'finish_reason': 'stop'}],
            'usage': {'completion_tokens': 4},
        }).encode())

    monkeypatch.setattr(OpenerDirector, 'open', hosted_reply)
    with pytest.raises(ProviderError, match='missing message content'):
        provider.translate('Hello', 'hi', max_tokens=20)
    assert provider.translate('Hello', 'hi', max_tokens=20) == 'नमस्ते'
    assert provider.translate('Hello', 'hi', max_tokens=20) == 'नमस्ते'
    assert len(requests) == 3
    assert all(request['max_output_tokens'] == 20 for request in requests)
    report = budget.snapshot()
    assert [call['status'] for call in report['calls']] == ['rejected', 'estimated', 'measured', 'cached']
    assert report['run']['estimated_inr'] == pytest.approx(20 * 0.00002)
    assert report['run']['measured_usage_inr'] == pytest.approx(4 * 0.00002)
