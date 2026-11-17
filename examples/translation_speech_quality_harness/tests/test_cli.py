import json
from pathlib import Path

from conftest import DATA, make_args, make_clients

from bodhan_eval import pipeline
from bodhan_eval.__main__ import main

FILES = ["items.jsonl", "summary.json", "summary.md", "calibration.json", "review_queue.csv"]


def run(tmp_path, fake_bodhan, fake_llm, **kw):
    bodhan, llm = make_clients(tmp_path, fake_bodhan, fake_llm)
    args = make_args(tmp_path, **kw)
    assert pipeline.run(args, bodhan=bodhan, llm=llm) == 0
    return Path(args.out)


def test_writes_all_report_files(tmp_path, fake_bodhan, fake_llm):
    out = run(tmp_path, fake_bodhan, fake_llm)
    for f in FILES:
        assert (out / f).is_file(), f
    assert not (out / "diff.md").exists()
    rows = [json.loads(line) for line in (out / "items.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 10 and [r["id"] for r in rows] == sorted(r["id"] for r in rows)
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["items"] == 10 and summary["skipped"]["count"] == 0


def test_real_outputs_clean_and_seeded_number_flagged(tmp_path, fake_bodhan, fake_llm):
    rows = [json.loads(line) for line in (DATA / "ten.jsonl").read_text(encoding="utf-8").splitlines()]
    rows[0]["translation"] = rows[0]["translation"].replace("100", "1000")
    ds = tmp_path / "seeded.jsonl"
    ds.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    out = run(tmp_path, fake_bodhan, fake_llm, dataset=ds)
    items = {r["id"]: r for r in map(json.loads, (out / "items.jsonl").read_text(encoding="utf-8").splitlines())}
    assert items["t01"]["status"] == "flagged" and items["t01"]["flags"][0]["category"] == "number"
    s = rows[0]["source"].index("100")
    assert items["t01"]["flags"][0]["span"] == [s, s + 3] and items["t01"]["flags"][0]["span_in"] == "source"
    assert items["t10"]["status"] == "unscored"  # no translation, nothing to check
    assert all(r["status"] == "clean" for k, r in items.items() if k not in ("t01", "t10"))


def test_report_is_byte_identical_on_rerun(tmp_path, fake_bodhan, fake_llm):
    out = run(tmp_path, fake_bodhan, fake_llm)
    first = {f: (out / f).read_bytes() for f in FILES}
    run(tmp_path, fake_bodhan, fake_llm)
    assert {f: (out / f).read_bytes() for f in FILES} == first


def test_garbage_dataset_still_writes_report(tmp_path, fake_bodhan, fake_llm):
    bad = tmp_path / "bad.jsonl"
    bad.write_bytes(b"\x00\x01 nonsense\n{\"id\": 1}\n[]\n")
    out = run(tmp_path, fake_bodhan, fake_llm, dataset=bad)
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["items"] == 0 and summary["skipped"]["count"] == 3


def test_unknown_langs_warn_and_continue(tmp_path, fake_bodhan, fake_llm):
    out = run(tmp_path, fake_bodhan, fake_llm, langs="hi,fr,,xx")
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["langs_requested"] == ["hi"] and len(summary["warnings"]) == 2
    assert summary["items"] == 4  # t01, t02, t05, t07


def test_missing_dataset_exits_2(tmp_path, capsys):
    code = main(["--dataset", str(tmp_path / "nope.jsonl"), "--langs", "hi", "--out", str(tmp_path / "r"), "--cache-dir", str(tmp_path / "c")])
    assert code == 2 and "cannot read dataset" in capsys.readouterr().err


def test_stale_diff_removed_without_baseline(tmp_path, fake_bodhan, fake_llm):
    out = tmp_path / "report"
    out.mkdir()
    (out / "diff.md").write_text("old", encoding="utf-8")
    run(tmp_path, fake_bodhan, fake_llm)
    assert not (out / "diff.md").exists()


def test_skip_switches_checks_off(tmp_path, fake_bodhan, fake_llm):
    out = run(tmp_path, fake_bodhan, fake_llm, skip="judge,regen_diff,bogus")
    rows = [json.loads(line) for line in (out / "items.jsonl").read_text(encoding="utf-8").splitlines()]
    assert all(r["checks_run"].get("judge", "not_run: disabled by --skip") == "not_run: disabled by --skip" for r in rows)
    assert any(r["checks_run"].get("regen_diff") == "not_run: disabled by --skip" for r in rows)
    assert "bogus" in json.loads((out / "summary.json").read_text(encoding="utf-8"))["warnings"][0]


def test_unseen_language_runs_end_to_end(tmp_path, fake_bodhan, fake_llm):
    # Telugu is not in the demo set; nothing language-specific should be needed.
    src = ["Water boils at 100 degrees at sea level.", "The heart pumps blood through the body."]
    te = ["సముద్ర మట్టం వద్ద నీరు 100 డిగ్రీల వద్ద మరుగుతుంది.", "గుండె శరీరం అంతటా రక్తాన్ని పంపుతుంది."]
    known = {(s, "Telugu"): t for s, t in zip(src, te, strict=True)} | {(t, "English"): s for s, t in zip(src, te, strict=True)}
    fake_bodhan.translate = lambda text, lang: known.get((text, lang), text)
    rows = [{"id": "a", "lang": "te", "source": src[0], "translation": te[0].replace("100", "1000")},
            {"id": "b", "lang": "te", "source": src[1], "translation": te[1]}]
    ds = tmp_path / "te.jsonl"
    ds.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    out = run(tmp_path, fake_bodhan, fake_llm, dataset=ds, langs="te")
    items = {r["id"]: r for r in map(json.loads, (out / "items.jsonl").read_text(encoding="utf-8").splitlines())}
    assert items["a"]["status"] == "flagged" and items["a"]["flags"][0]["category"] == "number"
    assert items["b"]["status"] == "clean"


def test_language_far_from_fresh_translation_warns(tmp_path, fake_bodhan, fake_llm):
    rows = [{"id": f"x{i}", "lang": "hi", "source": f"Sentence number {i} is here.", "translation": f"पूरी तरह अलग पाठ {i} यहाँ है।"} for i in range(10)]
    fake_bodhan.translate = lambda text, lang: "कुछ और ही लिखा है" if lang == "Hindi" else text
    ds = tmp_path / "far.jsonl"
    ds.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    out = run(tmp_path, fake_bodhan, fake_llm, dataset=ds, langs="hi")
    assert any(w.startswith("hi: median similarity") for w in json.loads((out / "summary.json").read_text(encoding="utf-8"))["warnings"])
