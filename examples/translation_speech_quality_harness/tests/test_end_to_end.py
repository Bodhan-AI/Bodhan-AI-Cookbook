"""test_missing_audio: A row whose audio file is missing: speech checks are skipped, it is logged and counted, nothing crashes.
test_report_diff: Block 7: report sections, summary.md, baseline diff (McNemar, mismatched ids), and --ci gate.
test_audit_fixes: Tests for the pre-submission audit fixes: key fallback, judge availability and stop rule,
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

import httpx
import openai
import pytest

from bodhan_eval.calibration import evaluate, load_labels
from bodhan_eval.checks import Flag
from bodhan_eval.clients import DEFAULT_LLM_CONCURRENCY, LLMClient, LLMError, llm_concurrency_from_env
from bodhan_eval.config import DEFAULTS, PRODUCTION_CODES, UnknownLanguageError, load_settings, parse_langs
from bodhan_eval.dataset import Item, _parse_label, load_dataset, parse_labels
from bodhan_eval.infra import mcnemar_exact
from bodhan_eval.judge import counted_flag_agreement, fuse, parse_judgment
from bodhan_eval.pipeline import LLM_FAILURES_BEFORE_STOP, RunOptions, build_parser, main, run_judge_pass
from bodhan_eval.report import _judge_unavailable_line, _label_caveat, build_diff, render_diff_md

from .conftest import make_wav

# ==================== cli_smoke
ROWS = [
    {"id": "a1", "source": "Water boils at 100 °C.", "lang": "hi", "translation": "पानी 100 °C पर उबलता है।"},
    {"id": "a2", "source": "The sun is a star.", "lang": "ta"},
    {"id": "a3", "source": "Rivers flow to the sea.", "lang": "bn", "labels": [{"type": "omission", "severity": "major", "span": [0, 4]}]},
    {"id": "a4", "source": "Filtered out.", "lang": "mr"},
    {"source": "no id", "lang": "hi"},
    "not json",
]


def write_dataset(path):
    with path.open("w", encoding="utf-8") as fh:
        for r in ROWS:
            fh.write((json.dumps(r, ensure_ascii=False) if isinstance(r, dict) else r) + "\n")


def test_parse_langs():
    assert parse_langs("hi, ta,bn,hi") == (["hi", "ta", "bn"], [])
    codes, warns = parse_langs("ne")
    assert codes == ["ne"] and warns
    assert len(PRODUCTION_CODES) == 13
    try:
        parse_langs("xx")
        raise AssertionError("expected failure")
    except UnknownLanguageError:
        pass


def test_cli_end_to_end_empty_valid_outputs(tmp_path, monkeypatch):
    for k in ("BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY", "BODHAN_TRANSCRIBE_KEY", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)  # no .env found from here
    ds = tmp_path / "data.jsonl"
    write_dataset(ds)
    out = tmp_path / "report"
    rc = main(["--dataset", str(ds), "--langs", "hi,ta,bn", "--out", str(out), "--cache-dir", str(tmp_path / "cache")])
    assert rc == 0
    for name in ("summary.md", "summary.json", "items.jsonl", "calibration.json", "run_meta.json"):
        assert (out / name).exists(), name
    assert not (out / "diff.md").exists()
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["dataset"]["evaluated"] == 3
    assert summary["dataset"]["bad_rows"] == 2
    assert summary["dataset"]["filtered_out"] == 1
    items = [json.loads(line) for line in (out / "items.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [i["id"] for i in items] == ["a3", "a1", "a2"]  # sorted by (lang, id)
    assert (out / "logs" / "run.jsonl").exists()


def test_cli_rejects_unknown_lang(tmp_path, capsys):
    ds = tmp_path / "d.jsonl"
    write_dataset(ds)
    assert main(["--dataset", str(ds), "--langs", "hi,zz", "--out", str(tmp_path / "r")]) == 2
    assert "Unknown language code" in capsys.readouterr().err


def test_cli_report_is_deterministic(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ds = tmp_path / "data.jsonl"
    write_dataset(ds)
    outs = []
    for n in range(2):
        out = tmp_path / f"r{n}"
        main(["--dataset", str(ds), "--langs", "hi,ta,bn", "--out", str(out), "--cache-dir", str(tmp_path / "cache")])
        outs.append(out)
    for name in ("summary.md", "summary.json", "items.jsonl", "calibration.json"):
        assert (outs[0] / name).read_bytes() == (outs[1] / name).read_bytes(), name


# ==================== missing_audio
def write_rows(tmp_path):
    (tmp_path / "ok.wav").write_bytes(make_wav(2.0))
    rows = [
        {"id": "m1", "source": "Rivers flow to the sea.", "lang": "hi", "translation": "x y z.", "audio": "gone.wav",
         "labels": [{"type": "truncation", "severity": "major", "span": [1.0, 1.0], "span_unit": "seconds"}]},
        {"id": "m2", "source": "The sun is a star.", "lang": "hi", "translation": "a b c.", "audio": "ok.wav"},
        {"id": "m3", "source": "Water boils.", "lang": "hi", "translation": "p q."},
    ]
    ds = tmp_path / "d.jsonl"
    ds.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return ds


def test_loader_marks_missing_audio(tmp_path):
    res = load_dataset(write_rows(tmp_path))
    assert not res.bad and res.missing_audio == ["m1"]
    m1 = next(i for i in res.items if i.id == "m1")
    assert m1.audio is None and m1.audio_missing and m1.labels  # item and its labels are kept


def test_harness_skips_speech_checks_and_counts(tmp_path, monkeypatch):
    for k in ("BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY", "BODHAN_TRANSCRIBE_KEY", "BODHAN_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)
    ds = write_rows(tmp_path)
    out = tmp_path / "rep"
    # --generate must not re-synthesize a named (missing) file: no keys are set, so any attempt would error
    rc = main(["--dataset", str(ds), "--langs", "hi", "--out", str(out), "--cache-dir", str(tmp_path / "c"), "--generate"])
    assert rc == 0
    items = {r["id"]: r for r in map(json.loads, (out / "items.jsonl").read_text(encoding="utf-8").splitlines())}
    m1 = items["m1"]
    assert m1["status"] == "ok" and m1["skipped"] == [{"what": "speech_checks", "reason": "audio_missing"}]
    assert m1["inputs"]["audio"] is False and "generated" not in m1
    audio_checks = [c for c in m1["checks"] if c["check"].startswith("audio")]
    assert audio_checks and all(c["status"] == "skipped" for c in audio_checks)
    assert not any(f["category"].startswith("speech") for f in m1["flags"])
    assert items["m2"]["inputs"]["audio"] is True and "skipped" not in items["m2"]
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["dataset"]["missing_audio"] == 1 and summary["dataset"]["missing_audio_ids"] == ["m1"]
    assert "audio file is missing" in (out / "summary.md").read_text(encoding="utf-8")
    events = [json.loads(x) for x in (out / "logs" / "run.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(e.get("event") == "audio_missing" and e.get("id") == "m1" for e in events)


def test_clean_clone_demo_without_audio_or_cache(tmp_path, monkeypatch):
    """On a fresh clone (gitignored sample_data/audio/*.wav and .bodhan_cache absent), the
    harness runs sample_data/demo.jsonl cleanly at zero cost, skipping and counting missing audio."""
    from pathlib import Path
    for k in ("BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY", "BODHAN_TRANSCRIBE_KEY", "BODHAN_API_KEY", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)
    recipe = Path(__file__).resolve().parents[1]
    demo_src = recipe / "sample_data" / "demo.jsonl"
    # Copy only demo.jsonl into tmp_path (no sample_data/audio/ directory, no cache)
    ds = tmp_path / "demo.jsonl"
    ds.write_bytes(demo_src.read_bytes())
    out = tmp_path / "clean_report"
    rc = main(["--dataset", str(ds), "--langs", "hi,ta,bn", "--out", str(out),
               "--calibration", str(ds), "--max-cost-inr", "0", "--cache-dir", str(tmp_path / "empty_cache")])
    assert rc == 0
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["dataset"]["evaluated"] == 138
    assert summary["dataset"]["item_errors"] == 0
    assert summary["dataset"]["missing_audio"] == 66
    assert (out / "summary.md").exists() and (out / "calibration.json").exists()


# ==================== report_diff
def test_mcnemar_exact_reference_values():
    assert round(mcnemar_exact(10, 2), 4) == 0.0386
    assert mcnemar_exact(5, 4) == 1.0
    assert mcnemar_exact(0, 0) == 1.0


def test_build_diff_handles_mismatched_ids_and_significance():
    base = {
        "shared_imp": {"id": "shared_imp", "lang": "hi", "status": "ok", "flags": []},
        "shared_reg": {
            "id": "shared_reg",
            "lang": "hi",
            "status": "ok",
            "flags": [
                {
                    "check_id": "numbers",
                    "category": "number_unit",
                    "severity": "major",
                    "span": [0, 2],
                    "unit": "chars",
                    "message": "x",
                    "needs_corroboration": False,
                }
            ],
        },
        "only_base": {"id": "only_base", "lang": "ta", "status": "ok", "flags": []},
    }
    cur = [
        {
            "id": "shared_imp",
            "lang": "hi",
            "status": "ok",
            "flags": [
                {
                    "check_id": "numbers",
                    "category": "number_unit",
                    "severity": "major",
                    "span": [0, 2],
                    "unit": "chars",
                    "message": "fixed",
                    "needs_corroboration": False,
                }
            ],
        },
        {"id": "shared_reg", "lang": "hi", "status": "ok", "flags": []},
        {"id": "only_cur", "lang": "bn", "status": "ok", "flags": []},
    ]
    labels = {
        "shared_imp": [_parse_label({"type": "number_unit", "severity": "major", "span": [0, 2]})],
        "shared_reg": [_parse_label({"type": "number_unit", "severity": "major", "span": [0, 2]})],
        "only_cur": [],
    }
    d = build_diff(base, cur, labels)
    assert d["items"] == {"current": 3, "baseline": 3, "shared": 2, "new": 1, "removed": 1}
    assert d["new_ids"] == ["only_cur"] and d["removed_ids"] == ["only_base"]
    assert d["counts"]["improved"] == 1 and d["counts"]["regressed"] == 1
    assert d["overall"]["label"] == "within noise"
    md = render_diff_md(d, "base_dir")
    assert "only_cur" in md and "only_base" in md and "within noise" in md and "shared_imp" in md


def test_cli_baseline_diff_and_ci_flag(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for k in ("BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY", "BODHAN_TRANSCRIBE_KEY", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(k, raising=False)
    # Build 10 error items where numbers check flags them in baseline, then disable via a dataset without numbers
    base_rows = [
        {
            "id": f"i{n}",
            "source": "Water boils at 100 C.",
            "lang": "hi",
            "translation": "99 C",
            "labels": [{"type": "number_unit", "severity": "major", "span": [0, 2]}],
        }
        for n in range(10)
    ]
    cur_rows = [
        {
            "id": f"i{n}",
            "source": "Water boils at 100 C.",
            "lang": "hi",
            "translation": "100 C",
            "labels": [{"type": "number_unit", "severity": "major", "span": [0, 3]}],
        }
        for n in range(10)
    ]
    b_ds, c_ds = tmp_path / "b.jsonl", tmp_path / "c.jsonl"
    b_ds.write_text("\n".join(json.dumps(r) for r in base_rows) + "\n", encoding="utf-8")
    c_ds.write_text("\n".join(json.dumps(r) for r in cur_rows) + "\n", encoding="utf-8")
    b_out, c_out = tmp_path / "base_rep", tmp_path / "cur_rep"
    assert main(["--dataset", str(b_ds), "--langs", "hi", "--out", str(b_out), "--cache-dir", str(tmp_path / "cache")]) == 0
    # 10 regressions, 0 improvements -> p = 0.0020 < 0.05 -> --ci exits 1
    rc = main(["--dataset", str(c_ds), "--langs", "hi", "--out", str(c_out), "--baseline", str(b_out), "--ci", "--cache-dir", str(tmp_path / "cache")])
    assert rc == 1
    assert (c_out / "diff.md").exists()
    md = (c_out / "summary.md").read_text(encoding="utf-8")
    for heading in (
        "## Headline vs targets",
        "## Per language",
        "## Per category",
        "## Top failures",
        "## Findings for the Bodhan team",
        "## Cost and cache",
        "## Bad, skipped and failed items",
        "## Caveats",
    ):
        assert heading in md


# ==================== audit_fixes
RECIPE = Path(__file__).resolve().parents[1]


TGT = "इसलिए, यह संभावना है कि संकेतन को केवल एक लेबल के रूप में जोड़ा गया था।"


INDIC = re.compile(r"[ऀ-෿]")


KEYS = ("BODHAN_API_KEY", "BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY", "BODHAN_TRANSCRIBE_KEY", "LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL")


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for k in KEYS + ("LLM_CONCURRENCY",):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)  # no .env found upward from here
    empty = tmp_path / "empty.env"
    empty.write_text("", encoding="utf-8")
    return empty


def test_env_example_declares_shared_key_and_holds_no_values():
    text = (RECIPE / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^BODHAN_API_KEY=", text, re.M)  # required by the Cookbook validator
    for k in KEYS:
        m = re.search(rf"^{k}=(.*)$", text, re.M)
        assert m and m.group(1).strip() == "", f"{k} must be declared with an empty value"


@pytest.mark.parametrize("attr,var", [("translate_key", "BODHAN_TRANSLATE_KEY"), ("speak_key", "BODHAN_SPEAK_KEY"),
                                      ("transcribe_key", "BODHAN_TRANSCRIBE_KEY")])
def test_each_model_key_falls_back_to_bodhan_api_key(clean_env, monkeypatch, attr, var):
    monkeypatch.setenv("BODHAN_API_KEY", "shared-key")
    assert getattr(load_settings(clean_env), attr) == "shared-key"
    monkeypatch.setenv(var, "per-model-key")  # a per-model key wins over the shared one
    assert getattr(load_settings(clean_env), attr) == "per-model-key"


def test_empty_values_count_as_unset(clean_env, monkeypatch):
    monkeypatch.setenv("BODHAN_API_KEY", "shared-key")
    monkeypatch.setenv("BODHAN_SPEAK_KEY", "")  # copied .env.example leaves it empty
    s = load_settings(clean_env)
    assert s.speak_key == "shared-key" and not s.llm_configured


class DeadCompletions:
    """Every call fails with a connection error, like an unreachable endpoint."""

    def __init__(self):
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        raise openai.APIConnectionError(request=httpx.Request("POST", "https://llm.test/v1/chat/completions"))


@pytest.fixture
def no_backoff(monkeypatch):
    async def no_sleep(_):
        return None

    monkeypatch.setattr("bodhan_eval.infra.asyncio.sleep", no_sleep)


def test_exhausted_retries_raise_llm_error(settings, cache, ledger, no_backoff):
    comp = DeadCompletions()
    llm = LLMClient(settings, cache, ledger, client=SimpleNamespace(chat=SimpleNamespace(completions=comp)))
    with pytest.raises(LLMError):
        run(llm.complete_json("sys", "user"))
    assert comp.calls == 6  # all retries used, then one clear "unavailable" error


def test_llm_concurrency_from_env(monkeypatch):
    monkeypatch.delenv("LLM_CONCURRENCY", raising=False)
    assert llm_concurrency_from_env() == DEFAULT_LLM_CONCURRENCY
    monkeypatch.setenv("LLM_CONCURRENCY", "2")
    assert llm_concurrency_from_env() == 2
    monkeypatch.setenv("LLM_CONCURRENCY", "zero")
    assert llm_concurrency_from_env() == DEFAULT_LLM_CONCURRENCY
    monkeypatch.setenv("LLM_CONCURRENCY", "0")
    assert llm_concurrency_from_env() == 1


OK = {"verdict": "ok", "errors": []}


class ScriptedLLM:
    """complete_json follows a per-item script: 'ok', 'down' (LLMError), 'bad' (invalid JSON), 'offline'."""

    def __init__(self, script: dict[str, str], default: str = "ok"):
        self.script, self.default, self.calls = script, default, 0

    async def complete_json(self, system, user, *, variant="", bypass_cache=False, max_tokens=2000):
        self.calls += 1
        item = re.search(r"SOURCE \(English\): \"(item \d+)\"", user).group(1)
        mode = self.script.get(item, self.default)
        if mode == "down":
            raise LLMError("connection error after retries")
        if mode == "offline":
            raise RuntimeError("offline cache miss")
        raw = "not json at all" if mode == "bad" else json.dumps(OK)
        return SimpleNamespace(data=None if mode == "bad" else OK, raw=raw, cached=False, prompt_tokens=1, completion_tokens=1)


def judge_inputs(n: int, tmp_path):
    items, results = {}, []
    for i in range(n):
        iid = f"x{i:02d}"
        items[iid] = Item(id=iid, source=f"item {i}", lang="hi", translation=TGT)
        results.append({"id": iid, "lang": "hi", "status": "ok", "flags": []})
    return items, results


def judge_pass(llm, n, tmp_path, **opts):
    items, results = judge_inputs(n, tmp_path)
    o = RunOptions(dataset=tmp_path / "d.jsonl", langs=["hi"], out=tmp_path / "out", **opts)
    run(run_judge_pass(llm, o, results, items))
    return [r["judge"] for r in results]


def reasons(judged):
    return [j["status"] if j["status"] == "ran" else f"{j['status']}:{j.get('reason', '').split(':')[0]}" for j in judged]


def test_judge_reads_every_eligible_item_by_default(tmp_path):
    assert build_parser().parse_args(["--dataset", "d", "--langs", "hi", "--out", "o"]).judge_max_items is None
    judged = judge_pass(ScriptedLLM({}), 30, tmp_path)
    assert reasons(judged) == ["ran"] * 30


def test_judge_max_items_is_an_optional_cap(tmp_path):
    judged = judge_pass(ScriptedLLM({}), 10, tmp_path, judge_max_items=4)
    assert reasons(judged).count("ran") == 4 and reasons(judged).count("skipped:judge_max_items") == 6


def test_judge_stops_after_consecutive_unavailable_failures(tmp_path):
    llm = ScriptedLLM({}, default="down")
    judged = judge_pass(llm, 10, tmp_path, llm_concurrency=1)
    r = reasons(judged)
    assert r[:LLM_FAILURES_BEFORE_STOP] == ["error:llm_error"] * LLM_FAILURES_BEFORE_STOP
    assert r[LLM_FAILURES_BEFORE_STOP:] == ["skipped:judge_unavailable"] * (10 - LLM_FAILURES_BEFORE_STOP)
    assert llm.calls == LLM_FAILURES_BEFORE_STOP


def test_invalid_json_counts_toward_the_stop(tmp_path):
    judged = judge_pass(ScriptedLLM({}, default="bad"), 6, tmp_path, llm_concurrency=1)
    assert reasons(judged) == ["error:invalid_json_after_repair"] * 3 + ["skipped:judge_unavailable"] * 3


def test_isolated_failures_do_not_stop_the_judge(tmp_path):
    # down, down, ok, down, down, ok ...: never 3 in a row, so every item is tried
    script = {f"item {i}": "down" for i in range(12) if i % 3 != 2}
    judged = judge_pass(ScriptedLLM(script), 12, tmp_path, llm_concurrency=1)
    assert "skipped:judge_unavailable" not in reasons(judged)
    assert reasons(judged).count("ran") == 4


def test_item_level_errors_do_not_count_as_unavailable(tmp_path):
    judged = judge_pass(ScriptedLLM({}, default="offline"), 6, tmp_path, llm_concurrency=1)
    assert all(r.startswith("error:RuntimeError") for r in reasons(judged))


def test_parallel_batches_stop_deterministically(tmp_path):
    first = reasons(judge_pass(ScriptedLLM({}, default="down"), 10, tmp_path, llm_concurrency=4))
    second = reasons(judge_pass(ScriptedLLM({}, default="down"), 10, tmp_path, llm_concurrency=4))
    assert first == second == ["error:llm_error"] * 4 + ["skipped:judge_unavailable"] * 6


def test_report_states_judge_unavailable_when_llm_not_configured(clean_env, tmp_path):
    ds = tmp_path / "d.jsonl"
    ds.write_text(json.dumps({"id": "a1", "source": "So, it is likely.", "lang": "hi", "translation": TGT}) + "\n", encoding="utf-8")
    out = tmp_path / "report"
    assert main(["--dataset", str(ds), "--langs", "hi", "--out", str(out), "--cache-dir", str(tmp_path / "c"), "--env-file", str(clean_env)]) == 0
    s = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert s["judge"]["unavailable"] == {"items_not_judged": 1, "by_reason": {"skipped:llm_not_configured": 1}}
    md = (out / "summary.md").read_text(encoding="utf-8")
    assert "**LLM judge unavailable for 1 item(s)** (LLM_MODEL / LLM_API_KEY not set: 1)" in md


def test_report_line_absent_when_judge_ran():
    assert _judge_unavailable_line({"unavailable": {"items_not_judged": 0, "by_reason": {}}}) is None
    line = _judge_unavailable_line({"unavailable": {"items_not_judged": 7, "by_reason": {"skipped:judge_unavailable": 7}}})
    assert "unavailable for 7 item(s)" in line and "consecutive LLM failures" in line


def test_unfound_quote_text_is_dropped_and_only_its_length_kept():
    llm_text = TGT[:12] + "X"  # not in the translation: LLM-written text
    _, errors, _ = parse_judgment({"verdict": "errors", "errors": [
        {"category": "mistranslation", "severity": "major", "quote": llm_text, "explanation": "x", "confidence": 1.0}]}, TGT)
    e = errors[0]
    assert e["quote"] is None and e["quote_len"] == len(llm_text) and not e["quote_found"]
    fused = fuse([], errors, dict(DEFAULTS))
    ev = fused[0]["evidence"]
    assert ev["quote"] is None and ev["quote_len"] == len(llm_text) and ev["review_queue"] == "quote_not_found"
    assert not INDIC.search(json.dumps(fused, ensure_ascii=False))


def test_found_quote_is_kept_verbatim():
    q = TGT[TGT.index("केवल"):TGT.index("केवल") + len("केवल एक लेबल")]
    _, errors, _ = parse_judgment({"verdict": "errors", "errors": [
        {"category": "mistranslation", "severity": "major", "quote": q, "explanation": "x", "confidence": 0.9}]}, TGT)
    assert errors[0]["quote"] == q and errors[0]["quote_found"]


def test_normalized_quote_stores_original_translation_slice():
    q = TGT[TGT.index("केवल"):TGT.index("केवल") + len("केवल एक लेबल")]
    altered = q.replace(" ", "  ")  # extra whitespace -> matches via normalized tier, not exact
    _, errors, _ = parse_judgment({"verdict": "errors", "errors": [
        {"category": "mistranslation", "severity": "major", "quote": altered, "explanation": "x", "confidence": 0.9}]}, TGT)
    assert errors[0]["quote_found"] and errors[0]["quote"] == q and errors[0]["quote"] != altered


def test_label_caveat_names_author_reviewed_labels():
    text = _label_caveat({"injector": 72, "reviewed_preexisting": 5})
    assert "No human labels on seeded errors" in text and "5 `reviewed_preexisting` labels were added by the recipe author" in text
    assert "No human annotations were added" not in text


def test_label_caveat_without_any_human_review():
    assert "No human annotations were added" in _label_caveat({"injector": 10})
    assert "No human annotations" not in _label_caveat({"human": 3, "injector": 10})
    ext = _label_caveat({"unspecified": 12})
    assert "No human annotations" not in ext and "external dataset labels" in ext


def test_parse_labels_drops_only_the_bad_label():
    labels, errors = parse_labels([{"type": "number", "span": [9, 2]}, {"type": "omission", "span": [0, 3]}])
    assert [lb.category for lb in labels] == ["omission"] and len(errors) == 1
    labels, errors = parse_labels([{"type": "number", "span": [9, 2]}])
    assert labels is None and errors  # all bad: unknown, never "labelled clean"
    assert parse_labels([]) == ([], [])


def test_dataset_keeps_row_with_a_bad_label(tmp_path):
    ds = tmp_path / "d.jsonl"
    row = {"id": "a1", "source": "s", "lang": "hi", "labels": [{"type": "number", "span": [9, 2]}, {"type": "omission"}]}
    ds.write_text(json.dumps(row) + "\n", encoding="utf-8")
    res = load_dataset(ds, ["hi"])
    assert [it.id for it in res.items] == ["a1"] and not res.bad
    assert [lb.category for lb in res.items[0].labels] == ["omission"] and len(res.bad_labels) == 1


def test_calibration_file_drops_only_bad_labels(tmp_path):
    p = tmp_path / "cal.jsonl"
    p.write_text(json.dumps({"id": "a1", "labels": [{"type": "number", "span": [9, 2]}, {"type": "omission"}]}) + "\n"
                 + json.dumps({"id": "a2", "labels": [{"type": "number", "span": [9, 2]}]}) + "\n", encoding="utf-8")
    labels, info = load_labels(p)
    assert list(labels) == ["a1"] and info["bad_labels"] == 2 and info["bad_rows"] == 0


def test_unmatched_calibration_ids_are_reported(clean_env, tmp_path):
    ds = tmp_path / "d.jsonl"
    ds.write_text(json.dumps({"id": "a1", "source": "s", "lang": "hi"}) + "\n", encoding="utf-8")
    cal = tmp_path / "cal.jsonl"
    cal.write_text(json.dumps({"id": "a1", "labels": []}) + "\n" + json.dumps({"id": "zz-te-9", "labels": []}) + "\n", encoding="utf-8")
    out = tmp_path / "r"
    main(["--dataset", str(ds), "--langs", "hi", "--out", str(out), "--calibration", str(cal), "--cache-dir", str(tmp_path / "c"),
          "--env-file", str(clean_env)])
    d = json.loads((out / "summary.json").read_text(encoding="utf-8"))["dataset"]
    assert d["calibration_ids_not_in_run"] == 1 and d["calibration_ids_not_in_run_examples"] == ["zz-te-9"]


def _flag(check_id, category, span=(0, 5), weak=False):
    return Flag(check_id, category, "major", span, "chars", "m", {}, 0.9, needs_corroboration=weak).to_dict()


def test_weak_only_counts_labels_caught_only_by_weak_signals():
    items = [
        {"id": "a", "lang": "hi", "flags": [_flag("asr_tail", "speech_omission_truncation", weak=True)]},  # weak only
        {"id": "b", "lang": "hi", "flags": [_flag("asr_tail", "speech_omission_truncation")]},  # counted
        {"id": "c", "lang": "hi", "flags": []},  # missed
    ]
    lb = parse_labels([{"type": "truncation"}])[0]
    m = evaluate(items, {"a": lb, "b": lb, "c": lb}, dict(DEFAULTS), ["hi"])
    cat = m["by_category"]["speech_omission_truncation"]
    assert (cat["recall"]["k"], cat["weak_only"]["k"], cat["weak_only"]["n"]) == (1, 1, 3)


def test_counted_flag_agreement_compares_what_the_report_counts():
    span_q = TGT[:6]
    found = {"category": "mistranslation", "severity": "major", "quote": span_q, "quote_len": 6, "explanation": "x", "confidence": 0.9,
             "span": [0, 6], "quote_found": True, "match": "exact"}
    other = _flag("number_check", "mistranslation", span=(0, 6))
    rec_same = {"id": "s", "lang": "hi", "status": "ok", "flags": [other] + fuse([other], [found], dict(DEFAULTS))}
    rec_flip = {"id": "f", "lang": "hi", "status": "ok", "flags": [other] + fuse([other], [found], dict(DEFAULTS))}
    cons = {"overall": {}, "by_lang": {}, "_second_errors": {"s": [found], "f": []}}  # "f": re-judge said ok
    counted_flag_agreement(cons, [rec_same, rec_flip], dict(DEFAULTS))
    assert (cons["overall"]["same_counted_flags"]["k"], cons["overall"]["same_counted_flags"]["n"]) == (1, 2)
    assert "_second_errors" not in cons


def test_unavailable_count_excludes_items_the_judge_would_skip(clean_env, tmp_path):
    ds = tmp_path / "d.jsonl"
    rows = [{"id": "a1", "source": "So, it is likely.", "lang": "hi", "translation": TGT},
            {"id": "a2", "source": "Left in English by mistake here.", "lang": "hi", "translation": "Left in English by mistake here."}]
    ds.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    out = tmp_path / "report"
    main(["--dataset", str(ds), "--langs", "hi", "--out", str(out), "--cache-dir", str(tmp_path / "c"), "--env-file", str(clean_env)])
    judge = json.loads((out / "summary.json").read_text(encoding="utf-8"))["judge"]
    assert judge["unavailable"]["items_not_judged"] == 1  # a2 already fails a Tier-1 check; it is a skip, not "unavailable"
    assert judge["status"]["skipped:tier1_unambiguous"] == 1
