"""End to end on a 10-item dataset: full CLI, schemas, graceful bad items, deterministic cached rerun at INR 0, baseline diff."""

from __future__ import annotations

import json

import httpx
import pytest

from bodhan_eval import cli
from bodhan_eval.clients import http as http_mod
from bodhan_eval.models import ItemResult
from conftest import Router, json_body, speechlike, wav_bytes

CLEAN_HI = "पानी समुद्र तल पर 100 °C पर उबलता है।"
BACK = {
    CLEAN_HI: "Water boils at 100 °C at sea level.",
    "पानी समुद्र तल पर 150 °C पर उबलता है।": "Water boils at 150 °C at sea level.",
    "सूरज पूर्व में उगता है।": "The sun rises in the east.",
}


def rows(audio_path: str) -> list[str]:
    items = [
        {"id": "clean-1", "source": "Water boils at 100 °C at sea level.", "lang": "hi", "translation": CLEAN_HI, "audio": audio_path, "labels": []},
        {
            "id": "num-1",
            "source": "Water boils at 100 °C at sea level.",
            "lang": "hi",
            "translation": "पानी समुद्र तल पर 150 °C पर उबलता है।",
            "labels": [{"type": "number", "severity": "major", "span": [18, 21]}],
        },
        {
            "id": "script-1",
            "source": "The sun rises in the east.",
            "lang": "hi",
            "translation": "সূর্য পূর্ব দিকে ওঠে।",
            "labels": [{"type": "wrong_language", "severity": "critical", "span": [0, 21]}],
        },
        {
            "id": "table-1",
            "source": "| # | Item |\n|---|---|\n| 1 | Water |",
            "lang": "hi",
            "translation": "| # | वस्तु |\n| 1 | पानी |",
            "labels": [{"type": "format", "severity": "major"}],
        },
        {"id": "sun-1", "source": "The sun rises in the east.", "lang": "hi", "translation": "सूरज पूर्व में उगता है।", "labels": []},
        {"id": "gen-1", "source": "The sun rises in the east.", "lang": "ta"},
        {"id": "missing-audio", "source": "The sun rises in the east.", "lang": "hi", "translation": "सूरज पूर्व में उगता है।", "audio": "nope/missing.wav"},
        {"id": "other-lang", "source": "Skip me.", "lang": "fr", "translation": "Ignore-moi."},
    ]
    lines = [json.dumps(r, ensure_ascii=False) for r in items]
    lines.append("{this is not json")
    lines.append(json.dumps({"id": "no-source", "lang": "hi"}))
    return lines


@pytest.fixture
def mocked(monkeypatch):
    def translate(request):
        body = json_body(request)
        if body["target_language"] == "en":
            return httpx.Response(200, json={"translation": BACK.get(body["text"], "Something else entirely."), "usage": {"completion_tokens": 20}})
        return httpx.Response(200, json={"translation": "சூரியன் கிழக்கில் உதிக்கிறது.", "usage": {"completion_tokens": 30}})

    def transcribe(request):
        return httpx.Response(200, json={"text": "पानी समुद्र तल पर सौ डिग्री सेल्सियस पर उबलता है"})

    def chat(request):
        user = json_body(request)["messages"][-1]["content"]
        ids = [line.split('"id": "')[1].split('"')[0] for line in user.splitlines() if line.startswith('{"category"') or '"id": "C' in line]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": json.dumps({"candidates": [{"id": i, "verdict": "confirm", "reason": "seen"} for i in ids], "errors": []})},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {},
            },
        )

    router = Router({"/translate": translate, "/v1/audio/transcriptions": transcribe, "/chat/completions": chat})
    real_client = httpx.AsyncClient

    class MockClient(real_client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(router)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(cli.httpx, "AsyncClient", MockClient)

    async def instant(_):
        return None

    monkeypatch.setattr(http_mod.asyncio, "sleep", instant)
    for key, value in {
        "BODHAN_TRANSLATE_KEY": "t",
        "BODHAN_SPEAK_KEY": "",
        "BODHAN_TRANSCRIBE_KEY": "r",
        "BODHAN_API_KEY": "",
        "LLM_BASE_URL": "https://llm.test/v1",
        "LLM_MODEL": "judge-model",
        "LLM_API_KEY": "k",
    }.items():
        monkeypatch.setenv(key, value)
    return router


DETERMINISTIC = ["items.jsonl", "summary.json", "summary.md", "calibration.json", "dashboard.html", "review_queue.csv", "thresholds.json"]


def test_full_run_is_graceful_deterministic_and_diffable(tmp_path, mocked):
    audio = tmp_path / "data" / "clean-1.wav"
    audio.parent.mkdir()
    audio.write_bytes(wav_bytes(speechlike(3.0)))
    dataset = tmp_path / "data" / "ten.jsonl"
    dataset.write_text("\n".join(rows("clean-1.wav")) + "\n", encoding="utf-8")
    args = ["--dataset", str(dataset), "--langs", "hi,ta", "--generate", "--cache-dir", str(tmp_path / "cache"), "--judge-repeats", "1"]

    assert cli.main(args + ["--out", str(tmp_path / "r1")]) == 0
    first_calls = len(mocked.calls)
    assert first_calls > 0
    r1 = tmp_path / "r1"
    for name in DETERMINISTIC + ["run.json", "run.log.jsonl"]:
        assert (r1 / name).exists(), name

    results = [ItemResult.model_validate_json(line) for line in (r1 / "items.jsonl").read_text(encoding="utf-8").splitlines()]
    by_id = {r.id: r for r in results}
    assert set(by_id) == {"clean-1", "num-1", "script-1", "table-1", "sun-1", "gen-1", "missing-audio"}
    assert {f.category.value for f in by_id["num-1"].flags} == {"number"}
    assert by_id["script-1"].flags[0].category.value == "wrong_language"
    assert by_id["table-1"].flags[0].category.value == "format"
    assert by_id["gen-1"].translation and by_id["gen-1"].back_translation
    assert by_id["clean-1"].transcript and by_id["clean-1"].flags == []
    assert any("audio file not found" in w for w in by_id["missing-audio"].warnings)
    assert any(c.name == "speech_generation" and c.status == "skipped" for c in by_id["sun-1"].checks)

    summary = json.loads((r1 / "summary.json").read_text(encoding="utf-8"))
    assert summary["items"]["rows_rejected"] == 2 and summary["items"]["filtered_by_lang"] == 1
    assert summary["quality_targets"]["detection_recall"]["value"] == 1.0
    assert summary["cost"]["total_inr"] > 0

    assert cli.main(args + ["--out", str(tmp_path / "r2")]) == 0
    assert len(mocked.calls) == first_calls, "cached rerun must not call any model"
    for name in DETERMINISTIC:
        assert (r1 / name).read_bytes() == (tmp_path / "r2" / name).read_bytes(), name
    run2 = json.loads((tmp_path / "r2" / "run.json").read_text(encoding="utf-8"))
    assert run2["budget"]["spent_this_run_inr"] == 0.0

    assert cli.main(args + ["--out", str(tmp_path / "r3"), "--baseline", str(r1), "--fail-on-regression", "0"]) == 0
    d = json.loads((tmp_path / "r3" / "diff.json").read_text(encoding="utf-8"))
    assert d["headline"]["regressed"] == 0 and d["headline"]["compared"] == 7
    assert (tmp_path / "r3" / "diff.md").exists()


def test_missing_dataset_exits_with_input_error(tmp_path):
    assert cli.main(["--dataset", str(tmp_path / "nope.jsonl"), "--out", str(tmp_path / "o")]) == 2


@pytest.mark.parametrize("cap", ["nan", "inf", "-1"])
def test_invalid_cost_cap_rejected_before_loading_or_paid_work(cap):
    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args(["--dataset", "unused", "--out", "unused", "--max-cost-inr", cap])
    assert exc.value.code == 2
