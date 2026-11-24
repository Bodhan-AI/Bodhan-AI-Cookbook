"""The Bodhan demo builder, against mocked indic-translate and indic-speak."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from bodhan_eval.clients import http as http_mod
from bodhan_eval.dataset import load
from conftest import Router, json_body, speechlike, wav_bytes

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_bodhan_demo.py"


@pytest.mark.parametrize("no_speech", [False, True])
def test_builder_writes_audio_and_labels(tmp_path, monkeypatch, no_speech):
    spec = importlib.util.spec_from_file_location("build_bodhan_demo", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def translate(request):
        n = len(json_body(request)["text"]) // 10 + 1
        return httpx.Response(200, json={"translation": " ".join(["यह एक वाक्य है।"] * n) + " कुल 25 लोग थे।", "usage": {"completion_tokens": 10}})

    router = Router(
        {"/translate": translate, "/v1/audio/speech": lambda r: httpx.Response(200, content=wav_bytes(speechlike(2.0)), headers={"content-type": "audio/wav"})}
    )
    real = httpx.AsyncClient

    class Mock(real):
        def __init__(self, *a, **k):
            k["transport"] = httpx.MockTransport(router)
            super().__init__(*a, **k)

    monkeypatch.setattr(mod.httpx, "AsyncClient", Mock)

    async def instant(_):
        return None

    monkeypatch.setattr(http_mod.asyncio, "sleep", instant)
    for key in ("BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY"):
        monkeypatch.setenv(key, "k")
    src = tmp_path / "in.jsonl"
    src.write_text(
        "".join(json.dumps({"id": f"../s{i}", "source": f"There were 25 people at rainy event number {i}.", "lang": "hi",
                            "meta": {"provenance": "test source", "injection": "stale edit"}}) + "\n" for i in range(12)),
        encoding="utf-8",
    )
    out = tmp_path / "data" / "out.jsonl"
    args = argparse.Namespace(
        input=str(src),
        out=str(out),
        audio_dir=str(tmp_path / "outputs" / "audio"),
        rate=0.5,
        speech_share=0.5,
        seed=1,
        limit=None,
        voice=None,
        retranslate=False,
        no_speech=no_speech,
        audio_per_lang=6,
        audio_max_source_chars=180,
        audio_max_chars=220,
        max_cost_inr=5.0,
        cache_dir=str(tmp_path / "cache"),
    )
    assert asyncio.run(mod.build(args)) == 0
    items, bad = load(out)
    assert not bad and len(items) == 12
    audio_items = [li for li in items if li.audio_path]
    assert len(audio_items) == (0 if no_speech else 6)
    assert all(li.audio_path.exists() for li in audio_items)
    assert all(li.audio_path.resolve().is_relative_to((tmp_path / "outputs" / "audio").resolve()) for li in audio_items)
    assert all(li.item.meta["provenance"] == "test source" and li.item.meta.get("injection") != "stale edit" for li in items)
    kinds = [li.item.labels[0].type for li in items if li.item.labels]
    assert len(kinds) == 6
    assert sum(k.startswith("speech_") for k in kinds) == (0 if no_speech else 3)
    manifest = json.loads(out.with_suffix(".build.json").read_text())
    assert manifest["audio_items"] == len(audio_items)
    assert manifest["budget"]["spent_this_run_inr"] <= 5.0


def test_audio_subset_is_balanced_and_excludes_long_or_structured_text():
    spec = importlib.util.spec_from_file_location("build_bodhan_demo", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rows = [{"id": str(i), "lang": lang, "source": "A short sentence.", "translation": "Short output."}
            for lang in ("hi", "ta") for i in range(8)]
    for row in rows:
        i = int(row["id"])
        if i == 0:
            row["source"] *= 20
        elif i == 1:
            row["translation"] *= 30
        elif i == 2:
            row["meta"] = {"content": "table"}
        elif i == 3:
            row["source"] += " Another sentence."
    args = argparse.Namespace(seed=31, audio_per_lang=2, audio_max_source_chars=180, audio_max_chars=220)
    selected = mod.audio_subset(rows, args)
    assert selected == mod.audio_subset(list(reversed(rows)), args)
    assert len(selected) == 4
    assert all(int(key[0]) >= 4 for key in selected)
    assert sum(lang == "hi" for _, lang in selected) == 2


@pytest.mark.parametrize("change", [{"rate": -0.1}, {"speech_share": 2}, {"max_cost_inr": float("nan")}, {"limit": 0}])
def test_invalid_options_fail_before_opening_input_or_network(change):
    spec = importlib.util.spec_from_file_location("build_bodhan_demo", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    args = argparse.Namespace(rate=0.5, speech_share=0.3, max_cost_inr=3.0, limit=None,
                              audio_per_lang=6, audio_max_source_chars=180, audio_max_chars=220)
    vars(args).update(change)
    with pytest.raises(ValueError):
        asyncio.run(mod.build(args))


def test_duplicate_input_rejected_before_paid_work(tmp_path):
    spec = importlib.util.spec_from_file_location("build_bodhan_demo", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    src = tmp_path / "duplicate.jsonl"
    row = json.dumps({"id": "a", "source": "Source.", "lang": "hi"})
    src.write_text(row + "\n" + row + "\n", encoding="utf-8")
    args = argparse.Namespace(input=str(src), rate=0.5, speech_share=0.3, max_cost_inr=3.0, limit=None,
                              audio_per_lang=6, audio_max_source_chars=180, audio_max_chars=220)
    with pytest.raises(ValueError, match="duplicate"):
        asyncio.run(mod.build(args))


def test_translation_failure_preserves_previous_dataset_and_records_cost(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("build_bodhan_demo", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    async def failed(*args, **kwargs):
        from bodhan_eval.clients.http import ServiceUnavailable

        raise ServiceUnavailable("test unavailable")

    monkeypatch.setattr(mod.BodhanClient, "translate", failed)
    src, out = tmp_path / "input.jsonl", tmp_path / "output.jsonl"
    src.write_text(json.dumps({"id": "a", "source": "A sentence.", "lang": "hi"}) + "\n", encoding="utf-8")
    out.write_text("previous dataset\n", encoding="utf-8")
    args = argparse.Namespace(input=str(src), out=str(out), audio_dir=str(tmp_path / "audio"),
                              cache_dir=str(tmp_path / "cache"), rate=0.5, speech_share=0.3, max_cost_inr=0,
                              limit=None, audio_per_lang=6, audio_max_source_chars=180, audio_max_chars=220)
    assert asyncio.run(mod.build(args)) == 1
    assert out.read_text(encoding="utf-8") == "previous dataset\n"
    manifest = json.loads(out.with_suffix(".build.json").read_text())
    assert len(manifest["failures"]) == 1 and manifest["budget"]["spent_this_run_inr"] == 0
