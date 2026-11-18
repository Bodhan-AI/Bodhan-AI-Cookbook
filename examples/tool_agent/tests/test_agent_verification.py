from __future__ import annotations

from types import SimpleNamespace

from bodhan_agent.__main__ import _parse_verifier_json, _verify_translation_with_llm


class FakeCompletions:
    def __init__(self, contents):
        self.contents = iter(contents)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        content = next(self.contents)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _client(contents):
    completions = FakeCompletions(contents)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


def test_translation_verifier_combines_multiple_full_batches():
    client, completions = _client([
        '{"status":"full","reason":"All first source details are present."}',
        '{"status":"full","reason":"All second source details are present."}',
    ])
    pairs = [
        {"source": "a" * 3500, "translation": "b" * 3500},
        {"source": "c" * 3500, "translation": "d" * 3500},
    ]

    result = _verify_translation_with_llm(client, "test-model", pairs)

    assert result["status"] == "full"
    assert result["passed"] is True
    assert len(result["batches"]) == 2
    assert len(completions.calls) == 2


def test_translation_verifier_rejects_empty_or_over_limit_pairs_without_call():
    client, completions = _client([])
    empty = _verify_translation_with_llm(client, "test-model", [])
    oversized = _verify_translation_with_llm(
        client,
        "test-model",
        [{"source": "a" * 12001, "translation": "x"}],
    )
    assert empty["status"] == "uncertain" and not empty["passed"]
    assert oversized["status"] == "uncertain" and not oversized["passed"]
    assert completions.calls == []


def test_translation_verifier_rejects_target_script_or_invalid_json():
    client, _ = _client(["नमस्ते", "not json"])
    target_script = _verify_translation_with_llm(client, "test-model", [{"source": "Hi", "translation": "नमस्ते"}])
    invalid_json = _verify_translation_with_llm(client, "test-model", [{"source": "Hi", "translation": "नमस्ते"}])
    assert target_script["status"] == "uncertain" and not target_script["passed"]
    assert invalid_json["status"] == "uncertain" and not invalid_json["passed"]


def test_verifier_json_parser_accepts_preamble_and_trailing_text():
    result = _parse_verifier_json(
        'Verdict: {"status":"full","reason":"All details are covered."} Extra note.'
    )
    assert result == {"status": "full", "reason": "All details are covered."}


def test_translation_verifier_retries_transient_connection_errors(monkeypatch):
    class FlakyCompletions:
        calls = 0

        def create(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise ConnectionError("temporary disconnect")
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(
                    content='{"status":"full","reason":"All source details are covered."}'
                ))]
            )

    monkeypatch.setattr("bodhan_agent.__main__.time.sleep", lambda delay: None)
    completions = FlakyCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    result = _verify_translation_with_llm(
        client,
        "test-model",
        [{"source": "Clinic opens at nine.", "translation": "Clinic opens at nine."}],
    )

    assert result["status"] == "full"
    assert result["passed"] is True
    assert completions.calls == 2
