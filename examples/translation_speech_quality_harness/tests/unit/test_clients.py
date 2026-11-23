"""Bodhan and LLM clients against the local stub: retries, splitting, caching, fallbacks (TESTING.md §4)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from bodhan_eval.budget import Budget
from bodhan_eval.cache import Cache
from bodhan_eval.clients import BodhanClient, BodhanError, LLMClient, LLMError, ServiceUnavailable, strip_indic
from bodhan_eval.config import Settings
from bodhan_eval.testing.stub_server import StubServer

PARA_1 = "The first paragraph is long enough to matter. " * 3
PARA_2 = "The second paragraph follows after a blank line. " * 3
FIXTURE = {
    "translate": {
        "hi": {"Water boils.": "पानी उबलता है।", "Cut short.": {"text": "छोटा", "truncated": True}},
        "en": {"पानी उबलता है।": "Water boils."},
    },
    "transliterate": {"hi": {"Newton": "न्यूटन"}},
    "chat": [
        {"contains": "verdict please", "content": '{"errors": [], "note": "fine"}'},
        {"contains": "prose please", "content": "I think it is fine."},
    ],
}


@pytest.fixture
def stub() -> Iterator[StubServer]:
    with StubServer(FIXTURE) as s:
        yield s


@pytest.fixture
def cache(tmp_path: Path) -> Iterator[Cache]:
    c = Cache(tmp_path / "cache")
    yield c
    c.close()


def settings_for(stub: StubServer, **kw) -> Settings:
    base = {
        "bodhan_base_url": stub.url,
        "translate_key": "stub-key",
        "transliterate_key": "stub-key",
        "speak_key": "stub-key",
        "transcribe_key": "stub-key",
        "llm_base_url": f"{stub.url}/v1",
        "llm_model": "stub-judge",
        "llm_api_key": "stub-key",
        "max_retries": 3,
        "request_timeout_s": 10.0,
    }
    return Settings(**{**base, **kw})


def bodhan(stub: StubServer, cache: Cache, budget: Budget | None = None, **kw) -> BodhanClient:
    return BodhanClient(settings_for(stub, **kw), cache, budget or Budget(None))


# ---- translation -------------------------------------------------------------


def test_translate_200_then_cache_hit(stub: StubServer, cache: Cache) -> None:
    budget = Budget(None)
    c = bodhan(stub, cache, budget)
    first = c.translate("Water boils.", "hi")
    second = c.translate("Water boils.", "hi")
    assert first == {"translation": "पानी उबलता है।", "truncated": False, "cached": False}
    assert second["cached"] is True
    assert len(stub.requests) == 1
    assert budget.total_spent > 0 and budget.used["translate"] == pytest.approx(2 * budget.spent["translate"])


def test_translate_records_truncated(stub: StubServer, cache: Cache) -> None:
    assert bodhan(stub, cache).translate("Cut short.", "hi")["truncated"] is True


def test_translate_413_splits_on_blank_lines_and_rejoins(stub: StubServer, cache: Cache) -> None:
    text = PARA_1 + "\n\n" + PARA_2
    stub.add_fault("/translate", 413, times=1, code="payload_too_large")
    out = bodhan(stub, cache).translate(text, "en")
    assert out["translation"] == text  # the stub echoes unknown text; the separator survives the rejoin
    assert [r["status"] for r in stub.requests] == [413, 200, 200]


def test_translate_429_retries_after_header(stub: StubServer, cache: Cache) -> None:
    stub.add_fault("/translate", 429, times=2, retry_after="0.01")
    assert bodhan(stub, cache).translate("Water boils.", "hi")["translation"] == "पानी उबलता है।"
    assert [r["status"] for r in stub.requests] == [429, 429, 200]


def test_translate_500_then_success(stub: StubServer, cache: Cache) -> None:
    stub.add_fault("/translate", 500, times=1, retry_after="0.01")
    assert bodhan(stub, cache).translate("Water boils.", "hi")["cached"] is False
    assert [r["status"] for r in stub.requests] == [500, 200]


def test_retries_exhausted_raises_and_releases_reservation(stub: StubServer, cache: Cache) -> None:
    stub.add_fault("/translate", 503, times=-1, retry_after="0.01")
    budget = Budget(1.0)
    with pytest.raises(BodhanError) as err:
        bodhan(stub, cache, budget, max_retries=2).translate("Water boils.", "hi")
    assert err.value.code == "retries_exhausted"
    assert budget.reserved == 0.0 and budget.total_spent == 0.0
    assert cache.get(Cache.key("translate", "indic-translate", {"target_language": "hi"}, "Water boils.")) is None


def test_transliterate(stub: StubServer, cache: Cache) -> None:
    assert bodhan(stub, cache).transliterate("Newton", "hi") == "न्यूटन"


def test_translate_uses_chat_transport_when_route_is_missing(stub: StubServer, cache: Cache) -> None:
    """The hosted gateway answered 404 on /translate (API_NOTES, 23 Nov); the chat transport serves the same model."""
    stub.add_fault("/translate", 404, times=-1, code="404", message="Not Found")
    c = bodhan(stub, cache)
    assert c.translate("Water boils.", "hi") == {"translation": "पानी उबलता है।", "truncated": False, "cached": False}
    assert c.translate("Cut short.", "hi")["truncated"] is True  # finish_reason "length"
    assert [r["path"] for r in stub.requests] == ["/translate", "/v1/chat/completions", "/v1/chat/completions"]
    assert stub.requests[1]["model"] == "indic-translate"


def test_transport_switch_is_logged_once_under_parallel_load(
    stub: StubServer, cache: Cache, caplog: pytest.LogCaptureFixture
) -> None:
    """Seen live: six workers met the 404 together and each logged the switch; one line is enough."""
    from concurrent.futures import ThreadPoolExecutor

    stub.add_fault("/translate", 404, times=-1)
    c = bodhan(stub, cache, concurrency=6)
    with caplog.at_level("WARNING", logger="bodhan_eval.clients"), ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda k: c.translate(f"sentence {k}", "hi"), range(12)))
    assert sum("answered 404" in rec.getMessage() for rec in caplog.records) == 1


def test_transliterate_uses_chat_transport_when_route_is_missing(stub: StubServer, cache: Cache) -> None:
    stub.add_fault("/transliterate", 404, times=-1)
    assert bodhan(stub, cache).transliterate("Newton", "hi") == "न्यूटन"
    assert stub.requests[-1]["model"] == "indic-transliterate"


def test_transliterate_sends_its_own_key(stub: StubServer, cache: Cache) -> None:
    """Bodhan keys are scoped per model (API_NOTES, 23 Nov): the translate key cannot transliterate."""
    c = bodhan(stub, cache, translate_key="translate-placeholder", transliterate_key="transliterate-placeholder")
    c.translate("Water boils.", "hi")
    c.transliterate("Newton", "hi")
    assert [(r["path"], r["bearer"]) for r in stub.requests] == [
        ("/translate", "translate-placeholder"),
        ("/transliterate", "transliterate-placeholder"),
    ]


def test_transliteration_stays_on_the_translate_cost_line(stub: StubServer, cache: Cache) -> None:
    budget = Budget(None)
    bodhan(stub, cache, budget).transliterate("Newton", "hi")
    assert set(budget.used) == {"translate"} and budget.calls == {"translate": 1}


def test_rejected_key_turns_the_service_off_for_the_run(stub: StubServer, cache: Cache) -> None:
    """A key the gateway refuses is not sent again: one failed request, then ServiceUnavailable without HTTP."""
    stub.add_fault("/transliterate", 401, times=-1, code="invalid_api_key_error")
    c = bodhan(stub, cache)
    with pytest.raises(BodhanError) as err:
        c.transliterate("Newton", "hi")
    assert err.value.status == 401
    assert c.available("transliterate") is False and c.available("translate") is True
    with pytest.raises(ServiceUnavailable, match="BODHAN_TRANSLITERATE_KEY"):
        c.transliterate("Einstein", "hi")
    assert [r["path"] for r in stub.requests] == ["/transliterate"]


def test_other_client_errors_do_not_switch_transport(stub: StubServer, cache: Cache) -> None:
    stub.add_fault("/translate", 400, times=1, code="unsupported_language")
    with pytest.raises(BodhanError) as err:
        bodhan(stub, cache).translate("Water boils.", "xx")
    assert err.value.status == 400
    assert [r["path"] for r in stub.requests] == ["/translate"]


def test_missing_key_is_service_unavailable_before_any_request(stub: StubServer, cache: Cache) -> None:
    budget = Budget(1.0)
    c = bodhan(stub, cache, budget, translate_key=None)
    assert c.available("translate") is False
    with pytest.raises(ServiceUnavailable, match="BODHAN_TRANSLATE_KEY"):
        c.translate("Water boils.", "hi")
    assert stub.requests == [] and budget.reserved == 0.0


# ---- speech ------------------------------------------------------------------


def test_tts_wav_returned_and_cached_as_blob(stub: StubServer, cache: Cache) -> None:
    c = bodhan(stub, cache)
    wav = c.speak("पानी उबलता है।", "hi", "Kavya")
    assert wav[:4] == b"RIFF"
    assert c.speak("पानी उबलता है।", "hi", "Kavya") == wav
    assert len(stub.requests) == 1
    assert len(list((cache.root / "blobs").glob("*.wav"))) == 1


def test_tts_422_is_not_retried(stub: StubServer, cache: Cache) -> None:
    with pytest.raises(BodhanError) as err:
        bodhan(stub, cache).speak("   ", "hi", "Kavya")
    assert err.value.status == 422
    assert len(stub.requests) == 1


# ---- LLM (K1) ------------------------------------------------------------------

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["errors"],
    "properties": {"errors": {"type": "array", "items": {"type": "object"}}},
}
VERDICT = [{"role": "user", "content": "verdict please"}]


def llm(stub: StubServer, cache: Cache, budget: Budget | None = None, price: float = 0.0, **kw) -> LLMClient:
    extra_body = kw.pop("extra_body", None)
    return LLMClient(settings_for(stub, max_retries=1, **kw), cache, budget or Budget(None), price, extra_body)


def formats(stub: StubServer) -> list[tuple[int, str | None]]:
    return [(r["status"], r.get("response_format")) for r in stub.requests]


def test_llm_answer_parsed_and_cached(stub: StubServer, cache: Cache) -> None:
    client = llm(stub, cache)
    assert client.chat_json(VERDICT) == {"errors": [], "note": "fine"}
    assert client.chat_json(VERDICT) == {"errors": [], "note": "fine"}
    assert formats(stub) == [(200, "json_object")]  # no schema given: the chain starts at json_object


def test_llm_uses_strict_json_schema_when_given_one(stub: StubServer, cache: Cache) -> None:
    client = llm(stub, cache)
    assert client.chat_json(VERDICT, schema=SCHEMA) == {"errors": [], "note": "fine"}
    assert formats(stub) == [(200, "json_schema")]
    sent = stub.requests[0]["json_schema"]
    assert sent["strict"] is True and sent["schema"] == SCHEMA
    assert client.format_for("stub-judge") == "json_schema"


def test_llm_cached_answer_still_reports_its_format(stub: StubServer, cache: Cache) -> None:
    llm(stub, cache).chat_json(VERDICT, schema=SCHEMA)
    rerun = llm(stub, cache)  # a new run on the same cache
    assert rerun.chat_json(VERDICT, schema=SCHEMA) == {"errors": [], "note": "fine"}
    assert len(stub.requests) == 1 and rerun.format_for("stub-judge") == "json_schema"


def test_llm_json_schema_rejected_falls_back_to_json_object_and_remembers(start_stub, cache: Cache) -> None:
    stub = start_stub({**FIXTURE, "chat_unsupported_formats": {"stub-judge": ["json_schema"]}})
    client = llm(stub, cache)
    assert client.chat_json(VERDICT, schema=SCHEMA) == {"errors": [], "note": "fine"}
    assert client.chat_json([{"role": "user", "content": "verdict please, again"}], schema=SCHEMA) is not None
    assert formats(stub) == [(400, "json_schema"), (200, "json_object"), (200, "json_object")]
    assert client.format_for("stub-judge") == "json_object"


def test_llm_both_formats_rejected_falls_back_to_plain(start_stub, cache: Cache) -> None:
    stub = start_stub({**FIXTURE, "chat_unsupported_formats": {"*": ["json_schema", "json_object"]}})
    client = llm(stub, cache)
    assert client.chat_json(VERDICT, schema=SCHEMA) == {"errors": [], "note": "fine"}
    assert formats(stub) == [(400, "json_schema"), (400, "json_object"), (200, None)]
    assert client.format_for("stub-judge") == "plain"


def test_llm_format_memory_is_per_model(start_stub, cache: Cache) -> None:
    stub = start_stub({**FIXTURE, "chat_unsupported_formats": {"weak-model": ["json_schema"]}})
    client = llm(stub, cache)
    client.chat_json(VERDICT, schema=SCHEMA, model="weak-model")
    client.chat_json(VERDICT, schema=SCHEMA, model="strong-model")
    assert [(r["model"], r["status"], r["response_format"]) for r in stub.requests] == [
        ("weak-model", 400, "json_schema"),
        ("weak-model", 200, "json_object"),
        ("strong-model", 200, "json_schema"),
    ]
    assert client.format_for("weak-model") == "json_object" and client.format_for("strong-model") == "json_schema"


def test_llm_error_on_every_format_raises_and_lowers_nothing(stub: StubServer, cache: Cache) -> None:
    """A 400 that is not about the format (it fails at every level) must not leave the model on plain prompting."""
    stub.add_fault("/chat/completions", 400, times=3, message="prompt too long")
    client = llm(stub, cache)
    with pytest.raises(LLMError) as err:
        client.chat_json(VERDICT, schema=SCHEMA)
    assert err.value.status == 400
    assert client.chat_json(VERDICT, schema=SCHEMA) is not None
    assert formats(stub)[-1] == (200, "json_schema")


def test_llm_auth_error_is_not_a_format_problem(stub: StubServer, cache: Cache) -> None:
    stub.add_fault("/chat/completions", 401, times=1, message="bad key")
    with pytest.raises(LLMError) as err:
        llm(stub, cache).chat_json(VERDICT, schema=SCHEMA)
    assert err.value.status == 401
    assert len(stub.requests) == 1


def test_llm_empty_content_retried_once_with_double_max_tokens(start_stub, cache: Cache) -> None:
    """A reasoning model that spends all of max_tokens thinking returns no content: one retry with twice the room."""
    stub = start_stub(
        {
            "chat": [
                {"contains": "think hard", "min_max_tokens": 3000, "content": '{"errors": []}'},
                {"contains": "think hard", "content": "", "finish_reason": "length"},
            ]
        }
    )
    budget = Budget(None)
    client = llm(stub, cache, budget, price=0.1)
    assert client.chat_json([{"role": "user", "content": "think hard"}], max_tokens=1500) == {"errors": []}
    assert [r["max_tokens"] for r in stub.requests] == [1500, 3000]
    assert budget.calls == {"llm": 2}  # two billed requests, each counted once
    assert budget.llm_tokens["prompt"] > 0 and budget.reserved == 0.0


def test_llm_empty_content_twice_gives_none(start_stub, cache: Cache) -> None:
    stub = start_stub({"chat": [{"contains": "think", "content": "", "finish_reason": "length"}]})
    assert llm(stub, cache).chat_json([{"role": "user", "content": "think"}]) is None
    assert len(stub.requests) == 2


def test_llm_extra_body_is_merged_into_the_request(stub: StubServer, cache: Cache) -> None:
    client = llm(stub, cache, extra_body={"reasoning": {"effort": "low"}})
    client.chat_json(VERDICT)
    assert stub.requests[0]["extra"] == {"reasoning": {"effort": "low"}}


def test_llm_extra_body_is_off_by_default(stub: StubServer, cache: Cache) -> None:
    llm(stub, cache).chat_json(VERDICT)
    assert stub.requests[0]["extra"] == {}


def test_llm_provider_reported_cost_is_recorded(start_stub, cache: Cache) -> None:
    stub = start_stub({"chat": [{"contains": "priced", "content": '{"errors": []}', "cost": 0.0021}]})
    budget = Budget(None)
    client = llm(stub, cache, budget)
    client.chat_json([{"role": "user", "content": "priced"}])
    client.chat_json([{"role": "user", "content": "priced"}])  # cache hit: nothing paid
    assert budget.llm_cost_usd == pytest.approx(0.0021)


def test_llm_model_can_be_chosen_per_call(stub: StubServer, cache: Cache) -> None:
    client = llm(stub, cache)
    client.chat_json(VERDICT, model="juror-b")
    client.chat_json(VERDICT, model="juror-b")  # cached per model
    client.chat_json(VERDICT)
    assert [r["model"] for r in stub.requests] == ["juror-b", "stub-judge"]


def test_llm_cache_tag_changes_only_the_cache_key(stub: StubServer, cache: Cache) -> None:
    """Repeat-consistency (K2c) re-sends the identical prompt; the tag lives in the cache key, not the request."""
    client = llm(stub, cache)
    client.chat_json(VERDICT)
    client.chat_json(VERDICT, cache_tag="repeat")
    assert len(stub.requests) == 2
    assert stub.requests[0]["body_hash"] == stub.requests[1]["body_hash"]


def test_llm_non_json_answer_is_none(stub: StubServer, cache: Cache) -> None:
    assert llm(stub, cache).chat_json([{"role": "user", "content": "prose please"}]) is None


def test_llm_unconfigured_is_service_unavailable(stub: StubServer, cache: Cache) -> None:
    client = llm(stub, cache, llm_model=None)
    assert client.available() is False
    with pytest.raises(ServiceUnavailable):
        client.chat_json([{"role": "user", "content": "x"}])


def test_strip_indic_masks_indian_scripts_in_llm_prose() -> None:
    assert strip_indic("The word पानी is fine, as is தண்ணீர்.") == "The word […] is fine, as is […]."
