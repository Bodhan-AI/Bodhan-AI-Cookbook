"""Tests for CLI flags that previously parsed but did nothing.

--langs and --calibration were accepted and silently ignored; the judge's
consistency metric was measured through the cache and was therefore always 100%.
These tests pin the corrected behaviour.
"""

from __future__ import annotations

import json

from bodhan_eval import config
from bodhan_eval.cli import main


def _write(path, rows):
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                    encoding="utf-8")
    return path


ROWS = [
    {"id": "a-hi", "source": "Water boils at 100 °C.", "lang": "hi",
     "translation": "पानी 100 °C पर उबलता है।", "labels": []},
    {"id": "b-ta", "source": "Jupiter is a large planet.", "lang": "ta",
     "translation": "வியாழன் ஒரு பெரிய கோள்.", "labels": []},
    {"id": "c-bn", "source": "A leap year has 366 days.", "lang": "bn",
     "translation": "অধিবর্ষে 366 দিন থাকে।", "labels": []},
]


def test_langs_filters_items(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ds = _write(tmp_path / "d.jsonl", ROWS)
    out = tmp_path / "rep"

    assert main(["--dataset", str(ds), "--langs", "hi,ta", "--out", str(out), "--offline"]) == 0
    summary = json.loads((out / "summary.json").read_text())
    assert summary["items_scored"] == 2
    assert summary["languages"] == ["hi", "ta"], "bn must be filtered out"


def test_langs_with_no_match_fails_cleanly(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ds = _write(tmp_path / "d.jsonl", ROWS)
    code = main(["--dataset", str(ds), "--langs", "ml", "--out", str(tmp_path / "r"),
                 "--offline"])
    assert code == 2, "an empty selection must exit non-zero, not report zero items"


def test_calibration_file_is_read(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ds = _write(tmp_path / "d.jsonl", ROWS)
    # A calibration file with many labelled items must change the sweep input.
    calib_rows = []
    for i in range(12):
        calib_rows.append({
            "id": f"cal-{i}", "source": " ".join(["word"] * 20), "lang": "hi",
            "translation": " ".join(["शब्द"] * 4),           # ratio 0.2 -> omission
            "labels": [{"type": "omission", "severity": "major", "span": [0, 5]}],
        })
    calib = _write(tmp_path / "calib.jsonl", calib_rows)

    out = tmp_path / "rep"
    assert main(["--dataset", str(ds), "--langs", "hi,ta,bn", "--out", str(out),
                 "--calibration", str(calib), "--offline"]) == 0

    cal = json.loads((out / "calibration.json").read_text())
    sweep = cal["length_sweep"]["chosen"]
    assert sweep["n_omission"] >= 12, "calibration labels must reach the sweep"
    assert sweep["lo_tuned"] is True, "with enough labels the sweep must actually tune"


def test_missing_calibration_file_does_not_crash(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ds = _write(tmp_path / "d.jsonl", ROWS)
    out = tmp_path / "rep"
    assert main(["--dataset", str(ds), "--langs", "hi,ta,bn", "--out", str(out),
                 "--calibration", str(tmp_path / "nope.jsonl"), "--offline"]) == 0


def test_judge_consistency_bypasses_cache(tmp_path, monkeypatch):
    """A re-ask must be a real second sample, not a cache hit."""
    from bodhan_eval.cache import Cache
    from bodhan_eval.checks.base import ItemContext
    from bodhan_eval.judge import Judge

    monkeypatch.setattr(config, "LLM_BASE_URL", "http://127.0.0.1:1/v1", raising=False)
    monkeypatch.setattr(config, "LLM_MODEL", "m", raising=False)
    monkeypatch.setattr(config, "LLM_API_KEY", "k", raising=False)
    monkeypatch.setattr(config, "have_judge", lambda: True, raising=False)

    cache = Cache(tmp_path / "cache")
    judge = Judge(cache, offline=False)
    ctx = ItemContext(id="x", source="Hello.", lang="hi", translation="नमस्ते।")

    # Prime the cache as if a first successful call had happened.
    key = Cache.key("judge", config.LLM_MODEL, judge.temperature,
                    json.dumps({"source": "Hello.", "translation": "नमस्ते।",
                                "language": "hi", "already found": []},
                               sort_keys=True, ensure_ascii=False))
    cache.put_json(key, {"flags": [], "raw": "cached"})

    import asyncio
    # Cached path returns immediately...
    first = asyncio.run(judge.review(ctx, []))
    assert first.ok and first.raw == "cached"
    # ...but a consistency re-ask must actually call the endpoint (which is
    # unreachable here), proving the cache was bypassed.
    second = asyncio.run(judge.review(ctx, [], use_cache=False))
    assert not second.ok, "use_cache=False must attempt a live call"
