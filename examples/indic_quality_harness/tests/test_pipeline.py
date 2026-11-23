"""End-to-end pipeline tests with fake Bodhan and Ollama clients (offline)."""

import json
import math
import struct
import wave

import pytest
from bodhan_eval.__main__ import main
from bodhan_eval.cache import JsonCache
from bodhan_eval.cost import BudgetExceededError, CostRates, CostTracker
from bodhan_eval.llm_judge import Finding, JudgeOutcome, JudgeUnavailableError
from bodhan_eval.pipeline import EvalConfig, run_evaluation, select_speech_records
from bodhan_eval.translation_service import CachedTranslator

RECORDS = [
    {"id": "hi-001", "language": "hi", "source_text": "The price is 500 rupees.",
     "translation": "कीमत 500 रुपये है।"},
    {"id": "hi-002", "language": "hi", "source_text": "The bus leaves every 30 minutes.",
     "translation": "बस हर 40 मिनट में निकलती है।"},
    {"id": "ta-001", "language": "ta", "source_text": "The school has 24 classrooms."},
    {"id": "bn-001", "language": "bn", "source_text": "The train takes 6 hours.",
     "translation": "ট্রেনে ৬ ঘণ্টা লাগে।"},
]

FORWARD = {("The school has 24 classrooms.", "ta"): "பள்ளியில் 24 வகுப்பறைகள் உள்ளன."}
BACKWARD = {
    "कीमत 500 रुपये है।": "The price is 500 rupees.",
    "बस हर 40 मिनट में निकलती है।": "The bus leaves every 40 minutes.",
    "பள்ளியில் 24 வகுப்பறைகள் உள்ளன.": "The school has 24 classrooms.",
    "ট্রেনে ৬ ঘণ্টা লাগে।": "The train takes 6 hours.",
}


class FakeTranslateClient:
    def __init__(self):
        self.calls = []

    def translate(self, text, target_language, script="native"):
        self.calls.append((text, target_language))
        output = BACKWARD[text] if target_language == "en" else FORWARD[(text, target_language)]
        return {"translation": output, "truncated": False, "usage": {"completion_tokens": 10}}


class FakeSpeechClient:
    def __init__(self, transcripts=None):
        self.transcripts = transcripts or {}
        self.speak_calls = []

    def speak(self, text, language, output_path, voice="Amit"):
        self.speak_calls.append((text, language, voice))
        rate = 24000
        with wave.open(output_path, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(rate)
            wav.writeframes(b"".join(
                struct.pack("<h", int(8000 * math.sin(2 * math.pi * 200 * i / rate)))
                for i in range(rate * 2)
            ))

    def transcribe(self, audio_path, language):
        text = [c for c in self.speak_calls if c[1] == language][-1][0]
        return self.transcripts.get(text, text)


class FakeJudge:
    def __init__(self, available=True):
        self.available = available
        self.calls = 0

    def check_available(self):
        if not self.available:
            raise JudgeUnavailableError("Ollama is not reachable")

    def judge(self, source, translation, language):
        self.calls += 1
        findings = []
        if "40" in translation:
            findings.append(Finding(evidence_source="translation", category="number_unit", severity="high",
                                    span="40", explanation="30 became 40", confidence=0.9))
        return JudgeOutcome(status="ok", findings=findings)


@pytest.fixture
def dataset(tmp_path):
    path = tmp_path / "data.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in RECORDS), encoding="utf-8")
    return path


def config(dataset, tmp_path, **kwargs):
    kwargs.setdefault("request_interval", 0)
    return EvalConfig(dataset=str(dataset), languages=["hi", "ta", "bn"], out=str(tmp_path / "out"), **kwargs)


def items_by_id(result):
    return {item["id"]: item for item in result["report"]["items"]}


def test_offline_run_needs_no_clients(dataset, tmp_path):
    result = run_evaluation(config(dataset, tmp_path))
    items = items_by_id(result)

    assert items["ta-001"]["status"] == "not_evaluated"
    assert items["hi-002"]["checks"][0]["status"] == "fail"
    assert (tmp_path / "out" / "summary.md").exists()
    assert result["report"]["run"]["cost"]["estimated_spend_inr"] == 0


def test_generate_fills_missing_translation_only(dataset, tmp_path):
    client = FakeTranslateClient()
    result = run_evaluation(config(dataset, tmp_path, generate=True), translate_client=client)
    items = items_by_id(result)

    assert client.calls == [("The school has 24 classrooms.", "ta")]
    assert items["ta-001"]["translation_source"] == "bodhan_generated"
    assert items["hi-001"]["translation_source"] == "dataset"
    assert "reference_chrf" not in {c["category"] for c in items["ta-001"]["checks"]}


def test_back_translation_uses_source_and_caches(dataset, tmp_path):
    client = FakeTranslateClient()
    run_evaluation(config(dataset, tmp_path, back_translate=True), translate_client=client)
    first_calls = len(client.calls)
    result = run_evaluation(config(dataset, tmp_path, back_translate=True), translate_client=client)

    assert len(client.calls) == first_calls  # second run fully cached
    items = items_by_id(result)
    rt = {c["category"]: c for c in items["hi-002"]["round_trip"]["checks"]}
    assert rt["round_trip_number_mismatch"]["status"] == "fail"
    assert items["hi-001"]["round_trip"]["cached"] is True
    assert result["report"]["run"]["back_translation"]["items"] == 3


def test_speech_limit_restricts_only_speech(dataset, tmp_path):
    speech = FakeSpeechClient()
    result = run_evaluation(config(dataset, tmp_path, speech=True, speech_limit=2), speech_client=speech)
    items = items_by_id(result)

    assert result["report"]["total_items"] == 4  # translation checks still run on all
    assert sum("speech" in item for item in items.values()) == 2
    assert {v for _, _, v in speech.speak_calls} <= {"Amit", "Sourav", "Arun"}


def test_speech_cache_reused_and_truncation_detected(dataset, tmp_path):
    speech = FakeSpeechClient(transcripts={"कीमत 500 रुपये है।": "कीमत"})
    cfg = config(dataset, tmp_path, speech=True)
    run_evaluation(cfg, speech_client=speech)
    calls = len(speech.speak_calls)
    result = run_evaluation(cfg, speech_client=speech)

    assert len(speech.speak_calls) == calls
    checks = {c["category"]: c["status"] for c in items_by_id(result)["hi-001"]["checks"]}
    assert checks["speech_truncation"] == "fail"
    assert checks["speech_missing_content"] == "fail"
    assert checks["audio_duration"] == "pass"
    assert result["report"]["run"]["speech"]["cache_hits"] == 3


def test_select_speech_round_robin():
    records = [{"id": f"{lang}-{i}", "language": lang, "translation": "x"} for lang in ("hi", "ta") for i in range(3)]
    assert select_speech_records(records, 3) == {"hi-0", "ta-0", "hi-1"}
    assert len(select_speech_records(records, None)) == 6


def test_llm_judge_findings_are_review_only(dataset, tmp_path):
    judge = FakeJudge()
    result = run_evaluation(config(dataset, tmp_path, llm_judge=True), judge=judge)
    items = items_by_id(result)

    llm = [c for c in items["hi-002"]["checks"] if c["category"].startswith("llm_")]
    assert llm and llm[0]["status"] == "review"
    assert result["report"]["errors_by_category"].get("llm_number_unit") is None
    assert result["report"]["run"]["llm_judge"]["findings_by_category"] == {"number_unit": 1}
    assert judge.calls == 3  # the record without a translation is skipped


def test_llm_judge_unavailable_does_not_break_run(dataset, tmp_path):
    judge = FakeJudge(available=False)
    result = run_evaluation(config(dataset, tmp_path, llm_judge=True), judge=judge)

    assert judge.calls == 0
    assert result["report"]["run"]["llm_judge"]["availability"] == "unavailable"
    assert result["report"]["total_items"] == 4


def test_llm_judge_not_constructed_without_flag(dataset, tmp_path, monkeypatch):
    import bodhan_eval.llm_judge as module

    def boom(*args, **kwargs):
        raise AssertionError("OllamaJudge must not be created without --llm-judge")

    monkeypatch.setattr(module, "OllamaJudge", boom)
    run_evaluation(config(dataset, tmp_path))


def test_budget_blocks_paid_calls_but_run_completes(dataset, tmp_path):
    client = FakeTranslateClient()
    result = run_evaluation(config(dataset, tmp_path, back_translate=True, max_cost_inr=0.0),
                            translate_client=client)

    assert client.calls == []
    cost = result["report"]["run"]["cost"]
    assert cost["budget_exhausted"] is True and cost["blocked_calls"] == 3
    assert result["report"]["run"]["errors"] == {
        "back_translate:budget": 3,
        "dataset:missing_translation": 1,  # ta-001: --generate was not passed
    }


def test_budget_tracker_unit():
    tracker = CostTracker(max_inr=1.0, rates=CostRates(translate_inr_per_1k_output_tokens=100))
    tracker.reserve("t", 0.5)
    tracker.record("t", 0.9)
    with pytest.raises(BudgetExceededError):
        tracker.reserve("t", 0.2)
    assert tracker.summary()["blocked_calls"] == 1


def test_translator_cache_key_and_truncation(tmp_path):
    class Truncating:
        calls = 0

        def translate(self, text, target, script="native"):
            Truncating.calls += 1
            return {"translation": "…", "truncated": True, "usage": {}}

    translator = CachedTranslator(Truncating(), JsonCache(tmp_path / "t.json"))
    translator.translate("hello", "hi")
    translator.translate("hello", "hi")
    assert Truncating.calls == 2  # truncated output is never cached
    assert CachedTranslator.cache_key("a", "hi") != CachedTranslator.cache_key("a", "ta")


def test_corrupt_cache_is_moved_aside(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{not json", encoding="utf-8")
    cache = JsonCache(path)
    assert cache.data == {}
    assert (tmp_path / "c.json.corrupt").exists()


def test_baseline_comparison(dataset, tmp_path):
    first = run_evaluation(config(dataset, tmp_path))
    baseline_path = tmp_path / "baseline.json"
    report = first["report"]
    # Pretend hi-001 used to fail and hi-002 used to pass.
    for item in report["items"]:
        if item["id"] == "hi-001":
            item["checks"].append({"category": "number_mismatch", "status": "fail", "passed": False})
        if item["id"] == "hi-002":
            item["checks"] = [c for c in item["checks"] if c["category"] != "number_mismatch"]
    baseline_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")

    result = run_evaluation(config(dataset, tmp_path, baseline=str(baseline_path)))
    comparison = result["baseline"]

    assert comparison["regressions"] == [{"id": "hi-002", "category": "number_mismatch"}]
    assert comparison["fixes"] == [{"id": "hi-001", "category": "number_mismatch"}]
    assert (tmp_path / "out" / "baseline_comparison.json").exists()
    assert "## Baseline comparison" in (tmp_path / "out" / "summary.md").read_text(encoding="utf-8")


def test_cli_offline_with_calibration(dataset, tmp_path, capsys):
    calibration = tmp_path / "cal.jsonl"
    calibration.write_text(json.dumps({
        "id": "c1", "clean_translation": "कीमत 500 रुपये है", "mutated_text": "कीमत 999 रुपये है",
        "injected_category": "number_mismatch", "source_text": "The price is 500 rupees.", "language": "hi",
    }, ensure_ascii=False), encoding="utf-8")

    code = main(["--dataset", str(dataset), "--langs", "hi,ta,bn", "--out", str(tmp_path / "cli"),
                 "--calibration", str(calibration)])

    assert code == 0
    output = capsys.readouterr().out
    assert "Calibration recall: 1.0" in output
    summary = json.loads((tmp_path / "cli" / "summary.json").read_text(encoding="utf-8"))
    assert "BODHAN" not in json.dumps(summary["run"]["config"]).upper().replace("BODHAN_EVAL", "")


def test_cli_rejects_bad_arguments(dataset):
    with pytest.raises(SystemExit):
        main(["--dataset", str(dataset), "--speech-limit", "0"])
    assert main(["--dataset", "does-not-exist.jsonl"]) == 2


def test_rate_limit_backoff_then_circuit_breaker():
    import requests as rq
    from bodhan_eval.ratelimit import RateLimitedError, RateLimiter

    sleeps = []
    limiter = RateLimiter("t", backoff=(1, 2), sleep=sleeps.append)
    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise rq.HTTPError("429 Client Error: Too Many Requests")
        return "ok"

    assert limiter.call(flaky) == "ok"
    assert sleeps == [1, 2]

    def always_429():
        raise RuntimeError("Translation request failed: 429 Client Error")

    with pytest.raises(RateLimitedError):
        limiter.call(always_429)
    assert limiter.tripped

    calls = []
    with pytest.raises(RateLimitedError):
        limiter.call(lambda: calls.append(1))
    assert calls == []  # breaker open: no request sent


def test_non_rate_limit_errors_are_not_retried():
    from bodhan_eval.ratelimit import RateLimiter

    limiter = RateLimiter("t", backoff=(1,), sleep=lambda s: pytest.fail("should not sleep"))
    with pytest.raises(ValueError):
        limiter.call(lambda: (_ for _ in ()).throw(ValueError("bad input")))


def test_rate_limited_run_records_errors_and_finishes(dataset, tmp_path):
    import requests as rq

    class Limited(FakeTranslateClient):
        def translate(self, text, target_language, script="native"):
            self.calls.append(text)
            raise rq.HTTPError("429 Client Error: Too Many Requests")

    client = Limited()
    cfg = config(dataset, tmp_path, back_translate=True, request_interval=0)
    from bodhan_eval import pipeline
    original = pipeline.RateLimiter
    pipeline.RateLimiter = lambda name, min_interval=0: original(name, min_interval, backoff=(0,), sleep=lambda s: None)
    try:
        result = run_evaluation(cfg, translate_client=client)
    finally:
        pipeline.RateLimiter = original

    assert len(client.calls) == 2  # one call + one retry, then the breaker opens
    errors = result["report"]["run"]["errors"]
    assert errors["back_translate:RateLimitedError"] == 3
    assert result["report"]["run"]["rate_limits"]["translate"]["tripped"] is True
