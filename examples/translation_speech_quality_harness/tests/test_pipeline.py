"""End-to-end pipeline run with fake Bodhan + fake judge (no network)."""

import json
from dataclasses import dataclass

import numpy as np

from bodhan_eval import pipeline
from bodhan_eval.checks.speech import to_wav
from bodhan_eval.cli import build_parser
from bodhan_eval.cost import BudgetExceeded

SR = 24000
CLEAN_SRC = (
    "Water boils at 100 °C at sea level. At higher altitudes it boils at a lower temperature."
)
CLEAN_TR = "समुद्र तल पर पानी 100 °C पर उबलता है। अधिक ऊँचाई पर यह कम तापमान पर उबलता है।"
NUM_SRC = "The Eiffel Tower is 330 metres tall and was completed in 1889."
NUM_TR = "एफिल टॉवर 330 मीटर ऊँचा है और इसे 1898 में पूरा किया गया था।"
SPEECH_TR = (
    "भारत एक विशाल देश है जहाँ अनेक भाषाएँ बोली जाती हैं। यहाँ की संस्कृति बहुत पुरानी और समृद्ध मानी जाती है।"
)
SPEECH_SRC = (
    "India is a vast country where many languages are spoken. "
    "Its culture is considered very old and rich."
)

BT = {
    "समुद्र तल पर पानी 100 °C पर उबलता है।": "Water boils at 100 °C at sea level.",
    "अधिक ऊँचाई पर यह कम तापमान पर उबलता है।": "At higher altitudes it boils at a lower temperature.",
    NUM_TR: "The Eiffel Tower is 330 metres tall and was completed in 1898.",
    "भारत एक विशाल देश है जहाँ अनेक भाषाएँ बोली जाती हैं।": (
        "India is a vast country where many languages are spoken."
    ),
    "यहाँ की संस्कृति बहुत पुरानी और समृद्ध मानी जाती है।": "Its culture is considered very old and rich.",
}


@dataclass
class _R:
    text: str = ""
    truncated: bool = False
    cached: bool = False


class FakeBodhan:
    def __init__(self, cache, cost, **kw):
        self.cost = cost

    async def translate(self, text, target_language, script="native"):
        if text.startswith("BUDGET"):
            raise BudgetExceeded("over budget")
        if "\n" in text:  # batched back-translation: one sentence per line
            return _R("\n".join(BT[t] for t in text.split("\n")))
        return _R(BT[text])

    async def transcribe(self, wav, language):
        return _R("भारत एक विशाल देश है जहाँ अनेक भाषाएँ बोली जाती हैं")  # second sentence cut off

    async def aclose(self):
        pass


class FakeLLM:
    configured = True

    def __init__(self, *a, **kw):
        self.calls = 0

    async def chat_json(self, messages, *, sample=0, max_tokens=2000, tag="judge", schema=None):
        self.calls += 1
        user = messages[1]["content"]
        if "1898" in user:
            i = next(p for p in user.split() if p.endswith("1898"))
            idx = int(i[1 : i.index("]")])
            err = {
                "category": "number",
                "severity": "major",
                "token_start": idx,
                "token_end": idx,
                "source_quote": "1889",
                "explanation": "year changed",
            }
            return {"errors": [err], "verdict": "major"}, False
        return {"errors": [], "verdict": "clean"}, False


def _run(tmp_path, monkeypatch, extra=()):
    tmp_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(pipeline, "BodhanClient", FakeBodhan)
    monkeypatch.setattr(pipeline, "make_llm_client", FakeLLM)
    x = 0.2 * np.sin(2 * np.pi * 180 * np.arange(SR * 4) / SR).astype(np.float32)
    (tmp_path / "s3.wav").write_bytes(to_wav(x, SR))
    rows = [
        {"id": "clean", "lang": "hi", "source": CLEAN_SRC, "translation": CLEAN_TR},
        {"id": "num", "lang": "hi", "source": NUM_SRC, "translation": NUM_TR},
        {
            "id": "speech",
            "lang": "hi",
            "source": SPEECH_SRC,
            "translation": SPEECH_TR,
            "audio": "s3.wav",
        },
        {"id": "budget", "lang": "hi", "source": "BUDGET test.", "translation": "BUDGET परीक्षण।"},
        {"id": "ta-filtered", "lang": "ta", "source": "x", "translation": "y"},
        {"lang": "hi", "source": "no id"},
    ]
    ds = tmp_path / "demo.jsonl"
    ds.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows))
    out = tmp_path / "out"
    args = build_parser().parse_args(
        [
            "--dataset",
            str(ds),
            "--langs",
            "hi",
            "--out",
            str(out),
            "--cache-dir",
            str(tmp_path / "cache"),
            "--log-level",
            "ERROR",
            *extra,
        ]
    )
    assert pipeline.run(args) == 0
    items = {r["id"]: r for r in map(json.loads, (out / "items.jsonl").read_text().splitlines())}
    return items, json.loads((out / "summary.json").read_text()), out


def test_pipeline_end_to_end(tmp_path, monkeypatch):
    items, summary, out = _run(tmp_path, monkeypatch, ["--judge-repeats", "2"])
    assert set(items) == {"clean", "num", "speech", "budget"}

    assert items["clean"]["verdict"] == "clean" and items["clean"]["flags"] == []

    num = items["num"]
    assert [f["category"] for f in num["flags"]] == ["number"]
    f = num["flags"][0]
    assert NUM_TR[f["span"][0] : f["span"][1]] == "1898"
    assert {"numbers", "judge"} <= set(f["details"]["checks"])  # agreeing evidence merged
    assert f["confidence"] > 0.9

    sp = items["speech"]
    cats = [f["category"] for f in sp["flags"]]
    assert "speech_omission" in cats
    trunc = next(f for f in sp["flags"] if f["category"] == "speech_omission")
    assert SPEECH_TR[trunc["span"][0] : trunc["span"][1]].startswith("यहाँ")

    assert items["budget"]["skipped"]["backtranslation"] == "budget exhausted"
    assert any(c["check"] == "script" for c in items["budget"]["checks"])  # free checks still ran

    assert summary["items"] == 4 and summary["skipped_items"] == 1
    assert summary["filtered_out_by_lang"] == 1
    assert summary["flags_by_category"]["number"] == 1
    assert (
        summary["judge_consistency"]["ran"]
        and summary["judge_consistency"]["verdict_agreement"] == 1.0
    )
    assert (out / "logs" / "run.jsonl").exists() and (out / "judge_consistency.json").exists()


def test_pipeline_no_judge_and_no_speech_sample(tmp_path, monkeypatch):
    items, summary, _ = _run(tmp_path, monkeypatch, ["--no-judge", "--speech-sample", "0.01"])
    assert all(r["skipped"].get("judge") == "disabled (--no-judge)" for r in items.values())
    assert not summary["judge_consistency"]["ran"]
    # the number error is still found by the rule-based check alone
    assert [f["category"] for f in items["num"]["flags"]] == ["number"]


def test_pipeline_calibrates_and_reuses_thresholds(tmp_path, monkeypatch):
    s = NUM_TR.index("1898")
    labels = tmp_path / "human.jsonl"
    labels.write_text(
        json.dumps({"id": "num", "labels": [{"type": "number", "span": [s, s + 4]}]})
        + "\n"
        + json.dumps({"id": "clean", "labels": []})
        + "\n"
    )
    items, summary, out = _run(tmp_path, monkeypatch, ["--calibration", str(labels)])
    cal = json.loads((out / "calibration.json").read_text())
    assert cal["mode"] == "calibrate" and cal["items"]["by_scope"] == {"complete": 2}
    assert cal["metrics"]["per_category"]["number"]["tp"] == 1
    assert summary["calibration"]["metrics"]["clean_flag_rate"] == 0.0

    items2, summary2, out2 = _run(
        tmp_path,
        monkeypatch,
        ["--calibration", str(labels), "--thresholds", str(out / "calibration.json")],
    )
    cal2 = json.loads((out2 / "calibration.json").read_text())
    assert cal2["mode"].startswith("evaluate") and cal2["in_sample"] is False
    assert cal2["thresholds"] == cal["thresholds"]
