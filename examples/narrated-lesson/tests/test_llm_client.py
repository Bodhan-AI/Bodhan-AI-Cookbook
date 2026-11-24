import json

import httpx
import pytest
from PIL import Image

from bodhan_lesson import config
from bodhan_lesson.clients import llm

SETTINGS = config.LlmSettings("https://llm.test/v1", "text-model", "secret-key", "vision-model")


def make_client(handler, tmp_path=None, sleeps=None):
    transport = httpx.MockTransport(handler)
    return llm.LlmClient(SETTINGS, http=httpx.Client(transport=transport), cache_dir=tmp_path,
                         sleep=(sleeps.append if sleeps is not None else lambda s: None))


def ok(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]})


def test_complete_returns_text_and_sends_auth_and_model():
    seen = {}

    def handler(request):
        seen["auth"], seen["body"], seen["url"] = request.headers["authorization"], json.loads(request.content), str(request.url)
        return ok("hello")

    assert make_client(handler).complete([llm.text_message("user", "hi")]) == "hello"
    assert seen["auth"] == "Bearer secret-key" and seen["body"]["model"] == "text-model"
    assert seen["url"] == "https://llm.test/v1/chat/completions"


def test_think_blocks_are_removed():
    assert make_client(lambda r: ok("<think>internal\nreasoning</think>\nAnswer")).complete([]) == "Answer"


def test_indian_language_output_is_rejected():
    for text in ("Namaste नमस्ते", "வணக்கம்", "ہیلو", "নমস্কার"):
        with pytest.raises(llm.IndicTextError):
            make_client(lambda r, t=text: ok(t)).complete([])


def test_find_indic_ignores_latin_and_math():
    assert llm.find_indic("angle A B C equals 90 degrees ∠ ° π") is None


def test_rate_limit_is_retried_using_retry_after():
    calls, sleeps = [], []

    def handler(request):
        calls.append(1)
        return httpx.Response(429, headers={"retry-after": "3"}) if len(calls) < 3 else ok("done")

    assert make_client(handler, sleeps=sleeps).complete([]) == "done"
    assert len(calls) == 3 and sleeps == [3.5, 3.5]


def test_client_errors_are_not_retried_and_never_leak_the_key():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(400, text="model does not support images")

    with pytest.raises(llm.LlmError) as info:
        make_client(handler).complete([])
    assert len(calls) == 1 and info.value.status == 400 and "secret-key" not in str(info.value)


def test_gives_up_after_max_attempts():
    with pytest.raises(llm.LlmError, match="unavailable"):
        make_client(lambda r: httpx.Response(503)).complete([])


def test_empty_content_is_an_error():
    with pytest.raises(llm.LlmError, match="no content"):
        make_client(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": None}, "finish_reason": "length"}]})).complete([])


def test_responses_are_cached_on_disk(tmp_path):
    calls = []

    def handler(request):
        calls.append(1)
        return ok("cached answer")

    client = make_client(handler, tmp_path)
    messages = [llm.text_message("user", "same question")]
    assert client.complete(messages) == client.complete(messages) == "cached answer"
    assert len(calls) == 1
    assert client.complete([llm.text_message("user", "different")]) == "cached answer" and len(calls) == 2


def test_extract_json_handles_fences_and_prose():
    assert llm.extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert llm.extract_json('Sure! Here it is: {"a": {"b": 2}} Hope it helps') == {"a": {"b": 2}}
    for bad in ("no json here", "{broken", "[1, 2]"):
        with pytest.raises(llm.LlmError):
            llm.extract_json(bad)


def test_images_are_only_allowed_for_figures(tmp_path):
    path = tmp_path / "crop.png"
    Image.new("RGB", (50, 50), "white").save(path)
    message = llm.image_message("describe", path, purpose="figure")
    assert message["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    for purpose in ("page", "ocr", ""):
        with pytest.raises(ValueError):
            llm.image_message("read this page", path, purpose=purpose)


def test_reasoning_effort_is_sent_only_for_the_main_model_and_only_when_set():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return ok("fine")

    with_effort = config.LlmSettings("https://llm.test/v1", "text-model", "k", "vision-model", "low")
    client = llm.LlmClient(with_effort, http=httpx.Client(transport=httpx.MockTransport(handler)))
    client.complete([], model="text-model")
    client.complete([], model="vision-model")
    make_client(handler).complete([])  # settings without an effort
    assert bodies[0]["reasoning_effort"] == "low"
    assert "reasoning_effort" not in bodies[1] and "reasoning_effort" not in bodies[2]


def test_running_out_of_tokens_raises_truncated_error():
    truncated = httpx.Response(200, json={"choices": [{"message": {"content": ""}, "finish_reason": "length"}]})
    with pytest.raises(llm.TruncatedError):
        make_client(lambda r: truncated).complete([])
