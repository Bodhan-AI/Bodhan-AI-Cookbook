"""End-to-end on a 10-line dataset with fake Bodhan + LLM clients (no network, no cost).

Checks: malformed lines are skipped and counted, each seeded error is flagged and localized,
clean items stay clean, the report files exist, and a rerun from cache is byte-identical with
zero model calls.
"""
import asyncio
import json

import numpy as np

from bodhan_eval import audio as audiolib
from bodhan_eval.budget import Ledger
from bodhan_eval.cache import Cache
from bodhan_eval.clients import BodhanClient
from bodhan_eval.config import Settings
from bodhan_eval.dataset import load_dataset
from bodhan_eval.pipeline import Options, Run
from bodhan_eval.report import write_report

SRC = "Water boils at 100 °C at sea level."
GOOD = "समुद्र तल पर पानी 100 डिग्री सेल्सियस पर उबलता है।"
SPEECH_TR = "समुद्र तल पर पानी उबलता है। यह एक सामान्य प्रक्रिया है। हर कोई इसे जानता है।"
BACK = {
    GOOD: "Water boils at 100 degrees Celsius at sea level.",
    GOOD.replace("100", "10"): "Water boils at 10 degrees Celsius at sea level.",
    "साबुन से हाथ धोने से अधिकांश कीटाणु दूर हो जाते हैं।": "Washing hands with soap removes most germs.",
    "इसरो ने चंद्रयान-3 लॉन्च किया।": "ISRO launched Chandrayaan-3.",
}


class FakeBodhan(BodhanClient):
    """Real caching/ledger logic, fake network: overrides the HTTP-level methods only."""

    def __init__(self, settings, cache, ledger):
        super().__init__(settings, cache, ledger)
        self.calls = 0

    async def _translate_chat(self, text, target, script):
        self.calls += 1
        return {"text": BACK.get(text, "Some English text." if target == "en" else GOOD), "completion_tokens": 20, "truncated": False}

    async def _send(self, service, method, path, **kw):
        self.calls += 1

        class R:
            status_code = 200

            def json(self_inner):
                # fake ASR: drops the last two sentences (a truncation)
                return {"text": "समुद्र तल पर पानी उबलता है यह एक"}

        return R()


class FakeLLM:
    model = "fake-judge"

    def __init__(self):
        self.calls = 0

    async def chat(self, messages, max_tokens=1500, sample=0):
        self.calls += 1
        if "real_error" in messages[-1]["content"]:
            return '{"real_error": false, "confidence": 0.6, "reason": "Paraphrase."}'
        return '{"errors": []}'

    async def aclose(self):
        pass


def _voiced(seconds, sr=24000):
    t = np.arange(int(seconds * sr)) / sr
    x = sum(np.sin(2 * np.pi * 140 * k * t) / k for k in range(1, 8)) * (0.5 + 0.5 * np.sin(2 * np.pi * 3 * t) ** 2)
    return (0.3 * x / np.max(np.abs(x))).astype(np.float32)


def write_dataset(tmp):
    (tmp / "audio").mkdir()
    (tmp / "audio" / "s1.wav").write_bytes(audiolib.encode(_voiced(6.0), 24000))
    bad_num = GOOD.replace("100", "10")
    i = bad_num.index("10 ")
    lines = [
        {"id": "n1", "source": SRC, "lang": "hi", "translation": GOOD, "labels": []},
        {"id": "n2", "source": SRC, "lang": "hi", "translation": bad_num, "labels": [{"type": "number", "severity": "critical", "span": [i, i + 2]}]},
        {"id": "m1", "source": "| Part | Function |\n|---|---|\n| Root | Absorbs water |", "lang": "hi",
         "translation": "| भाग | कार्य |\n|---|---|\n| जड़  पानी सोखती है |", "labels": [{"type": "format", "span": [27, 47]}]},
        {"id": "w1", "source": SRC, "lang": "hi", "translation": SRC, "labels": [{"type": "wrong_language", "severity": "critical"}]},
        {"id": "c1", "source": "Washing your hands with soap removes most germs.", "lang": "hi", "translation": "साबुन से हाथ धोने से अधिकांश कीटाणु दूर हो जाते हैं।", "labels": []},
        "this line is not json",
        {"id": "x1", "lang": "hi", "translation": "कुछ"},
        {"id": "o1", "source": "No translation for this one.", "lang": "hi"},
        {"id": "d1", "source": "ISRO launched Chandrayaan-3.", "lang": "hi", "translation": "इसरो ने चंद्रयान-3 लॉन्च किया।", "labels": []},
        {"id": "s1", "source": "Water boils at sea level. It is a common process. Everyone knows it.", "lang": "hi", "translation": SPEECH_TR,
         "audio": "audio/s1.wav", "labels": [{"type": "truncation", "area": "speech", "span": [SPEECH_TR.index("सामान्य"), len(SPEECH_TR)]}]},
    ]
    p = tmp / "data.jsonl"
    p.write_text("\n".join(x if isinstance(x, str) else json.dumps(x, ensure_ascii=False) for x in lines) + "\n", encoding="utf-8")
    return p


def run_once(tmp, ds, out):
    settings = Settings(translate_key="k", speak_key="k", transcribe_key="k", cache_dir=tmp / "cache")
    opts = Options(dataset=ds, out=out, langs=["hi"], consistency_sample=3)
    r = Run(opts, settings)
    r.bodhan = FakeBodhan(settings, r.cache, r.ledger)
    llm = FakeLLM()
    r.llm = llm
    from bodhan_eval.judge import Judge
    r.judge = Judge(llm)
    result = asyncio.run(r.execute())
    items, _ = load_dataset(ds, ["hi"])
    write_report(out, result, {it.id: it for it in items})
    r.cache.close()
    return result, r.bodhan.calls


def test_end_to_end(tmp_path):
    ds = write_dataset(tmp_path)
    out = tmp_path / "report"
    result, calls = run_once(tmp_path, ds, out)
    for f in ("summary.md", "summary.json", "items.jsonl", "calibration.json", "review_queue.csv", "dashboard.html"):
        assert (out / f).exists(), f
    rows = {json.loads(line)["id"]: json.loads(line) for line in open(out / "items.jsonl", encoding="utf-8")}
    assert len(result["problems"]) == 2  # bad JSON + missing source
    assert rows["o1"]["status"] == "skipped"
    for clean in ("n1", "c1", "d1"):
        assert rows[clean]["status"] == "clean", rows[clean]["flags"]
    n2 = rows["n2"]["flags"]
    assert n2 and n2[0]["category"] == "number" and n2[0]["text"] == "10"
    assert any(f["category"] == "format" for f in rows["m1"]["flags"])
    assert any(f["category"] == "wrong_language" for f in rows["w1"]["flags"])
    s1 = rows["s1"]["flags"]
    assert any(f["category"] == "speech_omission" for f in s1)
    cal = json.loads((out / "calibration.json").read_text(encoding="utf-8"))
    assert cal["after_tuning_in_sample"]["detection_recall"] == 1.0
    assert cal["after_tuning_in_sample"]["false_alarm_rate"] == 0.0
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["malformed_lines"] == 2 and summary["status_counts"]["skipped"] == 1

    # rerun from cache: identical report, no model calls
    out2 = tmp_path / "report2"
    _, calls2 = run_once(tmp_path, ds, out2)
    assert calls2 == 0
    for f in ("items.jsonl", "summary.json", "summary.md", "calibration.json"):
        assert (out / f).read_bytes() == (out2 / f).read_bytes(), f


def test_budget_guard_stops_paid_calls(tmp_path):
    ds = write_dataset(tmp_path)
    settings = Settings(translate_key="k", speak_key="k", transcribe_key="k", cache_dir=tmp_path / "cache")
    opts = Options(dataset=ds, out=tmp_path / "r", langs=["hi"], max_cost_inr=0.0, no_llm=True, consistency_sample=0)
    r = Run(opts, settings)
    r.bodhan = FakeBodhan(settings, r.cache, r.ledger)
    result = asyncio.run(r.execute())
    assert result["run"]["paid_calls_stopped"]
    assert r.bodhan.calls == 0
    assert all(i["status"] in ("clean", "flagged", "skipped") for i in result["items"])


def test_llm_cache_only_mode_serves_hits_and_refuses_misses(tmp_path):
    import pytest

    from bodhan_eval.clients import LLMClient, LLMUnavailable

    s = Settings(llm_base_url="http://127.0.0.1:9/v1", llm_model="m", llm_api_key="k", cache_dir=tmp_path)
    cache, led = Cache(tmp_path), Ledger()
    llm = LLMClient(s, cache, led)
    msgs = [{"role": "user", "content": "hi"}]
    cache.set("llm", {"model": "m", "messages": msgs, "max_tokens": 10, "sample": 0, "extra": {}}, {"text": "cached", "prompt_tokens": 1, "completion_tokens": 1})
    llm.cache_only = True
    assert asyncio.run(llm.chat(msgs, max_tokens=10)) == "cached"
    with pytest.raises(LLMUnavailable):
        asyncio.run(llm.chat([{"role": "user", "content": "new"}], max_tokens=10))


def test_cache_key_is_exact_input(tmp_path):
    c = Cache(tmp_path)
    c.set("translate", {"text": "a", "target": "hi"}, {"text": "x"})
    assert c.get("translate", {"target": "hi", "text": "a"}) == {"text": "x"}  # key order irrelevant
    assert c.get("translate", {"text": "a ", "target": "hi"}) is None  # any input change misses
    led = Ledger(1.0)
    led.record("speak", 0.5, 10, "chars", cached=False)
    led.record("speak", 0.5, 10, "chars", cached=True)
    assert led.spent_total == 0.5 and led.summary()["attributed_total_inr"] == 1.0


def test_redaction_of_provider_identifiers():
    from bodhan_eval.redact import redact

    msg = "429 in organization `org_00fakefakefake0000` key gsk_abcdefghijklmnop12 Bearer abcdefghijklmnopqrst"
    out = redact(msg)
    assert "org_00fake" not in out and "gsk_abc" not in out and "abcdefghijklmnopqrst" not in out
