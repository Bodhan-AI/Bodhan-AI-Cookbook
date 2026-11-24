"""The judge bake-off against the stub: estimate, refusal before any paid call, the table, cached reruns.

Every test runs in a temporary folder, so a developer's real ``.env`` is never loaded.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from bodhan_eval import bakeoff
from bodhan_eval.bakeoff import Price, estimate, listed_prices, parse_price
from bodhan_eval.config import defaults
from bodhan_eval.models import Item
from bodhan_eval.testing.stub_server import StubServer

N1_SRC, N1_TGT = "The train leaves at 7 o'clock.", "ट्रेन 9 बजे निकलती है।"  # 7 -> 9
N2_SRC, N2_TGT = "The book costs 250 rupees.", "किताब की कीमत 520 रुपये है।"  # 250 -> 520
CLEAN = [("Water boils.", "पानी उबलता है।"), ("Ice melts.", "बर्फ पिघलती है।")]


def number_error(target: int, source: int) -> str:
    err = {
        "category": "number",
        "severity": "critical",
        "target_tokens": [target, target],
        "source_tokens": [source, source],
        "explanation": "the number changed",
        "confidence": 0.9,
    }
    return json.dumps({"errors": [err]})


FIXTURE = {
    "translate": {
        "en": {
            N1_TGT: "The train leaves at 9 o'clock.",
            N2_TGT: "The book costs 520 rupees.",
            **{t: s for s, t in CLEAN},
        }
    },
    "chat": [
        {"model": "weak/judge", "content": "I cannot answer that."},
        {"model": "good/judge", "contains": "[1]9 ", "content": number_error(1, 4)},
        {"model": "good/judge", "contains": "[3]520 ", "content": number_error(3, 3)},
        {"content": '{"errors": []}'},
    ],
    "models": [
        {"id": "good/judge", "pricing": {"prompt": "0.000002", "completion": "0.00001"}},
        {"id": "free/thing", "pricing": {"prompt": "0", "completion": "0"}},
        {"id": "broken/listing"},
    ],
}


def label(tgt: str, wrong: str) -> list[dict]:
    a = tgt.index(wrong)
    return [{"type": "number", "severity": "critical", "span": [a, a + len(wrong)]}]


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Path, StubServer]]:
    with StubServer(FIXTURE) as stub:
        monkeypatch.chdir(tmp_path)
        for k, v in stub.env().items():
            monkeypatch.setenv(k, v)
        rows = [
            {"id": "n1", "lang": "hi", "source": N1_SRC, "translation": N1_TGT, "labels": label(N1_TGT, "9")},
            {"id": "n2", "lang": "hi", "source": N2_SRC, "translation": N2_TGT, "labels": label(N2_TGT, "520")},
        ] + [
            {"id": f"c{k}", "lang": "hi", "source": s, "translation": t, "labels": []} for k, (s, t) in enumerate(CLEAN)
        ]
        (tmp_path / "bakeoff.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
        )
        yield tmp_path, stub


def args(*extra: str) -> list[str]:
    return [
        "--dataset", "bakeoff.jsonl", "--models", "good/judge,weak/judge", "--out", "out",
        "--price", "good/judge=2/10", "--price", "weak/judge=0.1/0.1", "--max-usd", "1",
        "--repeat-sample", "1", "--log-level", "warning", *extra,
    ]  # fmt: skip


def chat_requests(stub: StubServer) -> list[dict]:
    return [r for r in stub.requests if r["path"].endswith("/chat/completions")]


# ---- pieces --------------------------------------------------------------------


def test_parse_price() -> None:
    assert parse_price("openai/gpt-6-luna=0.4/1.6") == ("openai/gpt-6-luna", Price(0.4, 1.6))
    for bad in ("x", "x=1", "x=a/b", "=1/2", "x=-1/2"):
        with pytest.raises(Exception, match="--price"):
            parse_price(bad)


def test_prices_are_read_from_the_models_listing(workdir) -> None:
    _, stub = workdir
    prices = listed_prices(f"{stub.url}/v1", "stub-key")
    assert prices == {"good/judge": Price(2.0, 10.0), "free/thing": Price(0.0, 0.0)}  # a broken row is skipped


def test_listing_unavailable_gives_no_prices() -> None:
    assert listed_prices("http://127.0.0.1:9/v1", None, timeout=1.0) == {}


def test_estimate_counts_replicas_tie_breaks_and_repeats() -> None:
    cfg = {**defaults()["judge"], "replicas": 2, "repeat_sample": 0.5}
    items = [Item(id="a", lang="hi", source=N1_SRC, translation=N1_TGT), Item(id="b", lang="hi", source="x")]
    e = estimate(items, "m", Price(1.0, 2.0), cfg, out_tokens=100)
    per_item = 2 * (1 + bakeoff.TIE_BREAK_SHARE) + 0.5
    assert e.items == 1 and e.calls == pytest.approx(per_item)
    assert e.completion_tokens == pytest.approx(per_item * 100)
    assert e.prompt_tokens > per_item * 500  # the rubric alone is several hundred tokens
    assert e.usd == pytest.approx((e.prompt_tokens * 1.0 + e.completion_tokens * 2.0) / 1e6)


# ---- refusing before any paid call ----------------------------------------------


def test_refuses_above_max_usd_before_any_call(workdir, capsys) -> None:
    _, stub = workdir
    a = args()
    a[a.index("--max-usd") + 1] = "0.000001"
    assert bakeoff.main(a) == 2
    assert stub.requests == []
    assert "Estimate $" in capsys.readouterr().out


def test_refuses_a_model_without_a_price(workdir) -> None:
    _, stub = workdir
    a = args()
    k = a.index("weak/judge=0.1/0.1")
    del a[k - 1 : k + 1]  # weak/judge has no --price and is not in the listing
    assert bakeoff.main(a) == 2
    assert [r["method"] for r in stub.requests] == ["GET"]  # only the price listing was asked


def test_dry_run_spends_nothing(workdir) -> None:
    _, stub = workdir
    assert bakeoff.main(args("--dry-run")) == 0
    assert stub.requests == []


def test_needs_labelled_items(workdir) -> None:
    tmp, _ = workdir
    (tmp / "unlabelled.jsonl").write_text(
        json.dumps({"id": "x", "lang": "hi", "source": "a", "translation": "b"}) + "\n", "utf-8"
    )
    a = args()
    a[a.index("--dataset") + 1] = "unlabelled.jsonl"
    assert bakeoff.main(a) == 1


# ---- the run ----------------------------------------------------------------------


def test_bakeoff_table_and_cached_rerun(workdir) -> None:
    tmp, stub = workdir
    assert bakeoff.main(args()) == 0
    report = json.loads((tmp / "out" / "bakeoff.json").read_text("utf-8"))
    rows = {row["model"]: row for row in report["models"]}
    good, weak = rows["good/judge"], rows["weak/judge"]
    assert good["judge_alone"]["per_category"]["number"]["recall"] == 1.0
    assert good["judge_alone"]["precision"] == 1.0
    assert good["invalid_output_rate"] == 0.0 and weak["invalid_output_rate"] == 1.0
    assert weak["judge_alone"]["recall"] == 0.0
    assert good["order_consistency"] == 1.0 and good["repeat_consistency"] == 1.0 and good["repeat_items"] == 4
    assert good["response_format"] == "json_schema"
    assert good["tokens_per_item"] > 0 and good["usd_per_100_items"] > weak["usd_per_100_items"]
    assert good["harness"]["recall"] == 1.0 and good["harness"]["false_alarm_rate"] == 0.0
    md = (tmp / "out" / "bakeoff.md").read_text("utf-8")
    assert "| Model | Judge-alone P / R |" in md and "`good/judge`" in md
    first = (tmp / "out" / "bakeoff.json").read_bytes()
    calls = len(chat_requests(stub))
    assert bakeoff.main(args()) == 0  # rerun: everything from the cache
    assert len(chat_requests(stub)) == calls
    assert (tmp / "out" / "bakeoff.json").read_bytes() == first
    spend = json.loads((tmp / "out" / "bakeoff_spend.json").read_text("utf-8"))
    assert spend["llm"]["good/judge"]["fresh_calls"] == 0 and spend["llm"]["good/judge"]["cache_hits"] > 0


def test_stops_taking_items_when_the_budget_is_reached(workdir) -> None:
    """The estimate passes, but the real spend reaches --max-usd: the rest is skipped, not paid for."""
    tmp, _ = workdir
    # estimated 1 output token per call passes the cap; the stub's real answers (~40 tokens each) at $1 per
    # output token do not
    a = args("--concurrency", "1", "--est-output-tokens", "1")
    a[a.index("good/judge=2/10")] = "good/judge=0/1000000"
    a[a.index("--max-usd") + 1] = "50"
    assert bakeoff.main(a) == 0
    rows = {row["model"]: row for row in json.loads((tmp / "out" / "bakeoff.json").read_text("utf-8"))["models"]}
    assert rows["good/judge"]["judged_items"] < 4
    assert any("--max-usd" in x for x in rows["weak/judge"]["not_judged"] + rows["good/judge"]["not_judged"])
