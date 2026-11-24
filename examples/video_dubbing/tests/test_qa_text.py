from bodhan_dub import qa, textutil
from bodhan_dub.llm import Brain, contains_indic


def test_cer_basics():
    assert qa.cer("नमस्ते दुनिया", "नमस्ते दुनिया") == 0.0
    assert 0 < qa.cer("hello world", "hello word") < 0.2


def test_repeat_and_omission_detection():
    ref = "आज हम त्रिभुज का क्षेत्रफल सीखेंगे"
    assert qa.assess(ref, ref)["ok"]
    rep = qa.assess(ref, ref + " " + ref)
    assert "repeat" in rep["flags"]
    om = qa.assess(ref, "आज हम")
    assert "omission" in om["flags"]


def test_legit_repeated_phrase_is_not_flagged():
    ref = "बार बार बार बार कहो बार बार बार बार"
    assert "repeat" not in qa.assess(ref, ref)["flags"]


def test_numbers_preserved_across_scripts():
    assert qa.numbers_preserved("the sum is 3,000 and 45", "योग 3000 और ४५ है") == []
    assert qa.numbers_preserved("take 12 apples", "सेब लो") == ["12"]


def test_split_for_tts_keeps_words_whole_and_short():
    text = "यह पहला वाक्य है। " * 30
    parts = textutil.split_for_tts(text, 120)
    assert all(len(p) <= 125 for p in parts)
    assert " ".join(parts).split() == text.split()


def test_glossary_user_translation_substitution():
    gl = [{"term": "Pythagoras theorem", "pin": True, "translations": {"hi": "पाइथागोरस प्रमेय"}}]
    t, used = textutil.apply_user_translations("We prove the Pythagoras theorem today", gl, "hi")
    assert "पाइथागोरस प्रमेय" in t and used == ["Pythagoras theorem"]
    t2, used2 = textutil.apply_user_translations("We prove the Pythagoras theorem today", gl, "ta")
    assert used2 == [] and t2 == "We prove the Pythagoras theorem today"


def test_llm_output_with_indic_script_is_rejected():
    from conftest import FakeLLM
    llm = FakeLLM()
    brain = Brain(client=llm, model="m")
    llm.shorten = False
    assert contains_indic("नमस्ते") and not contains_indic("hello")
    assert brain.condense("A long English sentence here", 0.5, []) is None   # reply was not shorter


def test_invalid_llm_grouping_falls_back_to_heuristic():
    from conftest import FakeLLM
    chunks = [{"start": 0, "end": 2}, {"start": 3, "end": 5}]
    groups, how = Brain(client=FakeLLM(bad_groups=True), model="m").group_sentences(["one.", "two."], chunks)
    assert how == "heuristic" and groups == [[0], [1]]


def test_voice_selection_uses_the_languages_own_voices_and_overrides():
    from bodhan_dub.pipeline import pick_voice
    assert pick_voice({}, "ta", "S1", "female") == "Anitha"
    assert pick_voice({}, "ta", "S2", "male") == "Arun"
    assert pick_voice({}, "hi", "S2", "female", ["S1", "S2"]) == "Suhani"       # two women: second Hindi voice
    assert pick_voice({"female": "X"}, "hi", "S1", "female") == "X"
    assert pick_voice({"speakers": {"S1": "Zed"}, "female": "X"}, "hi", "S1", "female") == "Zed"
    assert pick_voice({}, "xx", "S1", "male") == "Amit"


def test_translate_falls_back_to_v1_path_on_404():
    import httpx

    from bodhan_dub import config
    from bodhan_dub.bodhan import BodhanClient
    seen = []

    def handler(req):
        seen.append(req.url.path)
        if req.url.path == "/translate":
            return httpx.Response(404, json={"detail": "Not Found"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "नमस्ते"}}], "usage": {"completion_tokens": 3}})

    keys = config.Keys("a", "b", "c")
    c = BodhanClient(keys, None, http=httpx.Client(transport=httpx.MockTransport(handler)), backoff=0)
    assert c.translate("hello", "hi")["text"] == "नमस्ते"
    assert seen == ["/translate", "/v1/chat/completions"]
    c.translate("again", "hi")
    assert seen[-1] == "/v1/chat/completions" and seen.count("/translate") == 1     # remembers the working path


def test_preflight_fails_fast_with_hint_on_wrong_key():
    import httpx
    import pytest

    from bodhan_dub import config
    from bodhan_dub.bodhan import BodhanClient, BodhanError
    from bodhan_dub.pipeline import preflight
    calls = []

    def handler(req):
        calls.append(req.url.path)
        return httpx.Response(404, json={"detail": "Not Found"})

    c = BodhanClient(config.Keys("a", "b", "c"), None, http=httpx.Client(transport=httpx.MockTransport(handler)), backoff=0)
    with pytest.raises(BodhanError) as e:
        preflight(c, "hi")
    assert "indic-translate" in str(e.value) and "each Bodhan key serves one model" in str(e.value)
    assert set(calls) == {"/translate", "/v1/chat/completions"}      # nothing else was called, no transcription spend


def test_console_log_line_is_readable_and_flags_errors(capsys, tmp_path):
    from bodhan_dub import logs
    logs.setup(tmp_path / "r.jsonl", echo=True, tag="abc123")
    logs.log("api_error", api="translate", status=404, body='{"detail":"Not Found"}')
    logs.log("stage", stage="vad", spans=3)
    out = capsys.readouterr().out.splitlines()
    assert "ERROR [abc123] api_error api=translate status=404" in out[0]
    assert "INFO  [abc123] stage stage=vad spans=3" in out[1]
    logs.setup(None, echo=False)


def test_429_shows_bodhans_own_message_and_paces_after_it():
    import httpx

    from bodhan_dub.bodhan import BodhanClient, BodhanError
    from bodhan_dub.config import Keys
    n = {"c": 0}

    def handler(req):
        n["c"] += 1
        return httpx.Response(429, json={"detail": "Too many concurrent requests for this key"}, headers={"retry-after": "0"})

    c = BodhanClient(Keys("t", "m", "s"), None, max_attempts=2, backoff=0.01, http=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        c._request("transcribe", "transcribe", lambda: c.http.post("http://x/v1/audio/transcriptions"))
        raise AssertionError("should fail")
    except BodhanError as e:
        assert e.status == 429 and "Too many concurrent requests" in str(e) and "DUB_CONCURRENCY=1" in str(e)
    assert n["c"] == 2                                  # retries count against Bodhan's request limit: no extra attempts
    assert c.pace["transcribe"] > 0                     # and the client slowed itself down


def test_client_learns_bodhans_rate_limit_headers_and_spaces_its_calls(monkeypatch):
    import httpx

    from bodhan_dub import bodhan
    from bodhan_dub.bodhan import BodhanClient
    from bodhan_dub.config import Keys
    sleeps = []
    monkeypatch.setattr(bodhan.time, "sleep", lambda s: sleeps.append(s))
    h = {"x-ratelimit-limit-requests": "10", "x-ratelimit-limit-parallel-requests": "1"}
    c = BodhanClient(Keys("t", "m", "s"), None, max_concurrency=4, backoff=1.0,
                     http=httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={}, headers=h))))
    for _ in range(3):
        c._request("translate", "translate", lambda: c.http.post("http://x/translate"))
    assert abs(c.floor["translate"] - 6.9) < 0.01                  # 10 per minute -> one request every 6.9 s (with 15% headroom)
    assert c._gate("translate").limit == 1                         # the parallel limit shrinks the user's concurrency of 4
    # sleep is mocked, so time stands still: the 2nd call waits one slot, the 3rd waits two
    assert len(sleeps) == 2 and abs(sleeps[0] - 6.9) < 0.1 and abs(sleeps[1] - 13.8) < 0.1
    assert c._gate("transcribe").limit == 4                        # other models keep their own limits


def test_budget_exceeded_429_is_not_retried_and_is_explained():
    import httpx

    from bodhan_dub.bodhan import BodhanClient, BodhanError
    from bodhan_dub.config import Keys
    n = {"c": 0}
    body = {"error": {"message": "ExceededBudget: User=u over budget. Spend=10.19, Budget=10.0", "type": "budget_exceeded", "code": "429"}}

    def handler(req):
        n["c"] += 1
        return httpx.Response(429, json=body)

    c = BodhanClient(Keys("t", "m", "s"), None, max_attempts=4, backoff=0.01, http=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        c._request("translate", "translate", lambda: c.http.post("http://x/translate"))
        raise AssertionError("should fail")
    except BodhanError as e:
        assert e.status == 429 and e.code == "budget_exceeded"
        assert "over budget" in str(e) and "not a rate limit" in str(e) and "DUB_CONCURRENCY" not in str(e)
    assert n["c"] == 1                                   # waiting cannot fix a spent budget, so no retries


class _Flaky:
    """An LLM client that answers 429 a few times, then works; can also reject the temperature parameter."""
    def __init__(self, fail=2, reject_temperature=False):
        from types import SimpleNamespace
        self.fail, self.reject_temperature, self.n, self.kwargs = fail, reject_temperature, 0, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        from types import SimpleNamespace
        self.n += 1
        self.kwargs.append(kw)
        if self.reject_temperature and "temperature" in kw:
            e = Exception("temperature not supported")
            e.status_code = 400
            raise e
        if self.fail > 0:
            self.fail -= 1
            e = Exception("Error code: 429 rate limit")
            e.status_code = 429
            e.response = SimpleNamespace(headers={"retry-after": "3"})
            raise e
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"groups": [[0, 1]]}'))])


def test_llm_waits_and_retries_on_429_instead_of_falling_back(monkeypatch):
    from bodhan_dub import llm
    sleeps = []
    monkeypatch.setattr(llm.time, "sleep", lambda s: sleeps.append(s))
    flaky = _Flaky(fail=2)
    brain = llm.Brain(client=flaky, model="m")
    assert brain._ask("s", "u") == '{"groups": [[0, 1]]}'
    assert flaky.n == 3 and sleeps == [3.0, 3.0]                 # waited the provider's Retry-After each time


def test_llm_gives_up_after_max_attempts_and_raises(monkeypatch):
    import pytest

    from bodhan_dub import llm
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    flaky = _Flaky(fail=99)
    brain = llm.Brain(client=flaky, model="m")
    brain.max_attempts = 3
    with pytest.raises(Exception, match="429"):
        brain._ask("s", "u")
    assert flaky.n == 3


def test_llm_drops_temperature_when_the_provider_rejects_it(monkeypatch):
    from bodhan_dub import llm
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    flaky = _Flaky(fail=0, reject_temperature=True)
    brain = llm.Brain(client=flaky, model="m")
    assert brain._ask("s", "u")
    assert "temperature" in flaky.kwargs[0] and "temperature" not in flaky.kwargs[1] and brain.use_temperature is False


def test_dead_llm_is_switched_off_after_a_few_failures_and_the_reason_is_recorded(monkeypatch):
    import pytest

    from bodhan_dub import llm
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    flaky = _Flaky(fail=999)
    brain = llm.Brain(client=flaky, model="m")
    brain.max_attempts, brain.breaker = 2, 2
    for _ in range(2):
        with pytest.raises(Exception, match="429"):
            brain._ask("s", "u")
    assert brain.tripped and not brain.available and brain.failed_calls == 2
    n = flaky.n
    with pytest.raises(RuntimeError, match="switched off"):
        brain._ask("s", "u")
    assert flaky.n == n                                     # no more calls, no more waiting
    assert "429" in brain.last_error


def test_llm_success_resets_the_failure_streak(monkeypatch):
    from bodhan_dub import llm
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    flaky = _Flaky(fail=0)
    brain = llm.Brain(client=flaky, model="m")
    brain.exhausted = 2
    assert brain._ask("s", "u") and brain.exhausted == 0
