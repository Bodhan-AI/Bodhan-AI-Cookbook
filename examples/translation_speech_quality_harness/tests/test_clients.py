import json
import threading

import pytest
from conftest import make_clients, make_wav

from bodhan_eval.clients import MAX_ATTEMPTS, Budget, NotRun


def test_translate_is_cached_across_client_instances(tmp_path, fake_bodhan, fake_llm):
    bodhan, _ = make_clients(tmp_path, fake_bodhan, fake_llm)
    first = bodhan.translate("Hello.", "hi")
    assert first["text"] == "[Hindi] Hello."
    sent = json.loads(fake_bodhan.requests[0].content)
    assert sent["temperature"] == 0 and sent["model"] == "indic-translate" and len(sent["messages"]) == 1
    again, _ = make_clients(tmp_path, fake_bodhan, fake_llm)  # new process, same cache folder
    assert again.translate("Hello.", "hi")["text"] == first["text"]
    assert len(fake_bodhan.requests) == 1
    assert [e["cache"] for e in again.log.calls()] == ["hit"]


def test_retries_429_and_transient_403(tmp_path, fake_bodhan, fake_llm):
    bodhan, _ = make_clients(tmp_path, fake_bodhan, fake_llm)
    fake_bodhan.queue = [(429, {"error": {}}, {"Retry-After": "3"}), (403, {"error": {"message": "Invalid or missing authentication credentials"}}, None),
                         (502, {"error": {}}, None)]
    assert bodhan.translate("Hi.", "ta")["text"] == "[Tamil] Hi."
    assert len(fake_bodhan.requests) == 4
    assert bodhan.log.calls()[0]["retries"] == 3


def test_model_access_denied_is_not_retried(tmp_path, fake_bodhan, fake_llm):
    bodhan, _ = make_clients(tmp_path, fake_bodhan, fake_llm)
    fake_bodhan.queue = [(403, {"error": {"type": "key_model_access_denied"}}, None)]
    with pytest.raises(NotRun, match="auth"):
        bodhan.translate("Hi.", "hi")
    assert len(fake_bodhan.requests) == 1


def test_gives_up_after_max_attempts_and_caches_nothing(tmp_path, fake_bodhan, fake_llm):
    bodhan, _ = make_clients(tmp_path, fake_bodhan, fake_llm)
    fake_bodhan.queue = [(500, {"error": {}}, None)] * MAX_ATTEMPTS
    with pytest.raises(NotRun, match=f"failed after {MAX_ATTEMPTS}"):
        bodhan.translate("Hi.", "hi")
    assert bodhan.translate("Hi.", "hi")["text"] == "[Hindi] Hi."  # failure was not cached


def test_missing_key_is_not_run(tmp_path, fake_bodhan, fake_llm):
    bodhan, _ = make_clients(tmp_path, fake_bodhan, fake_llm, keys={"translate": ""})
    with pytest.raises(NotRun, match="BODHAN_TRANSLATE_KEY"):
        bodhan.translate("Hi.", "hi")
    assert not fake_bodhan.requests


def test_truncation_retries_with_larger_budget_then_gives_up(tmp_path, fake_bodhan, fake_llm):
    bodhan, _ = make_clients(tmp_path, fake_bodhan, fake_llm)
    cut = {"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}], "usage": {"completion_tokens": 5}}
    fake_bodhan.queue = [(200, cut, None)]
    assert bodhan.translate("Hi.", "hi")["text"] == "[Hindi] Hi."
    budgets = [json.loads(r.content)["max_tokens"] for r in fake_bodhan.requests]
    assert budgets[1] == 2 * budgets[0]
    fake_bodhan.queue = [(200, cut, None), (200, cut, None)]
    with pytest.raises(NotRun, match="truncated"):
        bodhan.translate("Other.", "hi")


def test_budget_blocks_calls_and_never_overshoots(tmp_path, fake_bodhan, fake_llm):
    bodhan, _ = make_clients(tmp_path, fake_bodhan, fake_llm, budget=0.0)
    with pytest.raises(NotRun, match="budget"):
        bodhan.speak("नमस्ते", "hi")
    assert not fake_bodhan.requests

    b = Budget(1.0)
    ok = []

    def worker():
        try:
            b.reserve(0.3)
            ok.append(1)
        except NotRun:
            pass

    threads = [threading.Thread(target=worker) for _ in range(10)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(ok) == 3 and b.spent <= 1.0


def test_speak_returns_wav_and_rejects_non_wav(tmp_path, fake_bodhan, fake_llm):
    bodhan, _ = make_clients(tmp_path, fake_bodhan, fake_llm)
    assert bodhan.speak("text", "hi")[:4] == b"RIFF"
    sent = json.loads(fake_bodhan.requests[0].content)
    assert sent["voice"] == "Kavya" and json.loads(sent["instructions"]) == {"lang": "hi"}
    fake_bodhan.queue = [(200, b"ID3notwav", None)]
    with pytest.raises(NotRun, match="non-WAV"):
        bodhan.speak("other", "hi")
    with pytest.raises(NotRun, match="empty"):
        bodhan.speak("  ", "hi")


def test_transcribe_cost_and_cache(tmp_path, fake_bodhan, fake_llm):
    bodhan, _ = make_clients(tmp_path, fake_bodhan, fake_llm)
    wav = make_wav(6.0)
    assert bodhan.transcribe(wav, "hi") == "transcript"
    assert bodhan.transcribe(wav, "hi") == "transcript"
    assert len(fake_bodhan.requests) == 1
    assert bodhan.log.calls()[0]["cost_inr"] == pytest.approx(0.01)
    with pytest.raises(NotRun, match="WAV"):
        bodhan.transcribe(b"not audio", "hi")


def test_llm_drops_rejected_parameters(tmp_path, fake_bodhan, fake_llm):
    _, llm = make_clients(tmp_path, fake_bodhan, fake_llm)
    fake_llm.reply = '{"ok": true}'
    fake_llm.reject = {"temperature"}
    assert llm.chat([{"role": "user", "content": "q"}]) == '{"ok": true}'
    assert "temperature" not in fake_llm.requests[-1]
    assert llm.chat([{"role": "user", "content": "q"}]) == '{"ok": true}'  # cached
    assert len(fake_llm.requests) == 2


def test_llm_unavailable_is_not_run(tmp_path, fake_bodhan, fake_llm):
    _, llm = make_clients(tmp_path, fake_bodhan, fake_llm, llm=False)
    with pytest.raises(NotRun, match="LLM_BASE_URL"):
        llm.chat([{"role": "user", "content": "q"}])
